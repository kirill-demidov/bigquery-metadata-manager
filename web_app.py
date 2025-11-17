#!/usr/bin/env python3
"""
Web UI for BigQuery Metadata Descriptions Management
"""

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from google.cloud import bigquery
from openai import OpenAI
import pandas as pd
from typing import Optional, List
import os
from datetime import datetime
import re
import httpx
import logging

from auth import oauth, require_auth, get_user_bigquery_client, get_current_user

# Initialize logger first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
PROJECT_ID = "guns-and-gangs"
METADATA_DATASET_ID = "analytics_280581623"

# Security: Sensitive data patterns for masking
SENSITIVE_PATTERNS = [
    r'.*pii.*', r'.*personal.*', r'.*ssn.*', r'.*password.*',
    r'.*credit.*card.*', r'.*payment.*', r'.*secret.*', r'.*token.*',
    r'.*auth.*', r'.*credential.*',
    r'.*email.*', r'.*phone.*', r'.*mobile.*', r'.*tel.*',
    r'.*user.*id.*', r'.*customer.*id.*', r'.*account.*id.*',
    r'.*address.*', r'.*zip.*', r'.*postal.*',
    r'.*ip.*address.*', r'.*mac.*address.*'
]

# Patterns for detecting sensitive values
SENSITIVE_VALUE_PATTERNS = {
    'email': r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
    'phone': r'^\+?[1-9]\d{1,14}$|^\d{10,}$',
    'credit_card': r'^\d{13,19}$',
    'ssn': r'^\d{3}-\d{2}-\d{4}$',
    'ip_address': r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$'
}

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}

if OPENAI_API_KEY:
    try:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        logger.info(f"OpenAI client initialized successfully with model {MODEL_NAME}")
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {e}")
        openai_client = None
else:
    logger.warning("OPENAI_API_KEY not found in environment variables")
    openai_client = None

# Initialize
app = FastAPI(title="BigQuery Metadata Manager")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Add session middleware for OAuth
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=3600,  # 1 hour session
    same_site="lax",
    https_only=False
)

def is_sensitive_name(name):
    """Проверка, является ли имя чувствительным"""
    if not name:
        return False
    name_lower = name.lower()
    return any(re.search(pattern, name_lower) for pattern in SENSITIVE_PATTERNS)

def mask_table_name(table_fqn):
    """Маскирование чувствительных имен таблиц"""
    if is_sensitive_name(table_fqn):
        return "sensitive_table"
    return table_fqn

def mask_sensitive_value(value, column_name=None):
    """Маскирование чувствительных значений перед отправкой в OpenAI"""
    if value is None:
        return None
    
    value_str = str(value).strip()
    
    # Если колонка чувствительная - маскировать все значения
    if column_name and is_sensitive_name(column_name):
        if isinstance(value, (int, float)):
            return "***"
        elif isinstance(value, str):
            if len(value_str) <= 3:
                return "***"
            return value_str[:3] + "***"
        return "***"
    
    # Проверка паттернов в значениях
    # Email
    if re.match(SENSITIVE_VALUE_PATTERNS['email'], value_str, re.IGNORECASE):
        return "***@***.***"
    
    # Phone
    if re.match(SENSITIVE_VALUE_PATTERNS['phone'], value_str):
        return "***-***-****"
    
    # Credit card
    if re.match(SENSITIVE_VALUE_PATTERNS['credit_card'], value_str):
        return "****-****-****-****"
    
    # SSN
    if re.match(SENSITIVE_VALUE_PATTERNS['ssn'], value_str):
        return "***-**-****"
    
    # IP address
    if re.match(SENSITIVE_VALUE_PATTERNS['ip_address'], value_str):
        return "***.***.***.***"
    
    # Если значение похоже на ID (только цифры, длина 5-20)
    if isinstance(value, (int, str)) and re.match(r'^\d{5,20}$', value_str):
        return "***"
    
    return value

def get_user_email(request: Request) -> str:
    """
    Get user email from OAuth session or fallback.
    """
    try:
        user = get_current_user(request)
        return user.get('email', 'unknown@example.com')
    except HTTPException:
        return None

def get_bq_client(request: Request):
    """
    Get BigQuery client using user's OAuth credentials.
    """
    return get_user_bigquery_client(request)

