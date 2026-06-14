import pandas as pd
import json
import re
import shutil
import os
import requests
import yaml
import time
from collections import defaultdict
from langchain_core.documents import Document
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma


DBT_PROJECT_PATH = os.getenv("DBT_PROJECT_PATH")
SQL_EXECUTOR_URL = os.getenv("SQL_EXECUTOR_URL", "http://sql-executor:8002/execute")


def load_config(config_path="/app/config.yml"):
    if os.path.exists(config_path):
        with open(config_path, 'r') as file:
            data = yaml.safe_load(file)
            return data if data else {}
    print("❌ Config file NOT found!")
    return {}


def load_manifest(path="target/manifest.json"):
    with open(path) as f:
        return json.load(f)


def get_table_joins(manifest, table_id):
    joins = []
    for node in manifest["nodes"].values():
        if node["resource_type"] == "test" and node.get("attached_node") == table_id:
            meta = node.get("test_metadata", {})
            if meta.get("name") == "relationships":
                fk_col = meta["kwargs"].get("column_name")
                to_table = meta["kwargs"].get("to")
                to_col = meta["kwargs"].get("field")
                joins.append(f"Column '{fk_col}' joins to {to_table} on '{to_col}'")
    return joins


def extract_ref_name(to_value):
    """Extract table name from a dbt ref() string, including Jinja-wrapped ones."""
    if not to_value:
        return None
    match = re.search(r"ref\(['\"]([^'\"]+)['\"]\)", to_value)
    return match.group(1) if match else to_value


def dbt_to_documents(manifest, default_catalog="AWSDataCatalog"):
    docs = []
    rel_map = defaultdict(list)

    # 1. Pre-process relationship tests to build a Join Map
    for node in manifest["nodes"].values():
        if node.get("resource_type") == "test":
            meta = node.get("test_metadata", {})

            if meta.get("name") == "relationships":
                kwargs = meta.get("kwargs", {})

                sources = node.get("sources", [])
                if sources and len(sources) > 0:
                    origin_table = extract_ref_name(sources[0][1])
                else:
                    origin_table = extract_ref_name(kwargs.get("model"))

                fk_col = node.get("column_name")
                to_table = extract_ref_name(kwargs.get("to"))
                to_col = kwargs.get("field")

                if origin_table and to_table:
                    rel_map[origin_table].append((fk_col, to_table, to_col))

    # 2. Generate Documents for both Models and Sources
    for node in manifest["nodes"].values():
        if node["resource_type"] not in ["model", "source"]:
            continue

        model_name = node["name"]
        actual_database = node.get("database") or default_catalog
        actual_schema = node.get("schema", "dev")
        full_table_path = f"{actual_database}.{actual_schema}.{model_name}"
        description = node.get("description", "")
        columns = node.get("columns", {})

        prefix = model_name.split("_")[0] if "_" in model_name else ""
        layer_labels = {
            "src": "src (raw source table — use for simple, direct queries)",
            "stg": "stg (staging/cleaned source data)",
            "dim": "dim (dimension table — descriptive attributes)",
            "fct": "fct (fact table — aggregated or event-level metrics, use for analytical queries)",
            "int": "int (intermediate model — avoid unless specifically needed)",
            "mart": "mart (data mart — pre-built analytical output)",
        }
        layer = layer_labels.get(prefix, prefix or "unknown")

        found_joins = rel_map.get(model_name, [])
        join_context = "\n".join(
            f"- Column '{fk}' joins to table '{to_t}' on column '{to_c}'"
            for fk, to_t, to_c in found_joins
        ) or "No explicit joins defined."

        docs.append(Document(
            page_content=f"""
            Table Path: {full_table_path}
            Table Name: {model_name}
            Layer: {layer}
            Database: {actual_database}
            Schema: {actual_schema}

            Description:
            {description}

            Relationships / Join Logic:
            {join_context}

            Columns:
            {", ".join(columns.keys())}
            """,
            metadata={
                "type": "table",
                "full_path": full_table_path,
                "model": model_name,
                "layer": prefix or "unknown"
            }
        ))

        for col, col_data in columns.items():
            col_joins = [(fk, to_t, to_c) for fk, to_t, to_c in found_joins if fk == col]
            join_hint = (
                f"Note: Column '{col}' joins to table '{col_joins[0][1]}' on column '{col_joins[0][2]}'"
                if col_joins else ""
            )

            docs.append(Document(
                page_content=f"""
                Column: {col}
                Parent Table: {full_table_path}
                {join_hint}

                Description:
                {col_data.get("description", "No description provided.")}
                """,
                metadata={
                    "type": "column",
                    "source": "dbt",
                    "database": actual_database,
                    "schema": actual_schema,
                    "model": model_name,
                    "column": col,
                    "full_table_path": full_table_path
                }
            ))

    return docs, rel_map


