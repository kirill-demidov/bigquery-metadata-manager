# BigQuery Metadata Descriptions Generator

Automatic generation of descriptions for BigQuery tables and columns using OpenAI GPT-4o-mini.

## Features

- 🤖 **Automatic description generation** — uses OpenAI to create detailed descriptions for tables and columns
- 🌐 **Web interface** — convenient UI for viewing, editing, and generating descriptions
- 🔄 **Metadata synchronization** — automatic synchronization between BigQuery schema and meta-tables
- 🔒 **Security** — automatic masking of sensitive data before sending to OpenAI
- ⚡ **Performance** — optimized batch queries and exclusion of sharded tables
- 📊 **Detailed statistics** — displays information about the generation process, cost, and token usage

## Description

The project consists of two components:

1. **Web interface** (`web_app.py`) — FastAPI application for interactive work with metadata
2. **Metadata generator** (`main.py`) — script for bulk generation of descriptions

### Main features:

1. **Finding tables without descriptions** — finds all tables/views without descriptions across the entire project
2. **Metadata synchronization** — compares descriptions between BigQuery and meta-tables:
   - If everything exists in meta but is lost in BQ — restores in BQ from meta (without OpenAI)
   - If everything exists in BQ but not in meta — fills meta from BQ (without OpenAI)
   - OpenAI is called only for tables/columns where descriptions don't exist in either meta or BQ
3. **Saving via MERGE** — updates meta-tables:
   - UPDATE only when text changes (updates `job_insert_ts`)
   - INSERT with `job_insert_ts = CURRENT_TIMESTAMP()` for new rows
4. **Updating BigQuery schema** — optionally updates descriptions in the BigQuery schema itself

## Requirements

### BigQuery tables

The following meta-tables must exist in your project:

- `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions`
  - Structure: `dataset STRING`, `table_name STRING`, `table_description STRING`, `job_insert_ts TIMESTAMP`
- `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions`
  - Structure: `dataset STRING`, `table_name STRING`, `column_name STRING`, `data_type STRING`, `generated_description STRING`, `job_insert_ts TIMESTAMP`

**SQL for creating tables:**

```sql
-- Create table descriptions table
CREATE TABLE `{PROJECT_ID}.{METADATA_DATASET_ID}.table_descriptions` (
  dataset STRING,
  table_name STRING,
  table_description STRING,
  job_insert_ts TIMESTAMP
);

-- Create column descriptions table
CREATE TABLE `{PROJECT_ID}.{METADATA_DATASET_ID}.column_descriptions` (
  dataset STRING,
  table_name STRING,
  column_name STRING,
  data_type STRING,
  generated_description STRING,
  job_insert_ts TIMESTAMP
);
```

### Environment variables

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
```

Fill in the required variables:

- `PROJECT_ID` — your GCP project ID (required)
- `METADATA_DATASET_ID` — dataset ID for meta-tables (default: `metadata`)
- `LOCATION_SCOPE` — BigQuery region (default: `region-eu`)
- `OPENAI_API_KEY` — OpenAI API key (required)
- `GOOGLE_CLIENT_ID` — OAuth Client ID for web interface
- `GOOGLE_CLIENT_SECRET` — OAuth Client Secret for web interface
- `SECRET_KEY` — secret key for sessions (generate a random string)
- `REDIRECT_URI` — URI for OAuth callback
- `ALLOWED_DOMAINS` — allowed domains for access (optional, comma-separated)

### Permissions

The service account or user must have the following permissions:
- `BigQuery Data Editor` — for reading and writing data
- `BigQuery Metadata Viewer` — for reading metadata
- `BigQuery Job User` — for executing queries

## Installation

### Local development

1. Clone the repository:

```bash
git clone https://github.com/your-username/tables-and-columns-description.git
cd tables-and-columns-description
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment variables (see `.env.example`)

4. Set up Application Default Credentials for BigQuery:

```bash
gcloud auth application-default login
```

### Running the web interface

```bash
python web_app.py
```

The web interface will be available at: http://localhost:8081

### Running the metadata generator

```bash
export OPENAI_API_KEY=your_api_key_here
export PROJECT_ID=your-project-id
export METADATA_DATASET_ID=metadata
python main.py
```

## Deployment to Google Cloud Run

### Deploying the web interface

1. Create secrets in Secret Manager:

