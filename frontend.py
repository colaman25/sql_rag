import streamlit as st
import requests
import uuid
import os


def build_sql_history(messages):
    history = []
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg["content"], str):
            history.append(f"User: {msg['content']}")
        elif "sql" in msg:
            history.append(f"SQL: {msg['sql']}")
            if msg.get("sql_status") == "cancelled":
                history.append("NOTE: User rejected this SQL as incorrect")
    return history

ORCHESTRATOR_URL = os.getenv("QUERY_SERVICE_URL", "http://localhost:8003")

st.set_page_config(page_title="Data Assistant", layout="wide")

# Initialize session state
if "messages" not in st.session_state: st.session_state.messages = []
if "session_id" not in st.session_state: st.session_state.session_id = str(uuid.uuid4())
if "pending_sql" not in st.session_state: st.session_state.pending_sql = None
if "last_context" not in st.session_state: st.session_state.last_context = ""
if "last_question" not in st.session_state: st.session_state.last_question = ""


def start_new_session():
    st.session_state.messages = []
    st.session_state.pending_sql = None
    st.session_state.last_context = ""
    st.session_state.last_question = ""
    st.session_state.session_id = str(uuid.uuid4())



st.title("📊 SQL Assistant")

# Reset Session Button
col1, col2 = st.columns([5, 1])

with col2:
    if st.button("🆕 New Session"):
        start_new_session()
        st.rerun()

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if isinstance(msg["content"], list): st.dataframe(msg["content"])
        else: st.markdown(msg["content"])

# 1. Chat Input
if prompt := st.chat_input("Enter your question..."):
    if st.session_state.pending_sql:
        st.session_state.messages.append({"role": "assistant", "content": f"Ignored SQL:\n```sql\n{st.session_state.pending_sql}\n```", "sql": st.session_state.pending_sql, "sql_status": "cancelled"})
        st.session_state.pending_sql = None

    history = build_sql_history(st.session_state.messages)
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.spinner("Generating SQL..."):
        try:
            res = requests.post(
                f"{ORCHESTRATOR_URL}/generate-sql",
                json={"question": prompt, "session_id": st.session_state.session_id, "history": history}
            )
            res.raise_for_status()
            data = res.json()
            
            st.session_state.pending_sql = data["sql"]
            st.session_state.last_question = data["question"] # Use question from response
            st.session_state.last_context = data["context"]   # Use context from response
            st.rerun()
        except Exception as e:
            st.error(f"Generation error: {e}")


# 2. Approval UI (Only shows if we have a pending SQL query)
if st.session_state.pending_sql:
    with st.chat_message("assistant"):
        st.markdown(f"I've generated this SQL. Please approve to run:\n```sql\n{st.session_state.pending_sql}\n```")
        
        col1, col2 = st.columns(2)
        if col1.button("✅ Approve and Run"):
            with st.spinner("Executing query..."):
                try:
                    exec_res = requests.post(
                        f"{ORCHESTRATOR_URL}/execute-sql",
                        params={
                            "sql": st.session_state.pending_sql,
                            "session_id": st.session_state.session_id,
                            "question": st.session_state.last_question,
                            "context": st.session_state.last_context
                        }
                    )
                    exec_res.raise_for_status()
                    resp = exec_res.json()

                    if resp.get("status") == "correction_needed":
                        st.session_state.messages.append({"role": "assistant", "content": f"Execution failed:\n```\n{resp['error']}\n```\nHere is a corrected query for your approval:", "sql": st.session_state.pending_sql, "sql_status": "cancelled"})
                        st.session_state.pending_sql = resp["sql"]
                        st.rerun()
                    else:
                        rows = resp.get("result", {}).get("data", {}).get("rows", [])
                        st.session_state.messages.append({"role": "assistant", "content": f"Executed SQL:\n```sql\n{st.session_state.pending_sql}\n```", "sql": st.session_state.pending_sql, "sql_status": "executed"})
                        st.session_state.messages.append({"role": "assistant", "content": rows})
                        st.session_state.pending_sql = None
                        st.rerun()
                except Exception as e:
                    st.error(f"Execution error: {e}")

        if col2.button("❌ Cancel"):
            st.session_state.messages.append({"role": "assistant", "content": f"Cancelled SQL:\n```sql\n{st.session_state.pending_sql}\n```", "sql": st.session_state.pending_sql, "sql_status": "cancelled"})
            st.session_state.pending_sql = None
            st.rerun()
