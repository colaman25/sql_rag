import logging
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from knowledge_retriever import load_vectorstore, build_retrievers, retrieve_context

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

db = None
retrievers = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global schema_db, value_db, join_db, retrievers
    schema_db, value_db, join_db = load_vectorstore()
    if schema_db._collection.count() == 0:
        logger.warning("Vector store is empty — run the indexer before querying: docker compose run --rm vector-builder")
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


@app.get("/health")
async def health():
    if schema_db is None or schema_db._collection.count() == 0:
        raise HTTPException(status_code=503, detail="Vector store not loaded")
    return {"status": "ok"}


@app.post("/retrieve")
async def retrieve(req: QueryRequest):
    context = await run_in_threadpool(
        retrieve_context, req.question, retrievers
    )

    return {
        "question": req.question,
        "context": context
    }