```bash
# OpenAI API Key
echo -n "your-openai-api-key" | gcloud secrets create openai-api-key --data-file=-

# Google OAuth credentials
echo -n "your-client-id" | gcloud secrets create google-client-id --data-file=-
echo -n "your-client-secret" | gcloud secrets create google-client-secret --data-file=-

# Session secret key
echo -n "your-random-secret-key" | gcloud secrets create secret-key --data-file=-
```

2. Update `cloudbuild-web.yaml` with your settings

3. Deploy:

```bash
gcloud builds submit --config=cloudbuild-web.yaml
```

**Configuring user access:**

```bash
# Allow access to a specific user
gcloud run services add-iam-policy-binding tables-and-columns-description-web \
  --region europe-west1 \
  --member="user:user@example.com" \
  --role="roles/run.invoker"

# Allow access to all users in a domain
gcloud run services add-iam-policy-binding tables-and-columns-description-web \
  --region europe-west1 \
  --member="domain:example.com" \
  --role="roles/run.invoker"
```

### Deploying the metadata generator

1. Update `cloudbuild.yaml` with your settings

2. Deploy:

```bash
gcloud builds submit --config=cloudbuild.yaml
```

3. Run the Job:

```bash
gcloud run jobs execute tables-and-columns-description --region europe-west1
```

## Configuration

Main parameters are configured via environment variables or in code:

```python
PROJECT_ID = os.getenv("PROJECT_ID", "")
LOCATION_SCOPE = os.getenv("LOCATION_SCOPE", "region-eu")
METADATA_DATASET_ID = os.getenv("METADATA_DATASET_ID", "metadata")
MODEL_NAME = "gpt-4o-mini"  # or "gpt-4o" for better quality
BATCH_SIZE = 5
UPDATE_BIGQUERY_METADATA = True
PREFER_META_OVER_BQ = True
```

## Security

The project includes built-in protection for sensitive data:

- **Name masking** — automatic detection and masking of sensitive table/column names
- **Value masking** — detection and masking of PII data (email, phone, credit cards, etc.)
- **Parameterized queries** — protection against SQL injection
- **OAuth authentication** — secure authentication via Google OAuth

## Project structure

```
.
├── main.py                 # Metadata generator
├── web_app.py              # Web interface (FastAPI)
├── auth.py                 # OAuth authentication
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker image for generator
├── Dockerfile.web          # Docker image for web interface
├── cloudbuild.yaml         # Cloud Build configuration for generator
├── cloudbuild-web.yaml     # Cloud Build configuration for web interface
├── .env.example            # Configuration example
├── LICENSE                 # MIT License
└── README.md               # Documentation
```

## Performance

- **Batch processing**: columns are processed in batches of 5
- **Rate limiting**: 0.05 second delay between requests to OpenAI
- **Retry logic**: up to 3 attempts on API errors
- **Skipping completed**: tables with complete descriptions are automatically skipped
- **Optimized queries**: batch queries to BigQuery to reduce the number of calls

## Limitations

- Job timeout: 3600 seconds (1 hour) — increase in configuration if necessary
- Memory: 2Gi — may need to be increased when processing large tables
- Materialized Views: column schema updates are skipped for materialized views
- Sharded tables: automatically excluded from processing (suffixes `_YYYYMMDD` / `_YYMMDD`)

## Contributing

Pull Requests are welcome! Please make sure that:

1. Code follows the project style
2. Tests are added for new features
3. Documentation is updated

## Service account verification

To verify Cloud Run service account permissions:

```bash
PROJECT_ID="your-project-id"
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Check IAM roles on BigQuery
gcloud projects get-iam-policy ${PROJECT_ID} \
  --flatten="bindings[].members" \
  --filter="bindings.members:${SA_EMAIL}" \
  --format="table(bindings.role)"

# Check permissions on Secret Manager
SECRET_NAME="your-secret-name"
gcloud secrets get-iam-policy ${SECRET_NAME} \
  --flatten="bindings[].members" \
  --filter="bindings.members:${SA_EMAIL}" \
  --format="table(bindings.role)"
```

## Support

If you encounter issues, check:

1. Cloud Run logs (if deployed in GCP)
2. Service account/user permissions
3. Presence and correctness of meta-tables
4. OpenAI API availability
5. Correctness of environment variables

## License

MIT License - see the [LICENSE](LICENSE) file for details.
