from collections import Counter
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI
import os
import threading
import yaml


SCHEMA_DIR = "./vectorstore/schema_db"
VALUE_DIR = "./vectorstore/value_db"
JOIN_DIR = "./vectorstore/join_db"
HF_TOKEN = os.getenv("HF_TOKEN")


def load_config(config_path="/app/config.yml"):
    if os.path.exists(config_path):
        with open(config_path, 'r') as file:
            data = yaml.safe_load(file)
            return data if data else {}
    print("❌ Config file NOT found!")
    return {}


def load_vectorstore():
    config = load_config()
    embedding_cfg = config.get("embedding", {})
    model_name = embedding_cfg.get(
        "model_name",
        "Qwen/Qwen3-Embedding-0.6B"
    )
    
    embeddings = HuggingFaceEmbeddings(model_name=model_name)

    schema_db = Chroma(
        persist_directory=SCHEMA_DIR,
        embedding_function=embeddings
    )

    value_db = Chroma(
        persist_directory=VALUE_DIR,
        embedding_function=embeddings
    )

    join_db = Chroma(
        persist_directory=JOIN_DIR,
        embedding_function=embeddings
    )

    print(f"📊 schema DB: {schema_db._collection.count()}")
    print(f"📊 value DB: {value_db._collection.count()}")
    print(f"📊 join DB: {join_db._collection.count()}")

    return schema_db, value_db, join_db


def build_retrievers(schema_db, value_db, join_db):
    config = load_config()
    llm_cfg = config.get("llm", {})
    llm = ChatOpenAI(
        model=llm_cfg.get("model_name", "nvidia/nemotron-3-super-120b-a12b:free"),
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0
    )
    return {
        "table": schema_db.as_retriever(
            search_kwargs={"k": 15, "filter": {"type": "table"}}
        ),
        "column": schema_db.as_retriever(
            search_kwargs={"k": 15, "filter": {"type": "column"}}
        ),
        "join_path": join_db.as_retriever(
            search_kwargs={"k": 5, "filter": {"type": "join_path"}}
        ),
        "value": value_db.as_retriever(
            search_kwargs={"k": 5, "filter": {"type": "value"}}
        ),
        "llm": llm,
    }


def print_docs(title, docs):
    print(f"\n{'=' * 60}")
    print(title)
    print(f"{'=' * 60}")

    for i, doc in enumerate(docs, start=1):
        print(f"\n--- DOC {i} ---")
        print("Metadata:")
        print(doc.metadata)

        print("\nContent:")
        print(doc.page_content[:1000])  # truncate if huge

    print()


def generate_hyde_query(question: str, llm) -> str:
    prompt = f"""A data analyst asks: "{question}"
Write one sentence describing the database table that would answer this.
Mention likely column names and what records it stores. Do not name a specific table."""
    return llm.invoke(prompt).content


def get_relevant_joins(table_names: list[str], join_db) -> list:
    result = join_db._collection.get(where={"type": "join_path"})
    all_join_docs = [
        Document(page_content=content, metadata=meta)
        for content, meta in zip(result["documents"], result["metadatas"])
    ]
    return [
        doc for doc in all_join_docs
        if any(t in doc.metadata.get("tables", "") for t in table_names)
    ]


def retrieve_context(question: str, retrievers: dict) -> str:
    db_lock = threading.Lock()

    # STEP 1: COLUMNS — run first against the raw question to gather independent votes
    with db_lock:
        raw_columns = retrievers["column"].invoke(question)
    column_votes = Counter(c.metadata.get("model") for c in raw_columns)
    print(f"🗳️ Column votes: {dict(column_votes)}")

    # STEP 2: TABLES — fetch k=15 via HyDE, then re-rank by column votes
    hyde_query = generate_hyde_query(question, retrievers["llm"])
    print(f"🔍 HyDE query: {hyde_query}")
    with db_lock:
        candidate_tables = retrievers["table"].invoke(hyde_query)

    candidate_tables.sort(
        key=lambda t: column_votes.get(t.metadata.get("model"), 0),
        reverse=True
    )
    tables = candidate_tables[:5]
    print_docs("TABLE RETRIEVAL (re-ranked)", tables)

    table_names = [t.metadata.get("model") for t in tables if t.metadata.get("model")]

    # STEP 3 (was 2): COLUMNS — filter to only those belonging to retrieved tables
    columns = [c for c in raw_columns if c.metadata.get("model") in table_names]
    print_docs("COLUMN RETRIEVAL (FILTERED)", columns)

    # STEP 4: JOINS (filtered by retrieved tables, not question similarity)
    joins = get_relevant_joins(table_names, retrievers["join_path"].vectorstore)
    print_docs("JOIN RETRIEVAL", joins)

    # STEP 5: VALUES
    with db_lock:
        values = []
        for table in table_names:
            hits = retrievers["value"].vectorstore.similarity_search(
                question, k=3, filter={"table": table}
            )
            values.extend(hits)

    # STEP 6: BUILD STRUCTURED CONTEXT
    context = []

    context.append("### TABLES")
    for d in tables:
        context.append(d.page_content.strip())

    context.append("\n### COLUMNS")
    for d in columns:
        context.append(d.page_content.strip())

    context.append("\n### JOINS")
    for d in joins:
        context.append(d.page_content.strip())

    context.append("\n### VALUES")
    for d in values:
        context.append(d.page_content.strip())

    return "\n\n".join(context)