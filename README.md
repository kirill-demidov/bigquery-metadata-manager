# BigQuery Metadata Descriptions Generator

Автоматическая генерация описаний для таблиц и колонок BigQuery с использованием OpenAI GPT-4o-mini.

## Возможности

- 🤖 **Автоматическая генерация описаний** — использует OpenAI для создания детальных описаний таблиц и колонок
- 🌐 **Веб-интерфейс** — удобный UI для просмотра, редактирования и генерации описаний
- 🔄 **Синхронизация метаданных** — автоматическая синхронизация между BigQuery schema и мета-таблицами
- 🔒 **Безопасность** — автоматическое маскирование чувствительных данных перед отправкой в OpenAI
- ⚡ **Производительность** — оптимизированные batch запросы и исключение шардированных таблиц
- 📊 **Детальная статистика** — отображение информации о процессе генерации, стоимости и использовании токенов

## Описание

Проект состоит из двух компонентов:

1. **Веб-интерфейс** (`web_app.py`) — FastAPI приложение для интерактивной работы с метаданными
2. **Генератор метаданных** (`main.py`) — скрипт для массовой генерации описаний

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

Должны существовать следующие мета-таблицы в вашем проекте:

- `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions`
  - Структура: `dataset STRING`, `table_name STRING`, `table_description STRING`, `job_insert_ts TIMESTAMP`
- `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions`
  - Структура: `dataset STRING`, `table_name STRING`, `column_name STRING`, `data_type STRING`, `generated_description STRING`, `job_insert_ts TIMESTAMP`

**SQL для создания таблиц:**

```sql
-- Создание таблицы описаний таблиц
CREATE TABLE `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions` (
  dataset STRING,
  table_name STRING,
  table_description STRING,
  job_insert_ts TIMESTAMP
);

-- Создание таблицы описаний колонок
CREATE TABLE `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions` (
  dataset STRING,
  table_name STRING,
  column_name STRING,
  data_type STRING,
  generated_description STRING,
  job_insert_ts TIMESTAMP
);
```

### Переменные окружения

Создайте файл `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

Заполните необходимые переменные:

- `PROJECT_ID` — ID вашего GCP проекта (обязательно)
- `METADATA_DATASET_ID` — ID датасета для мета-таблиц (по умолчанию: `metadata`)
- `LOCATION_SCOPE` — регион BigQuery (по умолчанию: `region-eu`)
- `OPENAI_API_KEY` — API ключ OpenAI (обязательно)
- `GOOGLE_CLIENT_ID` — OAuth Client ID для веб-интерфейса
- `GOOGLE_CLIENT_SECRET` — OAuth Client Secret для веб-интерфейса
- `SECRET_KEY` — секретный ключ для сессий (сгенерируйте случайную строку)
- `REDIRECT_URI` — URI для OAuth callback
- `ALLOWED_DOMAINS` — разрешенные домены для доступа (опционально, через запятую)

### Права доступа

Сервисный аккаунт или пользователь должен иметь права:
- `BigQuery Data Editor` — для чтения и записи данных
- `BigQuery Metadata Viewer` — для чтения метаданных
- `BigQuery Job User` — для выполнения запросов

## Установка

### Локальная разработка

1. Клонируйте репозиторий:

```bash
git clone https://github.com/your-username/tables-and-columns-description.git
cd tables-and-columns-description
```

2. Установите зависимости:

```bash
pip install -r requirements.txt
```

3. Настройте переменные окружения (см. `.env.example`)

4. Настройте Application Default Credentials для BigQuery:

```bash
gcloud auth application-default login
```

### Запуск веб-интерфейса

```bash
python web_app.py
```

Веб-интерфейс будет доступен по адресу: http://localhost:8081

### Запуск генератора метаданных

```bash
export OPENAI_API_KEY=your_api_key_here
export PROJECT_ID=your-project-id
export METADATA_DATASET_ID=metadata
python main.py
```

## Развертывание на Google Cloud Run

### Развертывание веб-интерфейса

1. Создайте секреты в Secret Manager:

```bash
# OpenAI API Key
echo -n "your-openai-api-key" | gcloud secrets create openai-api-key --data-file=-

# Google OAuth credentials
echo -n "your-client-id" | gcloud secrets create google-client-id --data-file=-
echo -n "your-client-secret" | gcloud secrets create google-client-secret --data-file=-

