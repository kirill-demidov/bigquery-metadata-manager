# Проверка сервисного аккаунта

## Как проверить текущий сервисный аккаунт

### 1. Узнать номер проекта
```bash
PROJECT_ID="your-project-id"
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
echo "Project Number: $PROJECT_NUMBER"
```

### 2. Дефолтный сервисный аккаунт Cloud Run
```bash
# Для Cloud Run Job
gcloud run jobs describe tables-and-columns-description \
  --region europe-west1 \
  --format='value(spec.template.spec.serviceAccountName)'

# Для Cloud Run Service (Web UI)
gcloud run services describe tables-and-columns-description-web \
  --region europe-west1 \
  --format='value(spec.template.spec.serviceAccountName)'
```

Если не указан явно, будет использоваться:
`${PROJECT_NUMBER}-compute@developer.gserviceaccount.com`

### 3. Проверить права сервисного аккаунта на BigQuery
```bash
PROJECT_ID="your-project-id"
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Проверить IAM роли
gcloud projects get-iam-policy ${PROJECT_ID} \
  --flatten="bindings[].members" \
  --filter="bindings.members:${SA_EMAIL}" \
  --format="table(bindings.role)"
```

### 4. Проверить права на Secret Manager
```bash
SECRET_NAME="your-secret-name"  # например, openai-api-key
gcloud secrets get-iam-policy ${SECRET_NAME} \
  --flatten="bindings[].members" \
  --filter="bindings.members:${SA_EMAIL}" \
  --format="table(bindings.role)"
```

## Рекомендация: создать отдельный сервисный аккаунт

Для лучшей безопасности рекомендуется создать отдельный сервисный аккаунт:

```bash
PROJECT_ID="your-project-id"
SECRET_NAME="your-secret-name"  # например, openai-api-key

# Создать сервисный аккаунт
gcloud iam service-accounts create bq-metadata-generator \
  --display-name="BigQuery Metadata Generator" \
  --description="Service account for BigQuery metadata generation" \
  --project=${PROJECT_ID}

SA_EMAIL="bq-metadata-generator@${PROJECT_ID}.iam.gserviceaccount.com"

# Назначить права на BigQuery
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.metadataViewer"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.jobUser"

# Назначить права на Secret Manager
gcloud secrets add-iam-policy-binding ${SECRET_NAME} \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --project=${PROJECT_ID}
```

Затем обновить Cloud Run Job и Service:
```bash
# Для Job
gcloud run jobs update tables-and-columns-description \
  --region europe-west1 \
  --service-account=${SA_EMAIL}

# Для Web Service
gcloud run services update tables-and-columns-description-web \
  --region europe-west1 \
  --service-account=${SA_EMAIL}
```

И обновить cloudbuild.yaml и cloudbuild-web.yaml, добавив:
`--service-account=${SA_EMAIL}`

