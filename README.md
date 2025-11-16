# BigQuery Metadata Descriptions Generator

Автоматическая генерация описаний для таблиц и колонок BigQuery с использованием OpenAI.

## Описание

Скрипт обрабатывает все таблицы и представления в проекте BigQuery `guns-and-gangs` (регион `region-eu`), генерируя описания для таблиц и колонок, которые их не имеют. Исключает шардированные таблицы с суффиксами `_YYYYMMDD` или `_YYMMDD`.

### Основные возможности:

1. **Поиск таблиц без описаний** — находит все таблицы/вью без описания во всём проекте
2. **Синхронизация метаданных** — сверяет описания между BigQuery и мета-таблицами:
   - Если в мета всё есть, а в BQ утеряно — восстанавливает в BQ из мета (без OpenAI)
   - Если в BQ всё есть, а в мета нет — дозаполняет мета из BQ (без OpenAI)
   - OpenAI вызывается только для тех таблиц/колонок, где описания нет ни в мета, ни в BQ
3. **Сохранение через MERGE** — обновляет мета-таблицы:
   - UPDATE только при изменении текста (обновляет `job_insert_ts`)
   - INSERT с `job_insert_ts = CURRENT_TIMESTAMP()` для новых строк
4. **Обновление BigQuery схемы** — опционально обновляет описания в самой BigQuery-схеме

## Требования

### BigQuery таблицы

Должны существовать следующие мета-таблицы:
- `guns-and-gangs.analytics_280581623.table_descriptions`
  - Структура: `dataset`, `table_name`, `table_description`, `job_insert_ts TIMESTAMP`
- `guns-and-gangs.analytics_280581623.column_descriptions`
  - Структура: `dataset`, `table_name`, `column_name`, `data_type`, `generated_description`, `job_insert_ts TIMESTAMP`

### Переменные окружения

- `OPENAI_API_KEY` — API ключ OpenAI (обязательно)

### Права доступа

Сервисный аккаунт Cloud Run должен иметь права:
- `BigQuery Data Editor` — для чтения и записи данных
- `BigQuery Metadata Viewer` — для чтения метаданных
- `BigQuery Job User` — для выполнения запросов

## Локальная разработка

### Установка зависимостей

```bash
pip install -r requirements.txt
```

### Запуск

```bash
export OPENAI_API_KEY=your_api_key_here
python main.py
```

## Развертывание на Google Cloud Run

Проект настроен для развертывания как **Cloud Run Job** (фоновый процесс).

### Вариант 1: Автоматический деплой через Cloud Build

```bash
gcloud builds submit --config=cloudbuild.yaml
```

**Примечание:** API ключ автоматически берется из Secret Manager (`analytics_table_desc_ai_creator`). Убедитесь, что Cloud Build имеет доступ к секрету.

### Вариант 2: Ручной деплой

#### 1. Сборка Docker образа

```bash
gcloud builds submit --tag gcr.io/guns-and-gangs/tables-and-columns-description
```

#### 2. Создание Cloud Run Job

```bash
gcloud run jobs create tables-and-columns-description \
  --image gcr.io/guns-and-gangs/tables-and-columns-description \
  --region europe-west1 \
  --memory 2Gi \
  --cpu 2 \
  --max-retries 1 \
  --task-timeout 3600 \
  --set-secrets OPENAI_API_KEY=analytics_table_desc_ai_creator:latest
```

#### 3. Запуск Job

```bash
gcloud run jobs execute tables-and-columns-description --region europe-west1
```

### Настройка переменных окружения через Secret Manager (рекомендуется)

API ключ OpenAI хранится в Secret Manager GCP под именем `analytics_table_desc_ai_creator`.

**Важно:** Убедитесь, что сервисный аккаунт Cloud Run имеет доступ к секрету:

```bash
# Предоставить доступ Cloud Run к секрету (если еще не предоставлен)
gcloud secrets add-iam-policy-binding analytics_table_desc_ai_creator \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Секрет автоматически подключается при деплое через Cloud Build. При ручном создании Job используйте:

```bash
gcloud run jobs create tables-and-columns-description \
  --image gcr.io/guns-and-gangs/tables-and-columns-description \
  --region europe-west1 \
  --memory 2Gi \
  --cpu 2 \
  --max-retries 1 \
  --task-timeout 3600 \
  --set-secrets OPENAI_API_KEY=analytics_table_desc_ai_creator:latest
```

## Конфигурация

Основные параметры можно изменить в `main.py`:

```python
PROJECT_ID = "guns-and-gangs"
LOCATION_SCOPE = "region-eu"
METADATA_DATASET_ID = "analytics_280581623"
MODEL_NAME = "gpt-4o-mini"  # или "gpt-4o" для лучшего качества
BATCH_SIZE = 5
UPDATE_BIGQUERY_METADATA = True
PREFER_META_OVER_BQ = True
```

## Мониторинг

### Просмотр логов

```bash
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=tables-and-columns-description" --limit 50
```

### Просмотр истории выполнения

```bash
gcloud run jobs executions list --job tables-and-columns-description --region europe-west1
```

## Автоматизация запуска

### Cloud Scheduler (ежедневный запуск)

```bash
gcloud scheduler jobs create http tables-and-columns-description-daily \
  --location=europe-west1 \
  --schedule="0 2 * * *" \
  --uri="https://europe-west1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/guns-and-gangs/jobs/tables-and-columns-description:run" \
  --http-method=POST \
  --oauth-service-account-email=PROJECT_NUMBER-compute@developer.gserviceaccount.com
```

## Структура проекта

```
.
├── main.py                 # Основной скрипт
├── requirements.txt        # Python зависимости
├── Dockerfile             # Docker образ
├── .dockerignore          # Исключения для Docker
├── cloudbuild.yaml        # Конфигурация Cloud Build
└── README.md              # Документация
```

## Производительность

- **Batch обработка**: колонки обрабатываются батчами по 5 штук
- **Rate limiting**: задержка 0.05 секунды между запросами к OpenAI
- **Retry logic**: до 3 попыток при ошибках API
- **Пропуск готовых**: таблицы с полными описаниями пропускаются автоматически

## Ограничения

- Таймаут задачи: 3600 секунд (1 час) — при необходимости увеличьте в конфигурации
- Память: 2Gi — при обработке больших таблиц может потребоваться увеличение
- Materialized Views: обновление схемы колонок пропускается для материализованных представлений

## Поддержка

При возникновении проблем проверьте:
1. Логи Cloud Run Job
2. Права доступа сервисного аккаунта
3. Наличие и корректность мета-таблиц
4. Доступность OpenAI API

