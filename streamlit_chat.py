import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any

import streamlit as st
from dotenv import load_dotenv
import traceback

# Ensure package imports work when run via `streamlit run`
ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from postgres_gpt.agent_langchain import run_chat
from postgres_gpt.agent_mcp_client import build_mcp_agent

load_dotenv()

st.set_page_config(page_title="Postgres Chat", page_icon="💬", layout="wide")

st.title("Postgres Chat")
st.caption("Talk to the database via LangChain tools or MCP server")

col_top, col_btn = st.columns([0.8, 0.2])
with col_btn:
    if st.button("🆕 New chat", use_container_width=True):
        st.session_state.history = []
        st.session_state.trace = []
        st.rerun()

# Sidebar settings
with st.sidebar:
    st.header("Connection")
    mode = st.radio("Agent mode", ["Direct (LangChain)", "MCP client"], index=0)
    model = st.text_input("Model", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    mcp_url_input = st.text_input("MCP URL", os.getenv("POSTGRES_MCP_URL", "http://localhost:8010/mcp"))
    mcp_url = mcp_url_input.replace("0.0.0.0", "localhost")
    st.markdown("---")
    st.markdown("Messages are processed per-send; this demo does not stream.")

if "history" not in st.session_state:
    st.session_state.history: List[Dict[str, str]] = []
if "trace" not in st.session_state:
    st.session_state.trace: List[Any] = []

# Render messages newest-first at the top
for msg in reversed(st.session_state.trace):
    role = getattr(msg, "type", "assistant")
    if role == "human":
        st.chat_message("user").write(msg.content)
    elif role == "tool":
        st.chat_message("assistant").markdown(f"`tool`: {msg.content}")
    else:
        st.chat_message("assistant").write(msg.content)

prompt = st.chat_input("Ask about your database…")

async def _run_direct(messages: List[Dict[str, str]], model_name: str):
    return await run_chat(messages, model=model_name)

async def _run_mcp(messages: List[Dict[str, str]], endpoint: str, model_name: str):
    runner = await build_mcp_agent(endpoint, model=model_name)
    return await runner(messages)

if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.spinner("Running agent…"):
        try:
            if mode.startswith("Direct"):
                outputs = asyncio.run(_run_direct(st.session_state.history, model))
            else:
                outputs = asyncio.run(_run_mcp(st.session_state.history, mcp_url, model))
            st.session_state.trace = outputs

            # Append the last AI message back into history for continuity
            ai_parts = [m.content for m in outputs if getattr(m, "type", "") == "ai"]
            if ai_parts:
                st.session_state.history.append({"role": "assistant", "content": "\n".join(ai_parts)})
        except Exception as exc:
            st.error(f"Error: {exc}")
            st.caption("See traceback below for details")
            st.code("\n".join(traceback.format_exception(exc)), language="text")
            st.stop()
    st.rerun()