# Session secret key
echo -n "your-random-secret-key" | gcloud secrets create secret-key --data-file=-
```

2. Обновите `cloudbuild-web.yaml` с вашими настройками

3. Разверните:

```bash
gcloud builds submit --config=cloudbuild-web.yaml
```

**Настройка доступа пользователей:**

```bash
# Разрешить доступ конкретному пользователю
gcloud run services add-iam-policy-binding tables-and-columns-description-web \
  --region europe-west1 \
  --member="user:user@example.com" \
  --role="roles/run.invoker"

# Разрешить доступ всем пользователям в домене
gcloud run services add-iam-policy-binding tables-and-columns-description-web \
  --region europe-west1 \
  --member="domain:example.com" \
  --role="roles/run.invoker"
```

### Развертывание генератора метаданных

1. Обновите `cloudbuild.yaml` с вашими настройками

2. Разверните:

```bash
gcloud builds submit --config=cloudbuild.yaml
```

3. Запустите Job:

```bash
gcloud run jobs execute tables-and-columns-description --region europe-west1
```

## Конфигурация

Основные параметры настраиваются через переменные окружения или в коде:

```python
PROJECT_ID = os.getenv("PROJECT_ID", "")
LOCATION_SCOPE = os.getenv("LOCATION_SCOPE", "region-eu")
METADATA_DATASET_ID = os.getenv("METADATA_DATASET_ID", "metadata")
MODEL_NAME = "gpt-4o-mini"  # или "gpt-4o" для лучшего качества
BATCH_SIZE = 5
UPDATE_BIGQUERY_METADATA = True
PREFER_META_OVER_BQ = True
```

## Безопасность

Проект включает встроенную защиту чувствительных данных:

- **Маскирование имен** — автоматическое определение и маскирование чувствительных имен таблиц/колонок
- **Маскирование значений** — обнаружение и маскирование PII данных (email, телефон, кредитные карты и т.д.)
- **Параметризованные запросы** — защита от SQL injection
- **OAuth аутентификация** — безопасная аутентификация через Google OAuth

## Структура проекта

```
.
├── main.py                 # Генератор метаданных
├── web_app.py              # Веб-интерфейс (FastAPI)
├── auth.py                 # OAuth аутентификация
├── requirements.txt        # Python зависимости
├── Dockerfile              # Docker образ для генератора
├── Dockerfile.web          # Docker образ для веб-интерфейса
├── cloudbuild.yaml         # Cloud Build конфигурация для генератора
├── cloudbuild-web.yaml     # Cloud Build конфигурация для веб-интерфейса
├── .env.example            # Пример конфигурации
├── LICENSE                 # MIT License
└── README.md               # Документация
```

## Производительность

- **Batch обработка**: колонки обрабатываются батчами по 5 штук
- **Rate limiting**: задержка 0.05 секунды между запросами к OpenAI
- **Retry logic**: до 3 попыток при ошибках API
- **Пропуск готовых**: таблицы с полными описаниями пропускаются автоматически
- **Оптимизированные запросы**: batch запросы к BigQuery для уменьшения количества обращений

## Ограничения

- Таймаут задачи: 3600 секунд (1 час) — при необходимости увеличьте в конфигурации
- Память: 2Gi — при обработке больших таблиц может потребоваться увеличение
- Materialized Views: обновление схемы колонок пропускается для материализованных представлений
- Шардированные таблицы: автоматически исключаются из обработки (суффиксы `_YYYYMMDD` / `_YYMMDD`)

## Вклад

Приветствуются Pull Requests! Пожалуйста, убедитесь, что:

1. Код соответствует стилю проекта
2. Добавлены тесты для новых функций
3. Обновлена документация

## Проверка сервисного аккаунта

Для проверки прав сервисного аккаунта Cloud Run:

```bash
PROJECT_ID="your-project-id"
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Проверить IAM роли на BigQuery
gcloud projects get-iam-policy ${PROJECT_ID} \
  --flatten="bindings[].members" \
  --filter="bindings.members:${SA_EMAIL}" \
  --format="table(bindings.role)"

# Проверить права на Secret Manager
SECRET_NAME="your-secret-name"
gcloud secrets get-iam-policy ${SECRET_NAME} \
  --flatten="bindings[].members" \
  --filter="bindings.members:${SA_EMAIL}" \
  --format="table(bindings.role)"
```

## Поддержка

При возникновении проблем проверьте:

1. Логи Cloud Run (если развернуто в GCP)
2. Права доступа сервисного аккаунта/пользователя
3. Наличие и корректность мета-таблиц
4. Доступность OpenAI API
5. Правильность переменных окружения

## Лицензия

MIT License - см. файл [LICENSE](LICENSE) для деталей.