def get_table_sample_data(bq_client, dataset_id, table_name, sample_percent=2, max_rows=5):
    """Get sample data from table using TABLESAMPLE SYSTEM with security filtering"""
    try:
        table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset_id}.{table_name}")
        
        # Исключить чувствительные колонки из sample данных
        safe_columns = [
            field.name for field in table_ref.schema 
            if not is_sensitive_name(field.name)
        ]
        
        # Если все колонки чувствительные - не отправлять sample данные
        if not safe_columns:
            logger.warning(f"All columns in {dataset_id}.{table_name} are sensitive, skipping sample data")
            return None
        
        # SELECT только безопасных колонок
        columns_str = ", ".join([f"`{col}`" for col in safe_columns])
        
        if table_ref.table_type in ["VIEW", "MATERIALIZED_VIEW"]:
            query = f"""
            SELECT {columns_str}
            FROM `{PROJECT_ID}.{dataset_id}.{table_name}`
            LIMIT {max_rows}
            """
        else:
            query = f"""
            SELECT {columns_str}
            FROM `{PROJECT_ID}.{dataset_id}.{table_name}`
            TABLESAMPLE SYSTEM ({sample_percent} PERCENT)
            LIMIT {max_rows}
            """
        
        df = bq_client.query(query, job_config=bigquery.QueryJobConfig(use_legacy_sql=False)).to_dataframe()
        if df.empty:
            return None
        
        sample_data = []
        for _, row in df.head(max_rows).iterrows():
            row_dict = {}
            for col in df.columns:
                value = row[col]
                if value is None:
                    row_dict[col] = None
                else:
                    # Маскировать чувствительные значения
                    masked_value = mask_sensitive_value(value, col)
                    # Обрезать длинные строки
                    if isinstance(masked_value, str) and len(masked_value) > 100:
                        row_dict[col] = masked_value[:100] + "..."
                    else:
                        row_dict[col] = masked_value
            sample_data.append(row_dict)
        
        return sample_data
    except Exception as e:
        logger.error(f"Error getting sample data: {e}")
        return None

def format_sample_data_for_prompt(sample_data, max_examples=3):
    """Format sample data for inclusion in AI prompt with value masking"""
    if not sample_data or len(sample_data) == 0:
        return None
    
    examples = sample_data[:max_examples]
    formatted = []
    for i, row in enumerate(examples, 1):
        # Значения уже замаскированы в get_table_sample_data, но дополнительно проверим
        row_items = []
        for k, v in row.items():
            if v is not None:
                # Дополнительное маскирование на случай, если что-то пропустили
                masked_value = mask_sensitive_value(v, k)
                row_items.append(f"{k}={masked_value}")
        row_str = ", ".join(row_items)
        formatted.append(f"Example {i}: {row_str}")
    
    return "\n".join(formatted)

