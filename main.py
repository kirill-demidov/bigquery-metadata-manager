#!/usr/bin/env python3
"""
BigQuery Metadata Descriptions Generator with OpenAI (project-wide, region-eu)

Что делает:
1) Ищет ВСЕ таблицы/вью без описания во всём проекте guns-and-gangs в region-eu
   (исключая шарды _YYYYMMDD / _YYMMDD).
2) Для каждой таблицы сверяет фактические описания в BigQuery и записи в мета-таблицах.
   - Если в мета всё есть, а в BQ утеряно — восстанавливает в BQ из мета (без OpenAI).
   - Если в BQ всё есть, а в мета нет — дозаполняет мета из BQ (без OpenAI).
   - OpenAI вызывается только для тех таблиц/колонок, где описания нет ни в мета, ни в BQ.
3) Сохраняет в мета-таблицы через MERGE:
   - UPDATE только при изменении текста (и тогда обновляет job_insert_ts)
   - INSERT с job_insert_ts = CURRENT_TIMESTAMP() для новых строк
4) (Опционально) Обновляет описания в самой BigQuery-схеме (таблица + колонки).

Требуется наличие таблиц:
  guns-and-gangs.analytics_280581623.table_descriptions(dataset, table_name, table_description, job_insert_ts TIMESTAMP)
  guns-and-gangs.analytics_280581623.column_descriptions(dataset, table_name, column_name, data_type, generated_description, job_insert_ts TIMESTAMP)
"""

from openai import OpenAI
from google.cloud import bigquery
import pandas as pd
import time
from datetime import datetime
import json
from threading import Lock
import re
import os
import logging

# ============================================================================
# SECURITY CONFIGURATION
# ============================================================================

# Паттерны для определения чувствительных таблиц/колонок
SENSITIVE_PATTERNS = [
    r'.*pii.*', r'.*personal.*', r'.*ssn.*', r'.*password.*',
    r'.*credit.*card.*', r'.*payment.*', r'.*secret.*', r'.*token.*',
    r'.*auth.*', r'.*credential.*'
]

# Whitelist/Blacklist датасетов (опционально)
ALLOWED_DATASETS = None  # None = все разрешены, или список: ["analytics", "staging"]
BLOCKED_DATASETS = []    # Список заблокированных датасетов: ["pii", "sensitive"]

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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

def validate_table_name(table_name):
    """Валидация имени таблицы BigQuery"""
    if not table_name or len(table_name) > 1024:
        raise ValueError(f"Invalid table name length: {table_name}")
    # BigQuery имена могут содержать только буквы, цифры, подчеркивания
    if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
        raise ValueError(f"Invalid characters in table name: {table_name}")
    return table_name

def validate_dataset_name(dataset_id):
    """Валидация имени датасета BigQuery"""
    if not dataset_id or len(dataset_id) > 1024:
        raise ValueError(f"Invalid dataset name length: {dataset_id}")
    if not re.match(r'^[a-zA-Z0-9_]+$', dataset_id):
        raise ValueError(f"Invalid characters in dataset name: {dataset_id}")
    return dataset_id

def should_process_table(dataset_id, table_name):
    """Проверка, должна ли таблица обрабатываться"""
    # Валидация имен
    try:
        validate_dataset_name(dataset_id)
        validate_table_name(table_name)
    except ValueError as e:
        logger.warning(f"Skipping invalid table {dataset_id}.{table_name}: {e}")
        return False
    
    # Проверка blacklist
    if dataset_id in BLOCKED_DATASETS:
        logger.info(f"Skipping blocked dataset: {dataset_id}")
        return False
    
    # Проверка whitelist
    if ALLOWED_DATASETS is not None and dataset_id not in ALLOWED_DATASETS:
        logger.info(f"Skipping dataset not in whitelist: {dataset_id}")
        return False
    
    # Проверка чувствительности
    table_fqn = f"{dataset_id}.{table_name}"
    if is_sensitive_name(table_fqn):
        logger.warning(f"Skipping sensitive table: {mask_table_name(table_fqn)}")
        return False
    
    return True

