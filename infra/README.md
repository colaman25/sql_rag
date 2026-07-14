# sql-rag AWS infrastructure (CDK, Python)

Deploys the RAG SQL Agent (see repo root `docker-compose.yml`) to ECS Fargate.

## What gets created

| Stack | Resources |
|---|---|
| `SqlRag-Ecr` | ECR repository `sql-rag` |
| `SqlRag-Network` | VPC (2 AZs, public subnets only, no NAT gateway), 5 security groups |
| `SqlRag-Data` | 3 Secrets Manager secrets (placeholders), DynamoDB table `rag-sessions`, encrypted EFS filesystem + access point |
| `SqlRag-Ecs` | ECS cluster, Cloud Map namespace, IAM execution/task roles, 4 task definitions, 3 Fargate services (retriever, query-service, streamlit) |

Mapping from `docker-compose.yml`:

| docker-compose service | ECS equivalent | Notes |
|---|---|---|
| `vector-builder` (profile `setup`) | standalone task def `sql-rag-vector-builder`, run on demand via `run-task` | not a long-running service, same as `docker compose run --rm vector-builder` |
| `retriever` | Fargate service `sql-rag-retriever` | public subnet + public IP, EFS-mounted at `/app/vectorstore` |
| `query-service` | Fargate service `sql-rag-query-service` | public subnet + public IP |
| `streamlit-app` | Fargate service `sql-rag-streamlit` | public subnet + public IP (no ALB, per request) |

Service-to-service calls (`http://retriever:8000`, `http://query-service:8003`) work unchanged because all three services join an ECS **Service Connect** namespace (`sqlrag.internal`) using the same DNS names as docker-compose.

### Design decisions worth knowing about

- **No NAT gateway.** Every task (retriever, query-service, streamlit, vector-builder) runs in the public subnet with its own public IP and reaches the internet directly via the Internet Gateway -- avoiding NAT's hourly charge plus per-GB data processing fees, which add up fast given the retriever re-downloads its embedding model from HuggingFace on every restart. This is safe because security groups, not subnet placement, are the actual access boundary here: retriever/query-service/vector-builder security groups have no inbound rules from the internet (0.0.0.0/0) at all, only from specific sibling security groups, so having a public IP doesn't make them reachable from outside the VPC. Only `sql-rag-streamlit`'s security group intentionally allows inbound from the internet, since it's the app's front door.
- **No ALB.** Streamlit gets a Fargate-assigned public IP directly. This IP **changes** every time the service is redeployed or the task is replaced — re-run `scripts/get_streamlit_url.*` after any deployment. Revisit adding an ALB (+ optionally a domain/ACM cert) once this needs to be stable or handle real traffic.
- **`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` are gone.** The original `query-service` container used these for boto3 calls (DynamoDB). On ECS, the task role (`sql-rag-query-service-task-role`) is granted DynamoDB access directly — boto3 picks it up automatically with no credentials in the environment.
- **`ATHENA_AWS_ACCESS_KEY_ID` / `ATHENA_AWS_SECRET_ACCESS_KEY` are kept as explicit credentials** (in Secrets Manager) because the app passes them explicitly to a separate `boto3.client("athena", ...)` / S3 session in `adapters/athena.py` and `generate_database_knowledge.py` — this looks like a deliberately separate AWS account/user from the one ECS runs in, so it can't be replaced by the task role.
- **EFS, not S3-sync**, shares the ChromaDB vectorstore between the batch indexer and the retriever service, matching the current shared-volume behavior exactly.
- **Secrets are never in code.** The CDK stack creates the 3 secrets with a placeholder string; `scripts/push_secrets.*` populates real values from your local `.env` after deploy.

## Prerequisites

- AWS CLI configured (`aws sts get-caller-identity` works)
- Docker Desktop running
- Node.js (for the `aws-cdk` CLI) and Python 3.11+
- `npm install -g aws-cdk` (if you don't already have it)
- Your `.env` filled in at the repo root (copy from `.env.example`) — used only to seed Secrets Manager, never read by CDK itself
- The Athena results bucket and the dbt manifest bucket already exist (per your answers, CDK does not create them — it only grants IAM/credential access)

## One-time setup

```powershell
cd infra
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cdk bootstrap
```

## Deploy sequence

Run everything from `infra/` with the venv activated. Replace the context values with your own (or edit the defaults in `cdk.json`).

`$ctx` must be a PowerShell **array**, passed with the `@ctx` splat operator -- not a plain string passed as `$ctx`. PowerShell does not word-split a string variable when it's handed to a native exe (unlike bash), so `cdk deploy Foo $ctx` silently glues the whole string into one bad argument and CDK ignores it, silently falling back to defaults. `cdk deploy Foo @ctx` (splat) passes each element as its own argument, which is what you want.

```powershell
$ctx = @(
    "-c", "athena_output_s3=s3://dennis-athena-result-bucket",
    "-c", "athena_database=dev",
    "-c", "manifest_path=s3://your-manifest-bucket/manifest.json"
)

# 1. Create the ECR repository
#    (if a repo named `sql-rag` already exists outside CDK, run `cdk import SqlRag-Ecr` instead)
cdk deploy SqlRag-Ecr

# 2. Bake and push the Docker image (uses the repo root Dockerfile)
.\scripts\build_and_push.ps1

# 3. Create networking + data layer (VPC, security groups, secrets, DynamoDB, EFS)
cdk deploy SqlRag-Network SqlRag-Data @ctx

# 4. Populate the real secret values from your local .env
.\scripts\push_secrets.ps1

# 5. Create the ECS cluster, task definitions, and services
cdk deploy SqlRag-Ecs @ctx
```

At this point `retriever`, `query-service`, and `streamlit` are all running, but the vectorstore is empty (same as a fresh docker-compose setup before the indexer has run).

```powershell
# 6. Get the public subnet ids (stack output PublicSubnetIds) + vector-builder security group id from the SqlRag-Ecs stack outputs,
#    then run the one-off indexing job (equivalent of `docker compose run --rm vector-builder`)
.\scripts\run_vector_builder.ps1 -Subnets "subnet-aaa,subnet-bbb" -SecurityGroup "sg-xxxx"

# 7. Once it finishes (check with the command the script prints), restart retriever to load the new index
aws ecs update-service --cluster sql-rag-cluster --service sql-rag-retriever --force-new-deployment

# 8. Get the live URL
.\scripts\get_streamlit_url.ps1
```

Open the printed `http://<ip>:8501` in a browser.

Bash equivalents of every script are in `scripts/*.sh` for non-Windows use.

## Updating the app

```powershell
.\scripts\build_and_push.ps1 -Tag v2
cdk deploy SqlRag-Ecs @ctx -c image_tag=v2
```

## Updating the schema index

Whenever the dbt schema changes, same as the original README:

```powershell
.\scripts\run_vector_builder.ps1 -Subnets "subnet-aaa,subnet-bbb" -SecurityGroup "sg-xxxx"
aws ecs update-service --cluster sql-rag-cluster --service sql-rag-retriever --force-new-deployment
```

## Teardown

```powershell
cdk destroy SqlRag-Ecs SqlRag-Data SqlRag-Network SqlRag-Ecr
```

`RemovalPolicy.RETAIN` is set on the ECR repository and the EFS filesystem (your built images and vectorstore survive stack deletion) — delete those manually if you really want everything gone.
