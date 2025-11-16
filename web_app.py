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

# Configuration
PROJECT_ID = "guns-and-gangs"
METADATA_DATASET_ID = "analytics_280581623"

# Security: Sensitive data patterns for masking
SENSITIVE_PATTERNS = [
    r'.*pii.*', r'.*personal.*', r'.*ssn.*', r'.*password.*',
    r'.*credit.*card.*', r'.*payment.*', r'.*secret.*', r'.*token.*',
    r'.*auth.*', r'.*credential.*'
]

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}

if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
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

logger = logging.getLogger(__name__)

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
    """Get sample data from table using TABLESAMPLE SYSTEM"""
    try:
        table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset_id}.{table_name}")
        if table_ref.table_type in ["VIEW", "MATERIALIZED_VIEW"]:
            query = f"""
            SELECT *
            FROM `{PROJECT_ID}.{dataset_id}.{table_name}`
            LIMIT {max_rows}
            """
        else:
            query = f"""
            SELECT *
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
                elif isinstance(value, str) and len(value) > 100:
                    row_dict[col] = value[:100] + "..."
                else:
                    row_dict[col] = value
            sample_data.append(row_dict)
        
        return sample_data
    except Exception as e:
        return None

def format_sample_data_for_prompt(sample_data, max_examples=3):
    """Format sample data for inclusion in AI prompt"""
    if not sample_data or len(sample_data) == 0:
        return None
    
    examples = sample_data[:max_examples]
    formatted = []
    for i, row in enumerate(examples, 1):
        row_str = ", ".join([f"{k}={v}" for k, v in row.items() if v is not None])
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
async def index(request: Request, user: dict = Depends(require_auth)):
    """Main page - list of all tables"""
    try:
        # Get user email from OAuth session
        user_email = user.get('email') if user else None
        
        # Get BigQuery client (uses user's OAuth credentials)
        bq_client = get_bq_client(request)
        
        # Get all tables with descriptions
        query = f"""
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
        ORDER BY t.dataset, t.table_name
        """
        
        df = bq_client.query(query).to_dataframe()
        tables = df.to_dict("records")
        
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
            "user_email": user_email
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"ERROR in index(): {e}")
        print(error_details)
        # Return error page instead of raising exception
        return templates.TemplateResponse("index.html", {
            "request": request,
            "datasets": [],
            "project_id": PROJECT_ID,
            "user_email": user_email,
            "error": str(e)
        })


@app.get("/table/{dataset}/{table_name}", response_class=HTMLResponse)
async def table_detail(request: Request, dataset: str, table_name: str, user: dict = Depends(require_auth)):
    """Table detail page with columns"""
    try:
        bq_client = get_bq_client(request)
        user_email = user.get('email') if user else None
        
        # Get table description
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
        
        if df_table.empty:
            raise HTTPException(status_code=404, detail="Table not found")
        
        table_info = df_table.iloc[0].to_dict()
        
        # Get columns
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
        
        bq_client = get_bq_client(request)
        df_columns = bq_client.query(query_columns, job_config=job_config).to_dataframe()
        columns = df_columns.to_dict("records")
        
        # Get user email (Cloud Run IAM or local dev)
        user_email = get_user_email(request)
        
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
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    try:
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
        if sample_data:
            formatted_samples = format_sample_data_for_prompt(sample_data, max_examples=3)
            if formatted_samples:
                sample_data_text = f"\n\nSample data (first 3 rows):\n{formatted_samples}"
        
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
        resp = openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=300
        )
        
        description = resp.choices[0].message.content.strip()
        
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
            "saved": True
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
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    try:
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
        
        # Get sample values
        sample_values_text = ""
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
                values = [str(v)[:50] for v in df_values[column_name].head(5).tolist() if v is not None]
                if values:
                    sample_values_text = f"\nSample values: {', '.join(values)}"
        except Exception:
            pass  # Continue without sample values
        
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
        resp = openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=200
        )
        
        description = resp.choices[0].message.content.strip()
        
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
            "saved": True
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)

