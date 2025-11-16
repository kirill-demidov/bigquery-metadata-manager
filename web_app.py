#!/usr/bin/env python3
"""
Web UI for BigQuery Metadata Descriptions Management
"""

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google.cloud import bigquery
from typing import Optional, List
import os
from datetime import datetime

# Configuration
PROJECT_ID = "guns-and-gangs"
METADATA_DATASET_ID = "analytics_280581623"

# Initialize
app = FastAPI(title="BigQuery Metadata Manager")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
bq_client = bigquery.Client(project=PROJECT_ID)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page - list of all tables"""
    try:
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
        
        return templates.TemplateResponse("index.html", {
            "request": request,
            "tables": tables,
            "project_id": PROJECT_ID
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/table/{dataset}/{table_name}", response_class=HTMLResponse)
async def table_detail(request: Request, dataset: str, table_name: str):
    """Table detail page with columns"""
    try:
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
        
        df_columns = bq_client.query(query_columns, job_config=job_config).to_dataframe()
        columns = df_columns.to_dict("records")
        
        return templates.TemplateResponse("table_detail.html", {
            "request": request,
            "table": table_info,
            "columns": columns,
            "project_id": PROJECT_ID
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/table/{dataset}/{table_name}/description")
async def update_table_description(
    dataset: str,
    table_name: str,
    description: str = Form(...)
):
    """Update table description"""
    try:
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
    dataset: str,
    table_name: str,
    column_name: str,
    description: str = Form(...)
):
    """Update column description"""
    try:
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)

