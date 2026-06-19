# RAG SQL Agent

A Retrieval Augmented Generation (RAG) system that translates natural language questions into SQL queries and executes them against AWS Athena. Designed for teams with dbt-managed data warehouses who want to enable non-technical stakeholders to query data directly.

## How It Works

1. **Index** — A one-time indexing job reads your dbt `manifest.json` and builds vector embeddings of your table schemas, column metadata, join paths, and sample values.
2. **Retrieve** — When a user asks a question, the retriever finds the most relevant tables and columns using semantic search with HyDE re-ranking.
3. **Generate** — An LLM receives the retrieved schema context and conversation history, then generates SQL.
4. **Review & Execute** — The user reviews the generated SQL before it runs. If execution fails, the LLM proposes a self-corrected query.

```
User Question
    ↓
[Retriever] ← vector search across tables, columns, joins, and sample values
    ↓
[Query Service] ← SQL generation with conversation history
    ↓
[Frontend] ← user reviews and approves SQL
    ↓
[Athena] ← execute query, self-correct on failure
    ↓
Results
```

## Prerequisites

- Docker and Docker Compose
- AWS account with Athena access and an S3 bucket for query results
- A dbt project with a compiled `manifest.json`
- [HuggingFace](https://huggingface.co) API token (for embeddings)
- [OpenRouter](https://openrouter.ai) API key (for LLM access)

## Setup

### 1. Clone and configure environment

```bash
git clone <repo-url>
cd RAG_Project
cp .env.example .env
```

Edit `.env` with your credentials:

```env
HF_TOKEN=your_huggingface_token
OPENROUTER_API_KEY=your_openrouter_key

AWS_ACCESS_KEY_ID=your_aws_key
AWS_SECRET_ACCESS_KEY=your_aws_secret
AWS_REGION=eu-west-2

ATHENA_OUTPUT_S3=s3://your-bucket/athena-results/
ATHENA_DATABASE=your_database_name

DBT_PROJECT_PATH=/path/to/your/dbt/project
MANIFEST_PATH=/dbt/target/manifest.json
```

### 2. Configure the agent

Edit `config.yml` to match your data warehouse:

```yaml
platform:
  type: athena
  database: your_database
  sql_dialect: "Presto SQL for AWS Athena"
  region: eu-west-2

embedding:
  model_name: "Qwen/Qwen3-Embedding-0.6B"

llm:
  model_name: "nvidia/nemotron-3-super-120b-a12b:free"
  temperature: 0

# Columns to index for semantic value matching
semantic_dictionary:
  your_table_name: [column_a, column_b]

# Pre-defined join paths for multi-table queries
common_paths:
  - path: "table_a -> table_b"
    logic: "JOIN table_b ON table_a.id = table_b.foreign_id"
```

### 3. Build the vector index

Run the indexing job once (and re-run whenever your dbt schema changes):

```bash
docker compose run --rm vector-builder
```

This reads your `manifest.json` and writes three vector stores to `schema_db/`, `value_db/`, and `join_db/`.

### 4. Start the services

```bash
docker compose up retriever query-service streamlit-app
```

| Service | URL | Role |
|---------|-----|------|
| Retriever | http://localhost:8000 | Schema context retrieval |
| Query Service | http://localhost:8003 | SQL generation and execution |
| Frontend | http://localhost:8501 | Web UI |

Open **http://localhost:8501** in your browser to start querying.

## Usage

### Web UI

1. Type a question in plain English (e.g. *"How many orders were placed last month by product family?"*)
2. Review the generated SQL that appears
3. Click **Approve** to execute, or **Cancel** to discard and rephrase
4. Results appear as a table below the chat
5. Ask follow-up questions — the agent remembers the conversation context
6. Click **New Session** to reset the conversation

### API

The query service exposes a REST API if you want to integrate the agent into other tools.

**Generate SQL from a question:**

```bash
curl -X POST http://localhost:8003/generate-sql \
  -H "Content-Type: application/json" \
  -d '{"question": "What were the top 10 products by revenue last quarter?", "session_id": "my-session"}'
```

**Execute SQL:**

```bash
curl -X POST http://localhost:8003/execute-sql \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT ...", "session_id": "my-session"}'
```

The execute endpoint validates queries before running them (blocks `DROP`, `DELETE`, `INSERT`, `UPDATE`, `ALTER`) and automatically adds `LIMIT 1000` if no limit is specified.

## Updating the Schema Index

Re-run the vector builder whenever your dbt models change:

```bash
docker compose run --rm vector-builder
```

Then restart the retriever to pick up the new index:

```bash
docker compose restart retriever
```

## Architecture

| Component | File | Description |
|-----------|------|-------------|
| Indexer | `generate_database_knowledge.py` | Parses dbt manifest and builds ChromaDB vector stores |
| Retriever API | `retriever_api.py` | FastAPI service; retrieves schema context via semantic search |
| Query Service | `query_api.py` | FastAPI service; generates SQL via LangGraph + executes via Athena adapter |
| SQL Generator | `generate_sql.py` | LangGraph graph for LLM-powered SQL generation |
| Frontend | `frontend.py` | Streamlit chat UI |
| Session Store | `session_store.py` | In-memory session management for conversation history |
| Athena Adapter | `adapters/athena.py` | AWS Athena query execution via boto3 |

## Troubleshooting

**Vector builder fails to find manifest.json** — Check that `DBT_PROJECT_PATH` in `.env` points to your dbt project root and that you have run `dbt compile` or `dbt run` to generate `target/manifest.json`.

**Retriever returns no results** — The vector index may be empty or mismatched. Re-run the vector builder and restart the retriever.

**Athena queries fail with permission errors** — Ensure the AWS credentials in `.env` have `athena:StartQueryExecution`, `athena:GetQueryExecution`, `athena:GetQueryResults`, and `s3:PutObject` / `s3:GetObject` permissions on the output bucket.

**LLM generates incorrect SQL** — Add the problematic tables and join paths to `common_paths` in `config.yml`, or enrich column descriptions in your dbt model YAML files to give the retriever better context.
