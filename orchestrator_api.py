from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import httpx
import uuid
import os

app = FastAPI()


class GenerateSQLRequest(BaseModel):
    question: str
    session_id: str | None = None
    history: List[str] = []

RETRIEVER_URL = "http://retriever:8000/retrieve"
SQL_GENERATOR_URL = "http://sql-generator:8001/query"
SQL_EXECUTOR_URL = os.getenv("SQL_EXECUTOR_URL", "http://sql-executor:8002/execute")

# --- Phase 1: Generate SQL ---
@app.post("/generate-sql")
async def generate_sql(req: GenerateSQLRequest):
    session_id = req.session_id or str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            # Step 1: Retrieve context
            r = await client.post(RETRIEVER_URL, json={"question": req.question})
            r.raise_for_status()
            context = r.json()["context"]

            # Step 2: Generate SQL
            s = await client.post(SQL_GENERATOR_URL, json={
                "question": req.question,
                "context": context,
                "session_id": session_id,
                "history": req.history
            })
            s.raise_for_status()
            sql = s.json().get("sql")

            if not sql:
                raise HTTPException(status_code=500, detail="SQL generation failed")

            return {
                "question": req.question,
                "sql": sql,
                "context": context,  # Essential for self-correction loop
                "session_id": session_id
            }

        except httpx.RequestError as e:
            raise HTTPException(status_code=500, detail=f"Service communication error: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=500, detail=f"Upstream error: {e.response.text}")

# --- Phase 2: Execute SQL ---
@app.post("/execute-sql")
async def execute_sql(sql: str, session_id: str, question: str, context: str):
    async with httpx.AsyncClient(timeout=300.0) as client:
        a = await client.post(SQL_EXECUTOR_URL, json={"sql": sql, "session_id": session_id})

        if a.status_code == 200:
            return {"status": "success", "sql": sql, "result": a.json()}

        error_detail = a.text[:1000]

        try:
            correction_res = await client.post(SQL_GENERATOR_URL, json={
                "question": question,
                "context": context,
                "session_id": session_id,
                "error_msg": error_detail,
                "wrong_sql": sql
            })
            correction_res.raise_for_status()
            corrected_sql = correction_res.json().get("sql")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Execution failed and self-correction also failed: {str(e)}")

        return {"status": "correction_needed", "sql": corrected_sql, "error": error_detail}
