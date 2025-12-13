import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any
import time
from datetime import datetime

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

# Function definitions
def create_chat_controls():
    """Create chat control buttons and settings at the top."""
    col1, col2, col3, col4, col5 = st.columns([2, 2, 2, 2, 2])

    with col1:
        if st.button("🆕 New Chat", help="Start a new conversation", key="chat_new_btn"):
            st.session_state.history = []
            st.session_state.trace = []
            st.session_state.processing_times = []
            st.rerun()

    with col2:
        if st.button("🗑️ Clear Chat", help="Clear current conversation", key="chat_clear_btn"):
            st.session_state.history = []
            st.session_state.trace = []
            st.session_state.processing_times = []
            st.rerun()

    with col3:
        # Toggle for showing tool outputs
        show_tool_outputs = st.session_state.get('show_tool_outputs', True)
        if st.checkbox("🔧 Show Tool Outputs", value=show_tool_outputs, key="tool_outputs_toggle"):
            st.session_state['show_tool_outputs'] = True
        else:
            st.session_state['show_tool_outputs'] = False

    with col4:
        # Toggle for showing processing times
        show_processing_times = st.session_state.get('show_processing_times', True)
        if st.checkbox("⏱️ Show Processing Times", value=show_processing_times, key="processing_times_toggle"):
            st.session_state['show_processing_times'] = True
        else:
            st.session_state['show_processing_times'] = False

    with col5:
        if st.button("📤 Export Chat", help="Export conversation as JSON", key="chat_export_btn"):
            export_current_chat()

def display_chat_messages_reversed():
    """Display chat messages in reverse order (latest at top) with tool output toggle and processing times."""
    messages = st.session_state.get("history", [])
    trace = st.session_state.get("trace", [])
    processing_times = st.session_state.get("processing_times", [])

    if not messages:
        st.info("👋 Start a conversation! Ask me anything about your database.")
        st.markdown("**Example queries:**")
        st.markdown("- List all tables in the database")
        st.markdown("- Describe the actor table")
        st.markdown("- Show me 5 sample records from the film table")
        return

    # Get display settings
    show_tool_outputs = st.session_state.get('show_tool_outputs', True)
    show_processing_times = st.session_state.get('show_processing_times', True)

    # Combine messages and trace for display
    combined_messages = []

    # Add user messages
    for i, msg in enumerate(messages):
        if msg["role"] == "user":
            combined_messages.append({
                "type": "user",
                "content": msg["content"],
                "timestamp": datetime.now().isoformat(),  # Placeholder timestamp
                "processing_time": processing_times[i] if i < len(processing_times) else None
            })
        elif msg["role"] == "assistant":
            combined_messages.append({
                "type": "assistant",
                "content": msg["content"],
                "timestamp": datetime.now().isoformat(),  # Placeholder timestamp
                "processing_time": processing_times[i] if i < len(processing_times) else None
            })

    # Add tool messages from trace
    for msg in trace:
        if getattr(msg, "type", "") == "tool" and show_tool_outputs:
            combined_messages.append({
                "type": "tool",
                "content": getattr(msg, "content", ""),
                "timestamp": datetime.now().isoformat(),  # Placeholder timestamp
            })

    # Display messages in REVERSE chronological order (latest first)
    for i, message in enumerate(reversed(combined_messages)):
        # Calculate the original index for unique keys
        original_index = len(combined_messages) - 1 - i

        if message["type"] == "user":
            display_user_message_reversed(message, original_index, show_processing_times)
        elif message["type"] == "assistant":
            display_assistant_message_reversed(message, original_index, show_tool_outputs, show_processing_times)
        elif message["type"] == "tool" and show_tool_outputs:
            display_tool_message_reversed(message, original_index)

def display_user_message_reversed(message: Dict, index: int, show_processing_times: bool):
    """Display a user message with copy functionality and processing time."""
    # User message styling with timestamp at top
    timestamp = get_formatted_timestamp(message.get('timestamp', ''))
    processing_time = message.get('processing_time')

    # Build the header info
    header_parts = [timestamp]
    if show_processing_times and processing_time:
        header_parts.append(f"⏱️ {processing_time:.2f}s")

    header_text = " | ".join(filter(None, header_parts))

    st.markdown(
        f"""
        <div style="
            background-color: #e3f2fd;
            padding: 10px 15px;
            border-radius: 10px;
            margin: 5px 0 10px 20%;
            border-left: 4px solid #2196f3;
        ">
            <div style="font-size: 0.8em; color: #666; margin-bottom: 5px;">{header_text}</div>
            <strong>👤 You:</strong><br>
            {message['content']}
        </div>
        """,
        unsafe_allow_html=True
    )

    # Copy button for user message
    col1, col2, col3 = st.columns([4, 1, 1])
    with col2:
        if st.button("📋 Copy", key=f"copy_user_{index}", help="Copy message"):
            st.success("Copied!", icon="✅")

