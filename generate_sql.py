import logging
from typing import TypedDict
from langgraph.graph import StateGraph, END
import re
import os
import yaml

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


# =========================================================
# CONFIG
# =========================================================

def load_config(config_path="/app/config.yml"):
    if os.path.exists(config_path):
        with open(config_path, 'r') as file:
            data = yaml.safe_load(file)
            return data if data else {}
    logger.error("Config file not found at %s", config_path)
    return {}

config = load_config()
llm_config = config.get("llm", {})
platform_cfg = config.get("platform", {})
sql_dialect = platform_cfg.get("sql_dialect", "Presto SQL for AWS Athena")

llm = ChatOpenAI(
    model=llm_config.get(
        "model_name",
        "nvidia/nemotron-3-super-120b-a12b:free"
    ),
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=llm_config.get("temperature", 0)
)


# =========================================================
# STATE
# =========================================================

class State(TypedDict):
    question: str
    context: str
    sql: str
    error: str  # Added to track Athena errors
    history: list[str]


# =========================================================
# SQL GENERATION NODE
# =========================================================

def extract_sql(raw: str) -> str:
    # Split on blank lines and return the last block that starts with SELECT or WITH.
    # Guards against models that leak a preamble before the real SQL.
    blocks = re.split(r'\n\s*\n', raw.strip())
    for block in reversed(blocks):
        if re.match(r'^\s*(SELECT|WITH)\b', block, re.IGNORECASE):
            return block.strip()
    return raw.strip()


def generate_sql_node(state: State):
    history_text = "\n".join(state.get("history", []))
    error_context = ""

    if state.get("error"):
        error_context = f"""
        ### ERROR FROM PREVIOUS ATTEMPT:
        {state['error']}
        PLEASE FIX THE SQL BELOW:
        {state['sql']}
        """

    prompt = f"""
    ### INSTRUCTION:
    You are a SQL generator. Output ONLY raw SQL. Generate {sql_dialect}.
    Your response must begin immediately with the SQL keyword — WITH for CTEs, SELECT for simple queries.
    Never write SELECT before WITH. Do not write any preamble, reasoning, table list, or explanation before or after the SQL.
    Beware of the data type of columns when generating SQL.
    Use CAST(column AS type) whenever possible when generating SQL.
    Do not include markdown backticks or any explanatory text.
    You must only use the tables and columns provided in the SCHEMA section below. Do not invent tables or columns.
    {error_context}

    ### SCHEMA:
    {state.get('context', 'No schema provided')}

    ### CONVERSATION HISTORY:
    {history_text}

    ### QUESTION:
    {state['question']}

    ### SQL:
    """

    response = llm.invoke(prompt)
    sql = extract_sql(response.content)

    new_history = state.get("history", []) + [
        f"User: {state['question']}",
        f"SQL: {sql}"
    ]

    return {
        "sql": sql,
        "history": new_history,
        "error": ""
    }


# =========================================================
# BUILD GRAPH
# =========================================================

graph = StateGraph(State)

graph.add_node("generate_sql", generate_sql_node)

graph.set_entry_point("generate_sql")

app = graph.compile()


# =========================================================
# PUBLIC FUNCTION
# =========================================================

def run_reasoning(question: str, history=None, previous_sql="", error_msg="", context=""):
    return app.invoke({
        "question": question,
        "sql": previous_sql,
        "error": error_msg,
        "context": context,
        "history": history or []
    })


# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":
    import sys
    import logging as _logging
    _logging.basicConfig(stream=sys.stdout, level=_logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = run_reasoning("What are total sales last month?")
    logger.debug("SQL generation result: %s", result)