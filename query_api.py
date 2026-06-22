import logging
import sys
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
import httpx
import uuid
import os
import yaml

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

from generate_sql import run_reasoning
from adapters import get_adapter
from session_store import get_session_store


app = FastAPI()

RETRIEVER_URL = os.getenv("RETRIEVER_URL")
if not RETRIEVER_URL:
    raise RuntimeError("RETRIEVER_URL environment variable is not set")


def load_config(config_path="/app/config.yml"):
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
            return data if data else {}
    return {}


config = load_config()
adapter = get_adapter(config)
sessions = get_session_store()


class GenerateSQLRequest(BaseModel):
    question: str
    session_id: str | None = None
    history: list[str] = []


FORBIDDEN_KEYWORDS = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER"]


def validate_query(sql: str) -> str:
    for keyword in FORBIDDEN_KEYWORDS:
        if keyword in sql.upper():
            raise HTTPException(status_code=400, detail=f"Forbidden keyword detected: {keyword}")
    if "LIMIT" not in sql.upper():
        sql += " LIMIT 1000"
    return sql


def _run_sql_generation(
    question: str,
    history: list | None,
    wrong_sql: str,
    error_msg: str,
    context: str,
    session_id: str,
) -> str:
    session = sessions.get(session_id)
    if not session:
        session = {"history": [], "sql": "", "context": context}

    use_history = history if history is not None else session["history"]

    result = run_reasoning(
        question,
        use_history,
        wrong_sql or session["sql"],
        error_msg or "",
        context,
    )

    session["history"] = result["history"]
    session["sql"] = result["sql"]
    sessions.set(session_id, session)

    raw = result["sql"]
    if raw.upper().lstrip().startswith(("SELECT", "WITH")):
        return raw.split(";")[0].replace("```sql", "").replace("```", "").strip()

    raise HTTPException(
        status_code=500,
        detail=f"The AI failed to build a query. It said: {raw}",
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/generate-sql")
async def generate_sql(req: GenerateSQLRequest):
    session_id = req.session_id or str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.post(RETRIEVER_URL, json={"question": req.question})
            r.raise_for_status()
            context = r.json()["context"]
        except httpx.RequestError as e:
            raise HTTPException(status_code=500, detail=f"Retriever unavailable: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=500, detail=f"Retriever error: {e.response.text}")

    sql = await run_in_threadpool(
        _run_sql_generation, req.question, req.history, "", "", context, session_id
    )

    return {
        "question": req.question,
        "sql": sql,
        "context": context,
        "session_id": session_id,
    }


@app.post("/execute-sql")
async def execute_sql(sql: str, session_id: str, question: str, context: str):
    # Received as query params — matches existing frontend contract
    validated_sql = validate_query(sql)

    try:
        result = await run_in_threadpool(adapter.execute_query, validated_sql)
        return {"status": "success", "sql": validated_sql, "result": {"data": result}}
    except Exception as e:
        error_detail = str(e)[:1000]

    # Self-correction
    try:
        corrected_sql = await run_in_threadpool(
            _run_sql_generation, question, None, sql, error_detail, context, session_id
        )
        return {"status": "correction_needed", "sql": corrected_sql, "error": error_detail}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Execution failed and self-correction also failed: {str(e)}",
        )