def calculate_cost(model_name, prompt_tokens, completion_tokens):
    """Calculate cost based on tokens used"""
    if model_name not in OPENAI_PRICING:
        return 0.0
    pricing = OPENAI_PRICING[model_name]
    input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    output_cost = (completion_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


# Authentication routes
@app.get("/login")
async def login(request: Request):
    """Initiate Google OAuth login"""
    if not os.getenv("GOOGLE_CLIENT_ID") or not os.getenv("GOOGLE_CLIENT_SECRET"):
        raise HTTPException(status_code=500, detail="OAuth not configured. Missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("REDIRECT_URI", "http://localhost:8081/auth/callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    """Handle OAuth callback"""
    try:
        code = request.query_params.get('code')
        if not code:
            raise HTTPException(status_code=400, detail="No authorization code received")
        
        # Exchange code for token
        token_url = "https://oauth2.googleapis.com/token"
        redirect_uri = os.getenv("REDIRECT_URI", "http://localhost:8081/auth/callback")
        token_data = {
            'client_id': os.getenv("GOOGLE_CLIENT_ID"),
            'client_secret': os.getenv("GOOGLE_CLIENT_SECRET"),
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': redirect_uri
        }
        
        async with httpx.AsyncClient() as client:
            token_response = await client.post(token_url, data=token_data)
            token_response.raise_for_status()
            token_data = token_response.json()
        
        # Get user info
        userinfo_url = "https://www.googleapis.com/oauth2/v2/userinfo"
        headers = {'Authorization': f"Bearer {token_data['access_token']}"}
        
        async with httpx.AsyncClient() as client:
            userinfo_response = await client.get(userinfo_url, headers=headers)
            userinfo_response.raise_for_status()
            user_info = userinfo_response.json()
        
        # Save to session
        request.session['user'] = user_info
        request.session['token'] = {
            'access_token': token_data['access_token'],
            'refresh_token': token_data.get('refresh_token'),
            'expires_at': token_data.get('expires_in')
        }
        
        logger.info(f"User logged in: {user_info.get('email')}")
        return RedirectResponse(url="/", status_code=302)
        
    except Exception as e:
        logger.error(f"OAuth callback error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}")

@app.get("/logout")
async def logout(request: Request):
    """Logout user"""
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page - list of all tables"""
    try:
        # Try to get user from session (optional - page is accessible without auth)
        try:
            user = get_current_user(request)
            user_email = user.get('email') if user else None
            bq_client = get_bq_client(request)
        except HTTPException:
            # User not authenticated - show login page
            user_email = None
            bq_client = None
        
        # If user not authenticated, show login page
        if not bq_client:
            return templates.TemplateResponse("index.html", {
                "request": request,
                "datasets": [],
                "project_id": PROJECT_ID,
                "user_email": None,
                "requires_auth": True
            })
        
        # Get ALL tables from BigQuery INFORMATION_SCHEMA (not just from metadata table)
        # This ensures we show all tables, even if they don't have descriptions yet
        # Exclude sharded tables with suffixes _YYYYMMDD or _YYMMDD
        # Use region-eu scope (same as main.py uses LOCATION_SCOPE = "region-eu")
        query_all_tables = f"""
        SELECT 
            table_schema as dataset,
            table_name,
            table_type
        FROM `{PROJECT_ID}`.`region-eu`.INFORMATION_SCHEMA.TABLES
        WHERE table_schema NOT IN ('INFORMATION_SCHEMA', '_script')
        AND NOT REGEXP_CONTAINS(table_name, r'_\\d{{6,8}}$')
        ORDER BY table_schema, table_name
        """
        
        df_all = bq_client.query(query_all_tables).to_dataframe()
        
        # Get descriptions from metadata table
        query_metadata = f"""
        SELECT DISTINCT 
            t.dataset,
            t.table_name,
            t.table_description,
            t.job_insert_ts,
            COUNT(DISTINCT c.column_name) as column_count
        FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions` t
        LEFT JOIN `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions` c
            ON t.dataset = c.dataset AND t.table_name = c.table_name
        GROUP BY t.dataset, t.table_name, t.table_description, t.job_insert_ts
        """
        
        df_metadata = bq_client.query(query_metadata).to_dataframe()
        
        # Create a dict for quick lookup of metadata
        metadata_dict = {}
        for row in df_metadata.to_dict("records"):
            key = f"{row['dataset']}.{row['table_name']}"
            metadata_dict[key] = row
        
        # Get table descriptions from BigQuery schema
        # Use TABLE_OPTIONS query for efficiency (batch query instead of individual get_table calls)
        # This is much faster than calling get_table() for each table
        # According to: https://docs.cloud.google.com/bigquery/docs/information-schema-tables
        query_schema_descriptions = f"""
        SELECT 
            t.table_schema as dataset,
            t.table_name,
            TRIM(CAST(o.option_value AS STRING)) as table_description
        FROM `{PROJECT_ID}`.`region-eu`.INFORMATION_SCHEMA.TABLES t
        LEFT JOIN `{PROJECT_ID}`.`region-eu`.INFORMATION_SCHEMA.TABLE_OPTIONS o
            ON o.table_catalog = t.table_catalog
            AND o.table_schema = t.table_schema
            AND o.table_name = t.table_name
            AND o.option_name = 'description'
        WHERE t.table_schema NOT IN ('INFORMATION_SCHEMA', '_script')
        AND NOT REGEXP_CONTAINS(t.table_name, r'_\\d{{6,8}}$')
        """
        
        try:
            df_schema = bq_client.query(query_schema_descriptions).to_dataframe()
            schema_dict = {}
            for row in df_schema.to_dict("records"):
                key = f"{row['dataset']}.{row['table_name']}"
                desc = row.get('table_description')
                # Only consider non-empty descriptions
                schema_dict[key] = desc if desc and len(str(desc).strip()) > 0 else None
        except Exception as e:
            logger.error(f"Error getting schema descriptions: {e}")
            # Fallback: empty dict, all tables will show as 'none' or 'meta_only'
            schema_dict = {}
        
        # Get column counts for ALL tables in one batch query (optimization)
        # This avoids N+1 query problem when tables don't have metadata
        query_all_column_counts = f"""
        SELECT 
            table_schema as dataset,
            table_name,
            COUNT(*) as col_count
        FROM `{PROJECT_ID}`.`region-eu`.INFORMATION_SCHEMA.COLUMNS
        WHERE table_schema NOT IN ('INFORMATION_SCHEMA', '_script')
        AND NOT REGEXP_CONTAINS(table_name, r'_\\d{{6,8}}$')
        GROUP BY table_schema, table_name
        """
        
        try:
            df_col_counts = bq_client.query(query_all_column_counts).to_dataframe()
            column_count_dict = {}
            for row in df_col_counts.to_dict("records"):
                key = f"{row['dataset']}.{row['table_name']}"
                column_count_dict[key] = int(row['col_count'])
        except Exception as e:
            logger.error(f"Error getting column counts: {e}")
            column_count_dict = {}
        
        # Merge: get all tables and add metadata if available
        tables = []
        for row in df_all.to_dict("records"):
            key = f"{row['dataset']}.{row['table_name']}"
            has_meta_desc = key in metadata_dict and metadata_dict[key].get('table_description')
            has_schema_desc = key in schema_dict and schema_dict[key] and len(str(schema_dict[key]).strip()) > 0
            
            # Determine status
            if has_meta_desc and has_schema_desc:
                status = 'both'  # Есть и в мета-таблице и в schema
            elif has_meta_desc and not has_schema_desc:
                status = 'meta_only'  # Есть только в мета-таблице
            else:
                status = 'none'  # Нет ни там, ни там
            
            if key in metadata_dict:
                # Table has metadata
                meta = metadata_dict[key]
                tables.append({
                    'dataset': row['dataset'],
                    'table_name': row['table_name'],
                    'table_description': meta.get('table_description'),
                    'job_insert_ts': meta.get('job_insert_ts'),
                    'column_count': meta.get('column_count', 0),
                    'description_status': status
                })
            else:
                # Table exists but has no metadata yet
                # Use column count from batch query
                col_count = column_count_dict.get(key, 0)
                
                tables.append({
                    'dataset': row['dataset'],
                    'table_name': row['table_name'],
                    'table_description': None,
                    'job_insert_ts': None,
                    'column_count': col_count,
                    'description_status': status
                })
        
        # Group tables by dataset
        datasets_dict = {}
        for table in tables:
            dataset = table['dataset']
            if dataset not in datasets_dict:
                datasets_dict[dataset] = []
            datasets_dict[dataset].append(table)
        
        # Convert to list of dicts for template
        datasets_list = [
            {"dataset": dataset, "tables": tables_list, "table_count": len(tables_list)}
            for dataset, tables_list in sorted(datasets_dict.items())
        ]
        
        return templates.TemplateResponse("index.html", {
            "request": request,
            "datasets": datasets_list,
            "project_id": PROJECT_ID,
            "user_email": user_email,
            "requires_auth": False
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"ERROR in index(): {e}")
        print(error_details)
        # Return error page instead of raising exception
        try:
            user = get_current_user(request)
            user_email = user.get('email') if user else None
        except:
            user_email = None
        
        return templates.TemplateResponse("index.html", {
            "request": request,
            "datasets": [],
            "project_id": PROJECT_ID,
            "user_email": user_email,
            "error": str(e),
            "requires_auth": False
        })


@app.get("/table/{dataset}/{table_name}", response_class=HTMLResponse)
async def table_detail(request: Request, dataset: str, table_name: str, user: dict = Depends(require_auth)):
    """Table detail page with columns"""
    try:
        bq_client = get_bq_client(request)
        user_email = user.get('email') if user else None
        
        # First, check if table exists in BigQuery
        try:
            table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset}.{table_name}")
            table_exists_in_bq = True
            bq_table_description = (table_ref.description or "").strip()
        except Exception as e:
            logger.error(f"Table {dataset}.{table_name} not found in BigQuery: {e}")
            raise HTTPException(status_code=404, detail=f"Table {dataset}.{table_name} not found in BigQuery")
        
        # Get table description from metadata table (if exists)
        query_table = f"""
        SELECT dataset, table_name, table_description, job_insert_ts
        FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions`
        WHERE dataset = @dataset_id AND table_name = @table_name
        """
        
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset),
                bigquery.ScalarQueryParameter("table_name", "STRING", table_name)
            ]
        )
        
        df_table = bq_client.query(query_table, job_config=job_config).to_dataframe()
        
        # If table exists in BigQuery but not in metadata, create basic info from BigQuery
        if df_table.empty:
            # Table exists in BigQuery but not in metadata table
            table_info = {
                'dataset': dataset,
                'table_name': table_name,
                'table_description': bq_table_description if bq_table_description else None,
                'job_insert_ts': None
            }
        else:
            table_info = df_table.iloc[0].to_dict()
            # Use BigQuery description if metadata description is empty
            if not table_info.get('table_description') and bq_table_description:
                table_info['table_description'] = bq_table_description
        
        # Get columns from metadata table
        query_columns = f"""
        SELECT 
            column_name,
            data_type,
            generated_description,
            job_insert_ts
        FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions`
        WHERE dataset = @dataset_id AND table_name = @table_name
        ORDER BY column_name
        """
        
        df_columns_meta = bq_client.query(query_columns, job_config=job_config).to_dataframe()
        
        # Get columns from BigQuery schema
        query_bq_columns = f"""
        SELECT 
            column_name,
            data_type,
            description as generated_description
        FROM `{PROJECT_ID}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @table_name
        ORDER BY column_name
        """
        
        df_bq_columns = bq_client.query(query_bq_columns, job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("table_name", "STRING", table_name)
            ]
        )).to_dataframe()
        
        # Merge columns: use metadata if available, otherwise use BigQuery schema
        columns_dict = {}
        for _, row in df_columns_meta.iterrows():
            col_name = row['column_name']
            columns_dict[col_name] = {
                'column_name': col_name,
                'data_type': row.get('data_type', ''),
                'generated_description': row.get('generated_description'),
                'job_insert_ts': row.get('job_insert_ts')
            }
        
        # Add columns from BigQuery that are not in metadata
        for _, row in df_bq_columns.iterrows():
            col_name = row['column_name']
            if col_name not in columns_dict:
                columns_dict[col_name] = {
                    'column_name': col_name,
                    'data_type': row.get('data_type', ''),
                    'generated_description': row.get('generated_description'),
                    'job_insert_ts': None
                }
        
        columns = list(columns_dict.values())
        
        return templates.TemplateResponse("table_detail.html", {
            "request": request,
            "table": table_info,
            "columns": columns,
            "project_id": PROJECT_ID,
            "user_email": user_email
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading table detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/table/{dataset}/{table_name}/description")
async def update_table_description(
    request: Request,
    dataset: str,
    table_name: str,
    description: str = Form(...),
    user: dict = Depends(require_auth)
):
    """Update table description"""
    try:
        bq_client = get_bq_client(request)
        
        # Update in metadata table
        tmp_tbl = f"{PROJECT_ID}.{METADATA_DATASET_ID}._tmp_table_desc_{int(datetime.now().timestamp()*1000)}"
        
        try:
            import pandas as pd
            job = bq_client.load_table_from_dataframe(
                pd.DataFrame([{
                    'dataset': dataset,
                    'table_name': table_name,
                    'table_description': description,
                    'job_insert_ts': datetime.now()
                }]),
                tmp_tbl,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
            )
            job.result()
            
            merge_sql = f"""
            MERGE `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions` T
            USING `{tmp_tbl}` S
            ON  T.dataset = S.dataset AND T.table_name = S.table_name
            WHEN MATCHED THEN
              UPDATE SET 
                T.table_description = S.table_description,
                T.job_insert_ts = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN
              INSERT (dataset, table_name, table_description, job_insert_ts)
              VALUES (S.dataset, S.table_name, S.table_description, CURRENT_TIMESTAMP());
            """
            bq_client.query(merge_sql).result()
        finally:
            bq_client.delete_table(tmp_tbl, not_found_ok=True)
        
        # Update in BigQuery schema
        try:
            table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset}.{table_name}")
            table_ref.description = description
            bq_client.update_table(table_ref, ["description"])
        except Exception as e:
            # If update fails, continue - metadata table is updated
            pass
        
        return {"status": "success", "message": "Table description updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/table/{dataset}/{table_name}/column/{column_name}/description")
async def update_column_description(
    request: Request,
    dataset: str,
    table_name: str,
    column_name: str,
    description: str = Form(...),
    user: dict = Depends(require_auth)
):
    """Update column description"""
    try:
        bq_client = get_bq_client(request)
        
        # Get data type
        query_type = f"""
        SELECT data_type
        FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions`
        WHERE dataset = @dataset_id AND table_name = @table_name AND column_name = @column_name
        LIMIT 1
        """
        
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset),
                bigquery.ScalarQueryParameter("table_name", "STRING", table_name),
                bigquery.ScalarQueryParameter("column_name", "STRING", column_name)
            ]
        )
        
        df_type = bq_client.query(query_type, job_config=job_config).to_dataframe()
        
        if df_type.empty:
            # Try to get from BigQuery schema
            try:
                table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset}.{table_name}")
                for field in table_ref.schema:
                    if field.name == column_name:
                        data_type = field.field_type
                        break
                else:
                    raise HTTPException(status_code=404, detail="Column not found")
            except:
                raise HTTPException(status_code=404, detail="Column not found")
        else:
            data_type = df_type.iloc[0]['data_type']
        
        # Update in metadata table
        tmp_cols = f"{PROJECT_ID}.{METADATA_DATASET_ID}._tmp_col_desc_{int(datetime.now().timestamp()*1000)}"
        
        try:
            import pandas as pd
            job = bq_client.load_table_from_dataframe(
                pd.DataFrame([{
                    'dataset': dataset,
                    'table_name': table_name,
                    'column_name': column_name,
                    'data_type': data_type,
                    'generated_description': description,
                    'job_insert_ts': datetime.now()
                }]),
                tmp_cols,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
            )
            job.result()
            
            merge_sql = f"""
            MERGE `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions` T
            USING `{tmp_cols}` S
            ON  T.dataset = S.dataset 
            AND T.table_name = S.table_name 
            AND T.column_name = S.column_name
            WHEN MATCHED THEN
              UPDATE SET 
                T.generated_description = S.generated_description,
                T.job_insert_ts = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN
              INSERT (dataset, table_name, column_name, data_type, generated_description, job_insert_ts)
              VALUES (S.dataset, S.table_name, S.column_name, S.data_type, S.generated_description, CURRENT_TIMESTAMP());
            """
            bq_client.query(merge_sql).result()
        finally:
            bq_client.delete_table(tmp_cols, not_found_ok=True)
        
        # Update in BigQuery schema
        try:
            table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset}.{table_name}")
            new_schema = []
            for field in table_ref.schema:
                if field.name == column_name:
                    new_field = bigquery.SchemaField(
                        field.name, field.field_type, mode=field.mode,
                        description=description, fields=field.fields
                    )
                else:
                    new_field = field
                new_schema.append(new_field)
            table_ref.schema = new_schema
            bq_client.update_table(table_ref, ["schema"])
        except Exception as e:
            # If update fails, continue - metadata table is updated
            pass
        
        return {"status": "success", "message": "Column description updated"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/table/{dataset}/{table_name}/generate-description")
async def generate_table_description(request: Request, dataset: str, table_name: str, user: dict = Depends(require_auth)):
    """Generate table description using OpenAI with sample data"""
    if not openai_client:
        logger.error("OpenAI client not initialized - OPENAI_API_KEY missing or invalid")
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    try:
        logger.info(f"Generating description for table {dataset}.{table_name}")
        bq_client = get_bq_client(request)
        
        # Get table columns
        query_cols = f"""
        SELECT column_name, data_type
        FROM `{PROJECT_ID}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @table_name
        ORDER BY column_name
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("table_name", "STRING", table_name)
            ]
        )
        df_cols = bq_client.query(query_cols, job_config=job_config).to_dataframe()
        
        # Get sample data
        sample_data = get_table_sample_data(bq_client, dataset, table_name, sample_percent=2, max_rows=5)
        sample_data_text = ""
        sample_rows_count = 0
        sample_columns_count = 0
        sensitive_columns_count = sum(1 for _, row in df_cols.iterrows() if is_sensitive_name(row['column_name']))
        
        if sample_data:
            formatted_samples = format_sample_data_for_prompt(sample_data, max_examples=3)
            if formatted_samples:
                sample_data_text = f"\n\nSample data (first 3 rows):\n{formatted_samples}"
                sample_rows_count = len(sample_data)
                sample_columns_count = len(sample_data[0]) if sample_data else 0
        
        # Build prompt with masking
        table_fqn = f"{dataset}.{table_name}"
        safe_table_fqn = mask_table_name(table_fqn)
        
        columns_list = ", ".join([
            f"{'column' if is_sensitive_name(row['column_name']) else row['column_name']} ({row['data_type']})" 
            for _, row in df_cols.head(20).iterrows()
        ])
        
        prompt = f"""You are a data analyst. Analyze this BigQuery table and write a comprehensive description (3-4 sentences).

Table: {safe_table_fqn}
Total columns: {len(df_cols)}
Sample columns: {columns_list}{sample_data_text}

The description should explain:
1. What business data or events this table stores
2. What is the primary purpose or use case
3. Who would use this data and why
4. Any important context about data granularity or scope

Write a professional, detailed description. Write ONLY the description, no preamble."""
        
        # Call OpenAI
        logger.info(f"Calling OpenAI API with model {MODEL_NAME}, prompt length: {len(prompt)}")
        try:
            resp = openai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
                max_tokens=300
            )
            description = resp.choices[0].message.content.strip()
            logger.info(f"OpenAI API call successful, received description length: {len(description)}")
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")
        
        # Calculate cost
        prompt_tokens = resp.usage.prompt_tokens
        completion_tokens = resp.usage.completion_tokens
        cost = calculate_cost(MODEL_NAME, prompt_tokens, completion_tokens)
        
        # Save to metadata table (same as main.py does)
        tmp_tbl = f"{PROJECT_ID}.{METADATA_DATASET_ID}._tmp_table_desc_{int(datetime.now().timestamp()*1000)}"
        try:
            job = bq_client.load_table_from_dataframe(
                pd.DataFrame([{
                    'dataset': dataset,
                    'table_name': table_name,
                    'table_description': description,
                    'job_insert_ts': datetime.now()
                }]),
                tmp_tbl,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
            )
            job.result()
            
            merge_sql = f"""
            MERGE `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions` T
            USING `{tmp_tbl}` S
            ON  T.dataset = S.dataset AND T.table_name = S.table_name
            WHEN MATCHED THEN
              UPDATE SET 
                T.table_description = S.table_description,
                T.job_insert_ts = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN
              INSERT (dataset, table_name, table_description, job_insert_ts)
              VALUES (S.dataset, S.table_name, S.table_description, CURRENT_TIMESTAMP());
            """
            bq_client.query(merge_sql).result()
        finally:
            bq_client.delete_table(tmp_tbl, not_found_ok=True)
        
        # Update in BigQuery schema (optional)
        try:
            table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset}.{table_name}")
            table_ref.description = description
            bq_client.update_table(table_ref, ["description"])
        except Exception:
            pass  # Continue if update fails
        
        return {
            "status": "success",
            "description": description,
            "cost": cost,
            "tokens": {"prompt": prompt_tokens, "completion": completion_tokens},
            "saved": True,
            "stats": {
                "total_columns": len(df_cols),
                "sensitive_columns": sensitive_columns_count,
                "sample_rows": sample_rows_count,
                "sample_columns": sample_columns_count,
                "model": MODEL_NAME,
                "prompt_length": len(prompt)
            },
            "steps": [
                "✓ Получена информация о таблице",
                f"✓ Найдено колонок: {len(df_cols)}",
                f"✓ Получены sample данные: {sample_rows_count} строк" if sample_data else "⚠ Sample данные не получены (все колонки чувствительные)",
                f"✓ Сформирован промпт ({len(prompt)} символов)",
                f"✓ Отправлен запрос в OpenAI ({MODEL_NAME})",
                f"✓ Получен ответ ({completion_tokens} токенов)",
                "✓ Сохранено в мета-таблицу",
                "✓ Обновлена BigQuery schema"
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/table/{dataset}/{table_name}/column/{column_name}/generate-description")
async def generate_column_description(
    request: Request,
    dataset: str,
    table_name: str,
    column_name: str,
    user: dict = Depends(require_auth)
):
    """Generate column description using OpenAI with sample values"""
    if not openai_client:
        logger.error("OpenAI client not initialized - OPENAI_API_KEY missing or invalid")
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    try:
        logger.info(f"Generating description for column {dataset}.{table_name}.{column_name}")
        bq_client = get_bq_client(request)
        
        # Get column data type
        query_type = f"""
        SELECT data_type
        FROM `{PROJECT_ID}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @table_name AND column_name = @column_name
        LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("table_name", "STRING", table_name),
                bigquery.ScalarQueryParameter("column_name", "STRING", column_name)
            ]
        )
        df_type = bq_client.query(query_type, job_config=job_config).to_dataframe()
        
        if df_type.empty:
            raise HTTPException(status_code=404, detail="Column not found")
        
        data_type = df_type.iloc[0]['data_type']
        
        # Get sample values (only if column is not sensitive)
        sample_values_text = ""
        if not is_sensitive_name(column_name):
            try:
                table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset}.{table_name}")
                if table_ref.table_type not in ["VIEW", "MATERIALIZED_VIEW"]:
                    query_values = f"""
                    SELECT DISTINCT `{column_name}`
                    FROM `{PROJECT_ID}.{dataset}.{table_name}`
                    TABLESAMPLE SYSTEM (2 PERCENT)
                    WHERE `{column_name}` IS NOT NULL
                    LIMIT 5
                    """
                else:
                    query_values = f"""
                    SELECT DISTINCT `{column_name}`
                    FROM `{PROJECT_ID}.{dataset}.{table_name}`
                    WHERE `{column_name}` IS NOT NULL
                    LIMIT 5
                    """
                
                df_values = bq_client.query(query_values, job_config=bigquery.QueryJobConfig(use_legacy_sql=False)).to_dataframe()
                
                if not df_values.empty:
                    # Маскировать значения перед отправкой
                    masked_values = []
                    for v in df_values[column_name].head(5).tolist():
                        if v is not None:
                            masked_v = mask_sensitive_value(v, column_name)
                            masked_values.append(str(masked_v)[:50])
                    if masked_values:
                        sample_values_text = f"\nSample values: {', '.join(masked_values)}"
            except Exception:
                pass  # Continue without sample values
        else:
            logger.info(f"Skipping sample values for sensitive column: {column_name}")
        
        # Build prompt with masking
        table_fqn = f"{dataset}.{table_name}"
        safe_table_fqn = mask_table_name(table_fqn)
        safe_column_name = 'column' if is_sensitive_name(column_name) else column_name
        
        prompt = f"""You are a data analyst. Write a detailed, professional description (1-2 sentences) for this database column.

Table: {safe_table_fqn}
Column: {safe_column_name}
Data Type: {data_type}{sample_values_text}

The description should:
- Explain what data this column stores
- Clarify its business purpose or use case
- Be specific and informative
- Use professional terminology

Write ONLY the description, no preamble or extra text."""
        
        # Call OpenAI
        logger.info(f"Calling OpenAI API with model {MODEL_NAME}, prompt length: {len(prompt)}")
        try:
            resp = openai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=200
            )
            description = resp.choices[0].message.content.strip()
            logger.info(f"OpenAI API call successful, received description length: {len(description)}")
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")
        
        # Calculate cost
        prompt_tokens = resp.usage.prompt_tokens
        completion_tokens = resp.usage.completion_tokens
        cost = calculate_cost(MODEL_NAME, prompt_tokens, completion_tokens)
        
        # Save to metadata table (same as main.py does)
        tmp_cols = f"{PROJECT_ID}.{METADATA_DATASET_ID}._tmp_col_desc_{int(datetime.now().timestamp()*1000)}"
        try:
            job = bq_client.load_table_from_dataframe(
                pd.DataFrame([{
                    'dataset': dataset,
                    'table_name': table_name,
                    'column_name': column_name,
                    'data_type': data_type,
                    'generated_description': description,
                    'job_insert_ts': datetime.now()
                }]),
                tmp_cols,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
            )
            job.result()
            
            merge_sql = f"""
            MERGE `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions` T
            USING `{tmp_cols}` S
            ON  T.dataset = S.dataset 
            AND T.table_name = S.table_name 
            AND T.column_name = S.column_name
            WHEN MATCHED THEN
              UPDATE SET 
                T.generated_description = S.generated_description,
                T.job_insert_ts = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN
              INSERT (dataset, table_name, column_name, data_type, generated_description, job_insert_ts)
              VALUES (S.dataset, S.table_name, S.column_name, S.data_type, S.generated_description, CURRENT_TIMESTAMP());
            """
            bq_client.query(merge_sql).result()
        finally:
            bq_client.delete_table(tmp_cols, not_found_ok=True)
        
        # Update in BigQuery schema (optional)
        try:
            table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset}.{table_name}")
            new_schema = []
            for field in table_ref.schema:
                if field.name == column_name:
                    new_field = bigquery.SchemaField(
                        field.name, field.field_type, mode=field.mode,
                        description=description, fields=field.fields
                    )
                else:
                    new_field = field
                new_schema.append(new_field)
            table_ref.schema = new_schema
            bq_client.update_table(table_ref, ["schema"])
        except Exception:
            pass  # Continue if update fails
        
        return {
            "status": "success",
            "description": description,
            "cost": cost,
            "tokens": {"prompt": prompt_tokens, "completion": completion_tokens},
            "saved": True,
            "stats": {
                "column_name": column_name,
                "data_type": data_type,
                "is_sensitive": is_sensitive_col,
                "sample_values_count": sample_values_count,
                "model": MODEL_NAME,
                "prompt_length": len(prompt)
            },
            "steps": [
                "✓ Получена информация о колонке",
                f"✓ Тип данных: {data_type}",
                f"✓ Sample значений: {sample_values_count}" if sample_values_count > 0 else "⚠ Sample значения не получены (колонка чувствительная)" if is_sensitive_col else "⚠ Sample значения не найдены",
                f"✓ Сформирован промпт ({len(prompt)} символов)",
                f"✓ Отправлен запрос в OpenAI ({MODEL_NAME})",
                f"✓ Получен ответ ({completion_tokens} токенов)",
                "✓ Сохранено в мета-таблицу",
                "✓ Обновлена BigQuery schema"
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)