# ============================================================================
# CONFIGURATION
# ============================================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is required")
PROJECT_ID = "guns-and-gangs"
LOCATION_SCOPE = "region-eu"                  # region-us / region-europe-west1 и т.д.
METADATA_DATASET_ID = "analytics_280581623"   # мета-таблицы уже существуют
TABLE_NAME = None  # Опционально: "<dataset>.<table>" чтобы принудительно обработать конкретную таблицу

# Поведение синхронизации:
PREFER_META_OVER_BQ = True   # если True, при расхождениях восстановим BQ из мета (без OpenAI)

# OpenAI settings
MODEL_NAME = "gpt-4o-mini"  # или "gpt-4o" для лучшего качества
OPENAI_TEMPERATURE = 0.5
OPENAI_MAX_TOKENS = 200

# Performance settings
REQUEST_DELAY = 0.05
MAX_RETRIES = 3
BATCH_SIZE = 5
UPDATE_BIGQUERY_METADATA = True

# ============================================================================
# INITIALIZATION
# ============================================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)
bq_client = bigquery.Client(project=PROJECT_ID)

api_lock = Lock()
last_request_time = [0]

# ============================================================================
# HELPERS
# ============================================================================

def rate_limited_api_call(func, *args, **kwargs):
    with api_lock:
        current_time = time.time()
        time_since_last = current_time - last_request_time[0]
        if time_since_last < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - time_since_last)
        result = func(*args, **kwargs)
        last_request_time[0] = time.time()
        return result

def get_table_type(project_id, dataset_id, table_name):
    try:
        table_ref = bq_client.get_table(f"{project_id}.{dataset_id}.{table_name}")
        return table_ref.table_type
    except Exception as e:
        print(f"\n  ⚠ Error getting table type: {e}")
        return "UNKNOWN"

