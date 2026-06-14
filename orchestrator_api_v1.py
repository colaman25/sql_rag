from fastapi import FastAPI, HTTPException
import httpx
import uuid

app = FastAPI()

RETRIEVER_URL = "http://retriever:8000/retrieve"
SQL_GENERATOR_URL = "http://sql-generator:8001/query"
ATHENA_URL = "http://athena-executor:8002/execute"


@app.post("/query")
async def query(question: str, session_id: str | None = None):
    
    session_id = session_id or str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            # Step 1: Retrieve context
            r = await client.post(RETRIEVER_URL, json={"question": question})
            r.raise_for_status()
            context = r.json()["context"]

            # Step 2: Generate SQL
            s = await client.post(SQL_GENERATOR_URL, json={
                "question": question,
                "context": context,
                "session_id": session_id
            })
            s.raise_for_status()
            sql = s.json().get("sql")

            if not sql:
                raise HTTPException(status_code=500, detail="SQL generation failed")

            # Step 3: Execute SQL
            a = await client.post(ATHENA_URL, json={"sql": sql, "session_id": session_id})
            a.raise_for_status()
            result = a.json()

            return {
                "question": question,
                "sql": sql,
                "result": result
            }

        except httpx.RequestError as e:
            raise HTTPException(status_code=500, detail=f"Service communication error: {str(e)}")

        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=500, detail=f"Upstream error: {e.response.text}")