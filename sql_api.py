from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional
from generate_sql import run_reasoning

app = FastAPI()

sessions = {}


class QueryRequest(BaseModel):
    question: str
    context: str
    session_id: str
    error_msg: Optional[str] = None
    wrong_sql: Optional[str] = None
    history: Optional[list[str]] = None


@app.post("/query")
async def query(req: QueryRequest):
    session = sessions.get(req.session_id)

    if not session:
        session = {
            "history": [],
            "sql": "",
            "context": req.context
        }

    history = req.history if req.history is not None else session["history"]

    result = await run_in_threadpool(
        run_reasoning,
        req.question,
        history,
        req.wrong_sql or session["sql"],
        req.error_msg or "",
        req.context
    )

    # update session
    session["history"] = result["history"]
    session["sql"] = result["sql"]

    sessions[req.session_id] = session

    raw_output = result["sql"]

    stripped = raw_output.upper().lstrip()
    if stripped.startswith("SELECT") or stripped.startswith("WITH"):
        sql = raw_output.split(';')[0].replace("```sql", "").replace("```", "").strip()
    else:
        raise HTTPException(
            status_code=500,
            detail=f"The AI failed to build a query. It said: {raw_output}"
        )
    # ------------------------------------

    return {
        "sql": sql,
        "session_id": req.session_id
    }