def generate_column_description_ai(column_name, data_type, table_fqn, max_retries=MAX_RETRIES):
    # Маскирование чувствительных данных перед отправкой в OpenAI
    safe_table_fqn = mask_table_name(table_fqn)
    safe_column_name = "column" if is_sensitive_name(column_name) else column_name
    
    prompt = f"""You are a data analyst. Write a detailed, professional description (1-2 sentences) for this database column.

Table: {safe_table_fqn}
Column: {safe_column_name}
Data Type: {data_type}

The description should:
- Explain what data this column stores
- Clarify its business purpose or use case
- Be specific and informative
- Use professional terminology

Write ONLY the description, no preamble or extra text."""
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            def make_openai_call():
                resp = openai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=OPENAI_TEMPERATURE,
                    max_tokens=OPENAI_MAX_TOKENS
                )
                return resp.choices[0].message.content
            description = rate_limited_api_call(make_openai_call)
            elapsed = time.time() - start_time
            if description and len(description.strip()) > 20:
                print(f" ({elapsed:.1f}s)", end='', flush=True)
                return description.strip()
        except Exception as e:
            error_str = str(e).lower()
            if attempt < max_retries - 1:
                if "quota" in error_str or "insufficient_quota" in error_str:
                    wait_time = 30 * (attempt + 1)
                    print(f"\n    ⚠ API quota exhausted, waiting {wait_time}s...")
                    time.sleep(wait_time)
                elif "429" in error_str or "rate" in error_str:
                    wait_time = 5 * (attempt + 1)
                    print(f"\n    ⚠ Rate limit, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"\n    ⚠ Error: {e}, retry {attempt+1}/{max_retries}...")
                    time.sleep(2 * (attempt + 1))
            else:
                print(f"\n    ✗ Failed after {max_retries} attempts: {e}")
                return f"Column storing {column_name.replace('_', ' ')} ({data_type})"
    return f"Data field for {column_name.replace('_', ' ')}"

def generate_batch_column_descriptions(columns_batch, table_fqn, max_retries=MAX_RETRIES):
    # Маскирование чувствительных данных перед отправкой в OpenAI
    safe_table_fqn = mask_table_name(table_fqn)
    safe_columns_info = "\n".join([
        f"- {'column' if is_sensitive_name(c['column_name']) else c['column_name']} ({c['data_type']})" 
        for c in columns_batch
    ])
    prompt = f"""You are a data analyst. Write detailed, professional descriptions for these database columns.

Table: {safe_table_fqn}
Columns:
{safe_columns_info}

For EACH column, write a description (1-2 sentences) that:
- Explains what data this column stores
- Clarifies its business purpose
- Is specific and informative

CRITICAL: Format your response as a valid JSON object with a "columns" array.
Use this EXACT format:
{{
"columns": [
  {{"column": "column_name", "description": "Description text"}},
  {{"column": "column_name2", "description": "Description text"}}
]
}}

Write ONLY valid JSON."""
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            def make_openai_call():
                resp = openai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": "You are a data analyst. Always return valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=800,
                    response_format={"type": "json_object"}
                )
                return resp.choices[0].message.content
            result_text = rate_limited_api_call(make_openai_call)
            elapsed = time.time() - start_time
            result_json = json.loads(result_text)
            if isinstance(result_json, dict) and 'columns' in result_json:
                descriptions_json = result_json['columns']
            elif isinstance(result_json, list):
                descriptions_json = result_json
            else:
                raise ValueError("Unexpected JSON format")
            descriptions_dict = {item['column']: item['description'] for item in descriptions_json}
            print(f"✓ ({elapsed:.1f}s, {len(descriptions_dict)} descriptions)", flush=True)
            return descriptions_dict
        except Exception as e:
            error_str = str(e).lower()
            if attempt < max_retries - 1:
                if "quota" in error_str or "insufficient_quota" in error_str:
                    wait_time = 30 * (attempt + 1)
                    print(f"\n    ⚠ API quota, waiting {wait_time}s...")
                    time.sleep(wait_time)
                elif "429" in error_str or "rate" in error_str:
                    wait_time = 5 * (attempt + 1)
                    print(f"\n    ⚠ Rate limit, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"\n    ⚠ Batch error: {e}, retry {attempt+1}/{max_retries}...")
                    time.sleep(2 * (attempt + 1))
    print(f"⚠ failed, fallback to individual", flush=True)
    return None

def generate_table_description_ai(table_fqn, columns_df, max_retries=MAX_RETRIES):
    # Маскирование чувствительных данных перед отправкой в OpenAI
    safe_table_fqn = mask_table_name(table_fqn)
    sample_columns = columns_df.head(20)
    columns_with_types = [
        f"{'column' if is_sensitive_name(row['column_name']) else row['column_name']} ({row['data_type']})" 
        for _, row in sample_columns.iterrows()
    ]
    columns_list = ", ".join(columns_with_types)
    prompt = f"""You are a data analyst. Analyze this BigQuery table and write a comprehensive description (3-4 sentences).

Table: {safe_table_fqn}
Total columns: {len(columns_df)}
Sample columns: {columns_list}

The description should explain:
1. What business data or events this table stores
2. What is the primary purpose or use case
3. Who would use this data and why
4. Any important context about data granularity or scope

Write a professional, detailed description. Write ONLY the description, no preamble."""
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            def make_openai_call():
                resp = openai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.6,
                    max_tokens=300
                )
                return resp.choices[0].message.content
            description = rate_limited_api_call(make_openai_call)
            elapsed = time.time() - start_time
            if description and len(description.strip()) > 50:
                print(f"✓ ({elapsed:.1f}s)", flush=True)
                return description.strip()
        except Exception as e:
            error_str = str(e).lower()
            if attempt < max_retries - 1:
                if "quota" in error_str or "insufficient_quota" in error_str:
                    wait_time = 30 * (attempt + 1)
                    print(f"\n  ⚠ API quota, waiting {wait_time}s...")
                    time.sleep(wait_time)
                elif "429" in error_str or "rate" in error_str:
                    wait_time = 5 * (attempt + 1)
                    print(f"\n  ⚠ Rate limit, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"\n  ⚠ Error: {e}, retry {attempt+1}/{max_retries}...")
                    time.sleep(2 * (attempt + 1))
    print(f"✗ Failed, using fallback", flush=True)
    return f"Table storing {table_fqn} data with {len(columns_df)} columns"

# ============================================================================
# MAIN
# ============================================================================

def main():
    print(f"\n{'='*80}")
    print(f"METADATA GENERATION JOB - project-wide (location scope)")
    print(f"{'='*80}")
    print(f"Project: {PROJECT_ID}")
    print(f"Location scope: {LOCATION_SCOPE}")
    print(f"Metadata Dataset: {METADATA_DATASET_ID}")
    print(f"Model: {MODEL_NAME}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}\n")

    # Test OpenAI
    print("Testing OpenAI API...", end=' ', flush=True)
    try:
        test_response = openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": "Say 'API Ready'"}],
            max_tokens=10
        )
        print(f"✓ {test_response.choices[0].message.content.strip()}\n")
    except Exception as e:
        print(f"✗ FAILED: {str(e)}")
        return

    # 1) Найти все таблицы/вью без описания во всём проекте в данной локации
    tables_to_process = []
    if TABLE_NAME:
        ds, tb = TABLE_NAME.split(".", 1)
        tables_to_process = [{"dataset": ds, "table_name": tb, "table_type": "UNKNOWN"}]
        print(f"✓ Forced single table: {TABLE_NAME}\n")
    else:
        sql_find = f"""
        SELECT
          t.table_schema   AS dataset,
          t.table_name,
          t.table_type
        FROM `{PROJECT_ID}`.`{LOCATION_SCOPE}`.INFORMATION_SCHEMA.TABLES AS t
        LEFT JOIN `{PROJECT_ID}`.`{LOCATION_SCOPE}`.INFORMATION_SCHEMA.TABLE_OPTIONS AS o
          ON  o.table_catalog = t.table_catalog
          AND o.table_schema  = t.table_schema
          AND o.table_name    = t.table_name
          AND o.option_name   = 'description'
        WHERE (o.option_value IS NULL OR TRIM(CAST(o.option_value AS STRING)) = '')
          AND NOT REGEXP_CONTAINS(t.table_name, r'_(\\d{{6}}|\\d{{8}})$')
        ORDER BY dataset, table_name
        """
        df_missing = bq_client.query(sql_find).to_dataframe()
        tables_to_process = df_missing.to_dict("records")
        print(f"✓ Found {len(tables_to_process)} tables/views without description in {LOCATION_SCOPE}\n")

    total_tables_processed = 0
    total_tables_skipped   = 0
    total_columns_generated = 0
    total_columns_skipped   = 0
    total_materialized_views = 0

    t0 = time.time()

    # 2) Обработка каждой таблицы
    for idx, row in enumerate(tables_to_process, 1):
        dataset_id    = row["dataset"]
        current_table = row["table_name"]
        table_fqn     = f"{dataset_id}.{current_table}"

        # Проверка безопасности перед обработкой
        if not should_process_table(dataset_id, current_table):
            logger.info(f"Skipping table {table_fqn} due to security policy")
            total_tables_skipped += 1
            continue

        # Безопасное логирование (маскирование чувствительных имен)
        safe_table_name = mask_table_name(table_fqn) if is_sensitive_name(table_fqn) else table_fqn
        print(f"\n{'='*80}")
        print(f"[{idx}/{len(tables_to_process)}] TABLE: {safe_table_name}")
        print(f"Time: {datetime.now().strftime('%H:%M:%S')} | Elapsed: {(time.time()-t0)/60:.1f} min")
        print(f"{'='*80}")

        # Тип объекта
        table_type = get_table_type(PROJECT_ID, dataset_id, current_table)
        print(f"Table type: {table_type}")
        is_materialized_view = (table_type == "MATERIALIZED_VIEW")
        if is_materialized_view:
            total_materialized_views += 1

        # Колонки конкретной таблицы (per dataset) - используем параметризованный запрос
        q_cols = f"""
        SELECT table_name, column_name, data_type
        FROM `{PROJECT_ID}.{dataset_id}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @table_name
        ORDER BY column_name
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("table_name", "STRING", current_table)
            ]
        )
        df_cols = bq_client.query(q_cols, job_config=job_config).to_dataframe()
        print(f"Total columns in table: {len(df_cols)}")

        # Метаданные из мета-таблиц - используем параметризованный запрос
        try:
            q_existing_cols = f"""
            SELECT dataset, table_name, column_name, generated_description
            FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions`
            WHERE dataset = @dataset_id AND table_name = @table_name
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
                    bigquery.ScalarQueryParameter("table_name", "STRING", current_table)
                ]
            )
            existing_columns_df = bq_client.query(q_existing_cols, job_config=job_config).to_dataframe()
            print(f"Existing column descriptions (meta): {len(existing_columns_df)}")
        except Exception as e:
            logger.warning(f"Failed to fetch existing column descriptions: {e}", exc_info=True)
            existing_columns_df = pd.DataFrame(columns=['dataset','table_name','column_name','generated_description'])
            print("Existing column descriptions (meta): 0")

        try:
            q_existing_table = f"""
            SELECT dataset, table_name, table_description
            FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions`
            WHERE dataset = @dataset_id AND table_name = @table_name
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
                    bigquery.ScalarQueryParameter("table_name", "STRING", current_table)
                ]
            )
            existing_tables_df = bq_client.query(q_existing_table, job_config=job_config).to_dataframe()
            has_meta_table_desc = not existing_tables_df.empty
            if has_meta_table_desc:
                print("✓ Table description exists in meta")
        except Exception as e:
            logger.warning(f"Failed to fetch existing table description: {e}", exc_info=True)
            existing_tables_df = pd.DataFrame()
            has_meta_table_desc = False
            print("✗ Table description does not exist in meta")

        # ---- Сверяем фактическую схему BQ
        table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset_id}.{current_table}")
        bq_table_desc = (table_ref.description or "").strip()
        bq_col_desc = {f.name: (f.description or "").strip() for f in table_ref.schema}

        has_bq_table_desc = bool(bq_table_desc)
        bq_cols_total = len(table_ref.schema)
        bq_cols_with_desc = sum(1 for d in bq_col_desc.values() if d)

        meta_cols_total = len(df_cols)
        meta_cols_with_desc = len(existing_columns_df)

        fully_described_in_meta = has_meta_table_desc and (meta_cols_with_desc == meta_cols_total)
        fully_described_in_bq   = has_bq_table_desc and (bq_cols_with_desc == bq_cols_total)

        # Полностью готово в обеих системах — пропускаем
        if fully_described_in_meta and fully_described_in_bq:
            print("⊘ Table FULLY described in meta & BQ, SKIPPING\n")
            total_tables_skipped += 1
            continue

        # Нужно восстановить BQ из мета?
        need_restore_bq_from_meta = PREFER_META_OVER_BQ and fully_described_in_meta and not fully_described_in_bq
        # Нужно дозаполнить мета из BQ?
        need_fill_meta_from_bq = (not fully_described_in_meta) and fully_described_in_bq

        # ---- Быстрые пути синхронизации без OpenAI
        if need_restore_bq_from_meta or need_fill_meta_from_bq:
            if need_restore_bq_from_meta:
                print("↺ Restoring BQ schema descriptions from meta...")
                # подтянем все описания колонок из мета - параметризованный запрос
                q_all = f"""
                SELECT column_name, generated_description
                FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions`
                WHERE dataset = @dataset_id AND table_name = @table_name
                """
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
                        bigquery.ScalarQueryParameter("table_name", "STRING", current_table)
                    ]
                )
                meta_col_desc = bq_client.query(q_all, job_config=job_config).to_dataframe().set_index("column_name")["generated_description"].to_dict()
                # обновим схему BQ
                new_schema = []
                for f in table_ref.schema:
                    new_schema.append(bigquery.SchemaField(
                        f.name, f.field_type, mode=f.mode,
                        description=(meta_col_desc.get(f.name) or f.description or ""),
                        fields=f.fields
                    ))
                new_table_desc = existing_tables_df.iloc[0]["table_description"] if has_meta_table_desc else table_ref.description
                table_ref.description = new_table_desc
                table_ref.schema = new_schema
                bq_client.update_table(table_ref, ["description","schema"])
                print("✓ BQ schema restored from meta")

            if need_fill_meta_from_bq:
                print("↺ Backfilling meta tables from existing BQ descriptions...")
                # таблица
                if (not has_meta_table_desc) and has_bq_table_desc:
                    tmp_tbl = f"{PROJECT_ID}.{METADATA_DATASET_ID}._tmp_table_desc_{int(time.time()*1000)}"
                    try:
                        job = bq_client.load_table_from_dataframe(
                            pd.DataFrame([{
                                'dataset': dataset_id,
                                'table_name': current_table,
                                'table_description': bq_table_desc,
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
                        WHEN MATCHED AND IFNULL(T.table_description,'') != IFNULL(S.table_description,'') THEN
                          UPDATE SET T.table_description = S.table_description, T.job_insert_ts = CURRENT_TIMESTAMP()
                        WHEN NOT MATCHED THEN
                          INSERT (dataset, table_name, table_description, job_insert_ts)
                          VALUES (S.dataset, S.table_name, S.table_description, CURRENT_TIMESTAMP());
                        """
                        bq_client.query(merge_sql).result()
                    finally:
                        bq_client.delete_table(tmp_tbl, not_found_ok=True)

                # колонки
                missing_in_meta = [c for c in table_ref.schema
                                   if existing_columns_df[existing_columns_df["column_name"] == c.name].empty and (c.description or "").strip()]
                if missing_in_meta:
                    df_backfill = pd.DataFrame([{
                        'dataset': dataset_id,
                        'table_name': current_table,
                        'column_name': c.name,
                        'data_type': c.field_type,
                        'generated_description': (c.description or "").strip(),
                        'job_insert_ts': datetime.now()
                    } for c in missing_in_meta])
                    tmp_cols = f"{PROJECT_ID}.{METADATA_DATASET_ID}._tmp_col_desc_{int(time.time()*1000)}"
                    try:
                        job = bq_client.load_table_from_dataframe(
                            df_backfill, tmp_cols,
                            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
                        )
                        job.result()
                        merge_cols_sql = f"""
                        MERGE `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions` T
                        USING `{tmp_cols}` S
                        ON  T.dataset=S.dataset AND T.table_name=S.table_name AND T.column_name=S.column_name
                        WHEN MATCHED AND IFNULL(T.generated_description,'') != IFNULL(S.generated_description,'') THEN
                          UPDATE SET T.generated_description = S.generated_description, T.job_insert_ts = CURRENT_TIMESTAMP()
                        WHEN NOT MATCHED THEN
                          INSERT (dataset, table_name, column_name, data_type, generated_description, job_insert_ts)
                          VALUES (S.dataset, S.table_name, S.column_name, S.data_type, S.generated_description, CURRENT_TIMESTAMP());
                        """
                        bq_client.query(merge_cols_sql).result()
                    finally:
                        bq_client.delete_table(tmp_cols, not_found_ok=True)
                    print(f"✓ Meta backfilled with {len(df_backfill)} column descriptions")

            # После синхронизации обновим статусы
            table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset_id}.{current_table}")
            bq_table_desc = (table_ref.description or "").strip()
            bq_col_desc = {f.name: (f.description or "").strip() for f in table_ref.schema}
            has_bq_table_desc = bool(bq_table_desc)
            bq_cols_total = len(table_ref.schema)
            bq_cols_with_desc = sum(1 for d in bq_col_desc.values() if d)

            q_existing_cols = f"""
            SELECT dataset, table_name, column_name, generated_description
            FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions`
            WHERE dataset = @dataset_id AND table_name = @table_name
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
                    bigquery.ScalarQueryParameter("table_name", "STRING", current_table)
                ]
            )
            existing_columns_df = bq_client.query(q_existing_cols, job_config=job_config).to_dataframe()

            q_existing_table = f"""
            SELECT dataset, table_name, table_description
            FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions`
            WHERE dataset = @dataset_id AND table_name = @table_name
            """
            existing_tables_df = bq_client.query(q_existing_table, job_config=job_config).to_dataframe()
            has_meta_table_desc = not existing_tables_df.empty

            fully_described_in_meta = has_meta_table_desc and (len(existing_columns_df) == len(df_cols))
            fully_described_in_bq   = bool(bq_table_desc) and (bq_cols_with_desc == len(table_ref.schema))

            if fully_described_in_meta and fully_described_in_bq:
                print("⊘ Table FULLY described after sync, SKIPPING\n")
                total_tables_skipped += 1
                continue

        # --- На этом этапе осталось генерировать только то, чего нет ни в мета, ни в BQ

        # Нужен ли AI для описания таблицы
        need_ai_table_desc = (not has_meta_table_desc) and (not has_bq_table_desc)

        if need_ai_table_desc:
            print("\nGenerating table description (AI)...", end=' ', flush=True)
            table_desc = generate_table_description_ai(table_fqn, df_cols)
            print(f"\nDescription: {table_desc[:150]}...")
        elif has_meta_table_desc:
            table_desc = existing_tables_df.iloc[0]['table_description']
        else:
            table_desc = bq_table_desc

        # Генерация описаний колонок — только для «дырок» (нет ни в мета, ни в BQ)
        print("\nGenerating column descriptions (only missing in meta & BQ)...")
        column_results = []
        to_generate = []
        for _, r in df_cols.iterrows():
            col = r['column_name']
            has_meta = not existing_columns_df[existing_columns_df['column_name'] == col].empty
            has_bq   = bool(bq_col_desc.get(col, "").strip())
            if not has_meta and not has_bq:
                to_generate.append(r)

        print(f"  - To generate: {len(to_generate)}")
        print(f"  - Already exist somewhere: {len(df_cols) - len(to_generate)}")

        if to_generate:
            total_batches = (len(to_generate)-1)//BATCH_SIZE + 1
            for i in range(0, len(to_generate), BATCH_SIZE):
                batch = to_generate[i:i+BATCH_SIZE]           # batch — list из pd.Series
                bnum  = i//BATCH_SIZE + 1
                print(f"\n  [{bnum}/{total_batches}] Batch ({len(batch)} columns): ", end='', flush=True)

                batch_input = [{'column_name': r['column_name'], 'data_type': r['data_type']} for r in batch]
                batch_desc = generate_batch_column_descriptions(batch_input, table_fqn)

                if batch_desc:
                    for r in batch:
                        desc = batch_desc.get(r['column_name'])
                        if not desc:
                            print(f"\n    → {r['column_name']}...", end='', flush=True)
                            desc = generate_column_description_ai(r['column_name'], r['data_type'], table_fqn)
                            print(" ✓", flush=True)
                        column_results.append({
                            'dataset': dataset_id,
                            'table_name': r['table_name'],
                            'column_name': r['column_name'],
                            'data_type': r['data_type'],
                            'generated_description': desc,
                            'job_insert_ts': datetime.now()  # будет переустановлено в MERGE
                        })
                        total_columns_generated += 1
                else:
                    for r in batch:
                        print(f"\n    → {r['column_name']}...", end='', flush=True)
                        desc = generate_column_description_ai(r['column_name'], r['data_type'], table_fqn)
                        print(" ✓", flush=True)
                        column_results.append({
                            'dataset': dataset_id,
                            'table_name': r['table_name'],
                            'column_name': r['column_name'],
                            'data_type': r['data_type'],
                            'generated_description': desc,
                            'job_insert_ts': datetime.now()
                        })
                        total_columns_generated += 1

        # ---- MERGE column_descriptions (UPDATE только при изменении текста)
        if column_results:
            print(f"\nSaving {len(column_results)} column descriptions (MERGE)...", end=' ', flush=True)
            columns_df = pd.DataFrame(column_results)
            tmp_cols = f"{PROJECT_ID}.{METADATA_DATASET_ID}._tmp_col_desc_{int(time.time()*1000)}"
            try:
                job = bq_client.load_table_from_dataframe(
                    columns_df,
                    tmp_cols,
                    job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
                )
                job.result()

                merge_cols_sql = f"""
                MERGE `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions` T
                USING `{tmp_cols}` S
                ON  T.dataset     = S.dataset
                AND T.table_name  = S.table_name
                AND T.column_name = S.column_name
                WHEN MATCHED AND IFNULL(T.generated_description,'') != IFNULL(S.generated_description,'') THEN
                  UPDATE SET
                    T.generated_description = S.generated_description,
                    T.job_insert_ts         = CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN
                  INSERT (dataset, table_name, column_name, data_type, generated_description, job_insert_ts)
                  VALUES (S.dataset, S.table_name, S.column_name, S.data_type, S.generated_description, CURRENT_TIMESTAMP());
                """
                bq_client.query(merge_cols_sql).result()
            finally:
                bq_client.delete_table(tmp_cols, not_found_ok=True)
            print("✓")

        # ---- MERGE table_descriptions (UPDATE только при изменении текста)
        print("Saving table description (MERGE)...", end=' ', flush=True)
        tmp_tbl = f"{PROJECT_ID}.{METADATA_DATASET_ID}._tmp_table_desc_{int(time.time()*1000)}"
        try:
            job = bq_client.load_table_from_dataframe(
                pd.DataFrame([{
                    'dataset': dataset_id,
                    'table_name': current_table,
                    'table_description': table_desc,
                    'job_insert_ts': datetime.now()
                }]),
                tmp_tbl,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
            )
            job.result()

            merge_sql = f"""
            MERGE `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions` T
            USING `{tmp_tbl}` S
            ON  T.dataset = S.dataset
            AND T.table_name = S.table_name
            WHEN MATCHED AND IFNULL(T.table_description,'') != IFNULL(S.table_description,'') THEN
              UPDATE SET
                T.table_description = S.table_description,
                T.job_insert_ts     = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN
              INSERT (dataset, table_name, table_description, job_insert_ts)
              VALUES (S.dataset, S.table_name, S.table_description, CURRENT_TIMESTAMP());
            """
            bq_client.query(merge_sql).result()
        finally:
            bq_client.delete_table(tmp_tbl, not_found_ok=True)
        print("✓")
        total_tables_processed += 1

        # Обновляем metadata в BigQuery (описание таблицы + поля)
        if UPDATE_BIGQUERY_METADATA:
            print("Updating BigQuery metadata...", end=' ', flush=True)
            table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset_id}.{current_table}")
            # Описание таблицы — ставим наиболее актуальное
            if PREFER_META_OVER_BQ and has_meta_table_desc:
                table_ref.description = existing_tables_df.iloc[0]['table_description']
            else:
                table_ref.description = table_desc
            bq_client.update_table(table_ref, ["description"])

            # Колонки: только для обычных таблиц (не VIEW/MV)
            if not is_materialized_view:
                table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset_id}.{current_table}")
                q_all = f"""
                SELECT column_name, generated_description
                FROM `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions`
                WHERE dataset = @dataset_id AND table_name = @table_name
                """
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
                        bigquery.ScalarQueryParameter("table_name", "STRING", current_table)
                    ]
                )
                all_col_desc = bq_client.query(q_all, job_config=job_config).to_dataframe()
                new_schema = []
                for field in table_ref.schema:
                    rowd = all_col_desc[all_col_desc['column_name'] == field.name]
                    new_description = rowd.iloc[0]['generated_description'] if not rowd.empty else (field.description or "")
                    new_field = bigquery.SchemaField(
                        field.name, field.field_type, mode=field.mode,
                        description=new_description, fields=field.fields
                    )
                    new_schema.append(new_field)
                table_ref.schema = new_schema
                bq_client.update_table(table_ref, ["schema"])
                print("✓")
            else:
                print("✓ (schema update skipped for materialized view)")
        else:
            print("Skipping BigQuery metadata update (disabled)")

        # Прогресс
        avg = (time.time() - t0) / idx
        eta = (avg * (len(tables_to_process) - idx)) / 60
        print(f"\n📊 Progress: {idx}/{len(tables_to_process)} tables | ETA: {eta:.1f} min")

    # Финал
    print(f"\n{'='*80}")
    print("FINAL SUMMARY")
    print(f"{'='*80}")
    print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\nTables:")
    print(f"  - Processed: {total_tables_processed}")
    print(f"  - Skipped (already full): {total_tables_skipped}")
    print(f"  - Materialized views: {total_materialized_views}")
    print("\nColumns:")
    print(f"  - Generated: {total_columns_generated}")
    print(f"  - Skipped (already exist): {total_columns_skipped}")
    print("\nResults saved to:")
    print(f"  - {METADATA_DATASET_ID}.column_descriptions")
    print(f"  - {METADATA_DATASET_ID}.table_descriptions")
    print(f"{'='*80}")
    print("✓ COMPLETE")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()