def display_assistant_message_reversed(message: Dict, index: int, show_tool_outputs: bool, show_processing_times: bool):
    """Display an assistant message with copy functionality, optional tool details, and processing time."""
    # Assistant message styling with timestamp at top
    content = message.get('content', '')
    timestamp = get_formatted_timestamp(message.get('timestamp', ''))
    processing_time = message.get('processing_time')

    # Build the header info
    header_parts = [timestamp]
    if show_processing_times and processing_time:
        # Color code processing time based on duration
        if processing_time < 2:
            time_color = "#4caf50"  # Green for fast
        elif processing_time < 5:
            time_color = "#ff9800"  # Orange for medium
        else:
            time_color = "#f44336"  # Red for slow

        header_parts.append(f'<span style="color: {time_color};">⏱️ {processing_time:.2f}s</span>')

    header_text = " | ".join(filter(None, header_parts))

    st.markdown(
        f"""
        <div style="
            background-color: #f3e5f5;
            padding: 10px 15px;
            border-radius: 10px;
            margin: 5px 20% 10px 0;
            border-left: 4px solid #9c27b0;
        ">
            <div style="font-size: 0.8em; color: #666; margin-bottom: 5px;">{header_text}</div>
            <strong>🤖 Assistant:</strong><br>
            {content}
        </div>
        """,
        unsafe_allow_html=True
    )

    # Action buttons row
    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])

    with col2:
        if st.button("📋 Copy", key=f"copy_assistant_{index}", help="Copy response"):
            st.success("Copied!", icon="✅")

def display_tool_message_reversed(message: Dict, index: int):
    """Display a tool execution message when tool outputs are enabled."""
    timestamp = get_formatted_timestamp(message.get('timestamp', ''))
    tool_output = message.get('content', '')

    # Build header
    header_text = timestamp

    # Truncate long outputs for display
    display_output = tool_output[:300] + "..." if len(tool_output) > 300 else tool_output

    st.markdown(
        f"""
        <div style="
            background-color: #fff3e0;
            padding: 8px 12px;
            border-radius: 8px;
            margin: 3px 10% 8px 10%;
            border-left: 3px solid #ff9800;
            font-size: 0.9em;
        ">
            <div style="font-size: 0.7em; color: #666; margin-bottom: 3px;">{header_text}</div>
            <strong>🔧 Tool Result:</strong><br>
            <div style="font-family: monospace; white-space: pre-wrap; max-height: 100px; overflow-y: auto;">
            {display_output}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Show full output button
    col1, col2, col3 = st.columns([4, 1, 1])
    with col2:
        if st.button("📄 Full", key=f"tool_full_{index}", help="Show full tool output"):
            st.session_state[f"show_full_tool_{index}"] = not st.session_state.get(f"show_full_tool_{index}", False)
            st.rerun()

    # Show full output if requested
    if st.session_state.get(f"show_full_tool_{index}", False):
        with st.expander(f"Full Tool Output", expanded=True):
            st.code(tool_output, language="text")

def get_formatted_timestamp(timestamp_str: str) -> str:
    """Format timestamp for display."""
    if not timestamp_str:
        return ""

    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return dt.strftime('%H:%M:%S')
    except:
        return ""

def export_current_chat():
    """Export the current chat conversation as JSON."""
    messages = st.session_state.get("history", [])
    trace = st.session_state.get("trace", [])

    export_data = {
        "conversation": messages,
        "trace": [str(msg) for msg in trace],
        "exported_at": datetime.now().isoformat()
    }

    json_str = json.dumps(export_data, indent=2)
    st.download_button(
        label="Download Chat JSON",
        data=json_str,
        file_name="chat_export.json",
        mime="application/json",
        key="download_chat"
    )

def create_chat_input():
    """Create the chat input area."""
    # Chat input form
    with st.form("chat_form", clear_on_submit=True):
        col1, col2 = st.columns([5, 1])

        with col1:
            user_input = st.text_area(
                "Type your message here...",
                height=100,
                placeholder="Ask me about the database, request SQL queries, or explore available tools...",
                key="chat_input"
            )

        with col2:
            st.markdown("<br>", unsafe_allow_html=True)  # Add some spacing
            submit_button = st.form_submit_button(
                "📤 Send",
                type="primary",
                use_container_width=True
            )

        # Process the message when submitted
        if submit_button and user_input.strip():
            process_chat_message(user_input.strip())

def process_chat_message(user_input: str):
    """Process a chat message from the user with processing time tracking."""
    # Record start time for processing
    start_time = time.time()

    # Add user message to chat history
    st.session_state.history.append({"role": "user", "content": user_input})

    # Process the message
    with st.spinner("🤖 AI is thinking..."):
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

            # Calculate and store processing time
            end_time = time.time()
            processing_time = end_time - start_time
            st.session_state.processing_times.append(processing_time)

        except Exception as exc:
            st.error(f"Error: {exc}")
            st.caption("See traceback below for details")
            st.code("\n".join(traceback.format_exception(exc)), language="text")
            st.stop()
    st.rerun()

async def _run_direct(messages: List[Dict[str, str]], model_name: str):
    return await run_chat(messages, model=model_name)

async def _run_mcp(messages: List[Dict[str, str]], endpoint: str, model_name: str):
    runner = await build_mcp_agent(endpoint, model=model_name)
    return await runner(messages)

# Main UI starts here
st.title("Postgres Chat")
st.caption("Talk to the database via LangChain tools or MCP server")

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
if "processing_times" not in st.session_state:
    st.session_state.processing_times: List[float] = []

# Chat controls at the top
create_chat_controls()

# Main chat interface
st.markdown("### 💬 AI Chat Interface")

# Chat container with fixed height and scrolling
chat_container = st.container()

with chat_container:
    # Chat messages area with scrolling - REVERSED ORDER
    messages_container = st.container(height=500, border=True)

    with messages_container:
        display_chat_messages_reversed()

    # Chat input area
    create_chat_input()

# Add separator
st.markdown("---")

# Tools section at the bottom
st.markdown("### 🧰 Available Tools")
st.info("Tools are loaded dynamically based on the selected agent mode. Switch modes to see different tool sets.")