def build_path_docs(config):
    path_docs = []
    paths = config.get("common_paths", [])

    for p in paths:
        path_docs.append(Document(
            page_content=f"""
            Join Path: {p['path']}
            SQL Logic: {p['logic']}
            When to use: {p['description']}
            """,
            metadata={
                "type": "join_path",
                "tables": p['path']
            }
        ))
    return path_docs


def build_path_docs_from_rel_map(rel_map):
    docs = []
    for origin_table, joins in rel_map.items():
        for fk_col, to_table, to_col in joins:
            docs.append(Document(
                page_content=f"""Join Path: {origin_table} -> {to_table}
SQL Logic: JOIN {to_table} ON {origin_table}.{fk_col} = {to_table}.{to_col}
When to use: Link {origin_table} to {to_table} via {fk_col}""",
                metadata={
                    "type": "join_path",
                    "tables": f"{origin_table} -> {to_table}"
                }
            ))
    return docs


def wait_for_executor(url=SQL_EXECUTOR_URL, timeout=60):
    print(f"⏳ Waiting for {url} to be ready...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            requests.get(url.replace("/execute", "/docs"))
            print("✅ Executor is up!")
            return True
        except requests.exceptions.ConnectionError:
            time.sleep(2)
    raise Exception("Timeout: SQL executor never started.")


def get_sample_values(database, table, column, limit=200):
    sql = f'SELECT DISTINCT "{column}" FROM "{database}"."{table}" WHERE "{column}" IS NOT NULL LIMIT {limit}'
    payload = {"sql": sql, "session_id": "vector_build_session"}
    try:
        print(f"📡 Requesting samples for {table}.{column}...")
        response = requests.post(SQL_EXECUTOR_URL, json=payload)
        response.raise_for_status()
        rows = response.json().get("data", {}).get("rows", [])
        return [list(row.values())[0] for row in rows if row.values()]
    except Exception as e:
        print(f"⚠️ Failed to fetch samples for {table}.{column}: {e}")
        return []


def build_semantic_docs(config, database):
    semantic_docs = []
    print(f"DEBUG: Processing config for tables: {list(config.keys())}")
    for table, columns in config.items():
        for col in columns:
            samples = get_sample_values(database, table, col)
            print(f"DEBUG: Found {len(samples)} samples for {table}.{col}")

            for val in samples:
                semantic_docs.append(Document(
                    page_content=f"Value: {val}\nTable: {table}\nColumn: {col}",
                    metadata={
                        "type": "value",
                        "table": table,
                        "column": col,
                        "value": str(val)
                    }
                ))
    return semantic_docs


config = load_config('/app/config.yml')
platform_cfg = config.get("platform", {})
default_catalog = platform_cfg.get("catalog", "AWSDataCatalog")
platform_database = platform_cfg.get("database") or os.getenv("ATHENA_DATABASE", "dev")

semantic_config = config.get("semantic_dictionary", {})
embedding_config = config.get("embedding", {})
embedding_model = embedding_config.get(
    "model_name",
    "Qwen/Qwen3-Embedding-0.6B"
)

manifest = load_manifest("/dbt/target/manifest.json")

dbt_docs, rel_map = dbt_to_documents(manifest, default_catalog=default_catalog)

wait_for_executor()

semantic_docs = build_semantic_docs(semantic_config, platform_database)

path_docs = build_path_docs(config)
rel_map_path_docs = build_path_docs_from_rel_map(rel_map)

schema_docs = [
    d for d in dbt_docs
    if d.metadata["type"] in ["table", "column"]
]

value_docs = [
    d for d in semantic_docs
    if d.metadata["type"] == "value"
]

join_docs = path_docs + rel_map_path_docs

print("📦 DBT docs:", len(dbt_docs))
print("📦 Semantic docs:", len(semantic_docs))
print("📦 Path docs (config):", len(path_docs))
print("📦 Path docs (schema FK):", len(rel_map_path_docs))

SCHEMA_DIR = "./vectorstore/schema_db"
VALUE_DIR = "./vectorstore/value_db"
JOIN_DIR = "./vectorstore/join_db"

for d in [SCHEMA_DIR, VALUE_DIR, JOIN_DIR]:
    if os.path.exists(d):
        shutil.rmtree(d)

HF_TOKEN = os.getenv("HF_TOKEN")

embeddings = HuggingFaceEmbeddings(model_name=embedding_model)

schema_vs = Chroma.from_documents(
    documents=schema_docs,
    embedding=embeddings,
    persist_directory=SCHEMA_DIR
)
schema_vs.persist()

value_vs = Chroma.from_documents(
    documents=value_docs,
    embedding=embeddings,
    persist_directory=VALUE_DIR
)
value_vs.persist()

join_vs = Chroma.from_documents(
    documents=join_docs,
    embedding=embeddings,
    persist_directory=JOIN_DIR
)
join_vs.persist()

with open("/app/vectorstore/.ready", "w") as f:
    f.write("done")
