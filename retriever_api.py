from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from knowledge_retriever import load_vectorstore, build_retrievers, retrieve_context

db = None
retrievers = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global schema_db, value_db, join_db, retrievers
    schema_db, value_db, join_db = load_vectorstore()
    retrievers = build_retrievers(schema_db, value_db, join_db)
    yield
    # optional cleanup
    try:
        schema_db.persist()
        value_db.persist()
        join_db.persist()
    except:
        pass


app = FastAPI(lifespan=lifespan)


class QueryRequest(BaseModel):
    question: str


@app.post("/retrieve")
async def retrieve(req: QueryRequest):
    context = await run_in_threadpool(
        retrieve_context, req.question, retrievers
    )

    return {
        "question": req.question,
        "context": context
    }