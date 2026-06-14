from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import yaml

from adapters import get_adapter

app = FastAPI()


def load_config(config_path="/app/config.yml"):
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
            return data if data else {}
    return {}


config = load_config()
adapter = get_adapter(config)


class QueryRequest(BaseModel):
    sql: str
    session_id: str


def validate_query(sql: str) -> str:
    forbidden = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER"]
    for keyword in forbidden:
        if keyword in sql.upper():
            raise HTTPException(status_code=400, detail=f"Forbidden keyword detected: {keyword}")
    if "LIMIT" not in sql.upper():
        sql += " LIMIT 1000"
    return sql


@app.post("/execute")
def run_query(req: QueryRequest):
    try:
        sql = validate_query(req.sql)
        results = adapter.execute_query(sql)
        return {"session_id": req.session_id, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
