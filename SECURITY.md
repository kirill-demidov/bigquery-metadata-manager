# Анализ безопасности кода

## Критические проблемы безопасности

### 1. ⚠️ SQL Injection через f-strings

**Проблема:** Использование f-strings для вставки значений в SQL запросы без валидации и экранирования.

**Затронутые места:**
- Строки 337-342: `WHERE table_name = '{current_table}'`
- Строки 348-352: `WHERE dataset = '{dataset_id}' AND table_name = '{current_table}'`
- Строки 360-364: `WHERE dataset = '{dataset_id}' AND table_name = '{current_table}'`
- Строки 405-409: аналогично
- Строки 492-496, 499-503, 661-665: аналогично

**Риск:** Средний-Низкий (данные приходят из BigQuery, но могут быть скомпрометированы)

**Решение:** Использовать параметризованные запросы BigQuery через `QueryJobConfig`:

```python
from google.cloud.bigquery import QueryJobConfig

query = """
SELECT table_name, column_name, data_type
FROM `{}.{}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = @table_name
ORDER BY column_name
""".format(PROJECT_ID, dataset_id)

job_config = QueryJobConfig(
    query_parameters=[
        bigquery.ScalarQueryParameter("table_name", "STRING", current_table)
    ]
)
df_cols = bq_client.query(query, job_config=job_config).to_dataframe()
```

### 2. ⚠️ Отправка чувствительных данных в OpenAI

**Проблема:** Имена таблиц и колонок могут содержать чувствительную информацию (например, `user_pii`, `payment_data`, `customer_ssn`), которая отправляется в OpenAI API.

**Затронутые места:**
- Строки 89-102: `generate_column_description_ai` - отправляет `table_fqn` и `column_name`
- Строки 138-160: `generate_batch_column_descriptions` - отправляет список колонок
- Строки 205-221: `generate_table_description_ai` - отправляет `table_fqn` и список колонок

**Риск:** Высокий - утечка структуры данных в сторонний сервис

**Решение:**
1. Добавить фильтрацию чувствительных таблиц/колонок по паттернам
2. Использовать маскирование имен перед отправкой в OpenAI
3. Добавить whitelist/blacklist для таблиц

```python
SENSITIVE_PATTERNS = [
    r'.*pii.*', r'.*personal.*', r'.*ssn.*', r'.*password.*',
    r'.*credit.*card.*', r'.*payment.*', r'.*secret.*'
]

def is_sensitive_name(name):
    return any(re.search(pattern, name.lower()) for pattern in SENSITIVE_PATTERNS)

def mask_table_name(table_fqn):
    if is_sensitive_name(table_fqn):
        # Заменить на обобщенное имя
        return "sensitive_table"
    return table_fqn
```

### 3. ⚠️ Логирование чувствительной информации

**Проблема:** В логи выводятся имена таблиц, колонок и описания, которые могут содержать чувствительную информацию.

**Затронутые места:**
- Строка 325: `print(f"[{idx}/{len(tables_to_process)}] TABLE: {table_fqn}")`
- Строка 523: `print(f"\nDescription: {table_desc[:150]}...")`
- Строки 557, 571: вывод имен колонок

**Риск:** Средний - утечка структуры данных через логи Cloud Run

**Решение:**
1. Использовать уровни логирования (DEBUG/INFO/WARN)
2. Маскировать чувствительные имена в логах
3. Не логировать описания полностью

```python
import logging

logger = logging.getLogger(__name__)

def safe_log_table(table_fqn):
    if is_sensitive_name(table_fqn):
        logger.info(f"Processing sensitive table: {mask_table_name(table_fqn)}")
    else:
        logger.info(f"Processing table: {table_fqn}")
```

### 4. ⚠️ Отсутствие валидации входных данных

**Проблема:** Нет проверки формата и содержимого данных из BigQuery перед использованием.

**Затронутые места:**
- Строка 287: `TABLE_NAME.split(".", 1)` - может упасть при неверном формате
- Все места использования `dataset_id` и `current_table` без валидации

**Риск:** Средний - возможны ошибки выполнения и потенциальные уязвимости

**Решение:**
```python
def validate_table_name(table_name):
    """Валидация имени таблицы BigQuery"""
    if not table_name or len(table_name) > 1024:
        raise ValueError("Invalid table name")
    # BigQuery имена могут содержать только буквы, цифры, подчеркивания
    if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
        raise ValueError(f"Invalid characters in table name: {table_name}")
    return table_name

def validate_dataset_name(dataset_id):
    """Валидация имени датасета BigQuery"""
    if not dataset_id or len(dataset_id) > 1024:
        raise ValueError("Invalid dataset name")
    if not re.match(r'^[a-zA-Z0-9_]+$', dataset_id):
        raise ValueError(f"Invalid characters in dataset name: {dataset_id}")
    return dataset_id
```

### 5. ⚠️ Широкий доступ ко всем таблицам проекта

**Проблема:** Скрипт имеет доступ ко всем таблицам проекта без ограничений.

**Риск:** Высокий - может обработать чувствительные таблицы, которые не должны обрабатываться

**Решение:**
1. Добавить whitelist/blacklist датасетов
2. Добавить фильтрацию по паттернам имен таблиц
3. Использовать IAM политики для ограничения доступа

```python
ALLOWED_DATASETS = ["analytics", "staging"]  # whitelist
BLOCKED_DATASETS = ["pii", "sensitive"]     # blacklist

def should_process_table(dataset_id, table_name):
    if dataset_id in BLOCKED_DATASETS:
        return False
    if ALLOWED_DATASETS and dataset_id not in ALLOWED_DATASETS:
        return False
    if is_sensitive_name(f"{dataset_id}.{table_name}"):
        return False
    return True
```

### 6. ⚠️ Временные таблицы могут остаться при ошибках

**Проблема:** Временные таблицы создаются, но могут не удалиться при ошибках.

**Затронутые места:**
- Строки 429, 465, 588, 616: создание временных таблиц

**Риск:** Низкий - утечка ресурсов и потенциальная утечка данных

**Решение:** Использовать try-finally для гарантированного удаления:

```python
tmp_tbl = f"{PROJECT_ID}.{METADATA_DATASET_ID}._tmp_table_desc_{int(time.time()*1000)}"
try:
    job = bq_client.load_table_from_dataframe(...)
    job.result()
    # ... merge операция
finally:
    bq_client.delete_table(tmp_tbl, not_found_ok=True)
```

### 7. ⚠️ Слишком широкий except без логирования

**Проблема:** Использование `except:` без указания типа исключения и без логирования.

**Затронутые места:**
- Строки 355, 369: `except:` без обработки

**Риск:** Низкий-Средний - скрытие ошибок и сложность отладки

**Решение:**
```python
except Exception as e:
    logger.warning(f"Failed to fetch existing descriptions: {e}", exc_info=True)
    existing_columns_df = pd.DataFrame(...)
```

## Рекомендации по улучшению безопасности

### 1. Использовать Secret Manager для API ключей
✅ Уже упомянуто в README, но нужно реализовать в cloudbuild.yaml

### 2. Добавить мониторинг и алерты
- Отслеживать попытки доступа к чувствительным таблицам
- Алерты при ошибках аутентификации
- Логирование всех операций с метаданными

### 3. Ограничить права сервисного аккаунта
- Использовать принцип минимальных привилегий
- Создать отдельный сервисный аккаунт только для этого скрипта
- Ограничить доступ только к необходимым датасетам

### 4. Добавить аудит операций
- Логировать все изменения в мета-таблицах
- Отслеживать, какие таблицы обрабатываются
- Сохранять историю изменений

### 5. Регулярный security review
- Проверять код на уязвимости перед деплоем
- Использовать автоматизированные инструменты (Bandit, Safety)
- Проводить code review с фокусом на безопасность

## Приоритет исправлений

1. **Критично:** Исправить SQL injection (проблема #1)
2. **Высокий:** Добавить фильтрацию чувствительных данных для OpenAI (проблема #2)
3. **Высокий:** Ограничить доступ к таблицам (проблема #5)
4. **Средний:** Улучшить логирование (проблема #3)
5. **Средний:** Добавить валидацию входных данных (проблема #4)
6. **Низкий:** Исправить обработку ошибок (проблемы #6, #7)

