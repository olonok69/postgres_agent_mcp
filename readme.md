# Postgres MCP + LangGraph

Agentic Postgres assistant with two entrypoints: (1) a LangGraph ReAct agent that calls database tools directly, and (2) the same agent running over a streamable HTTP MCP server. A Streamlit UI fronts both modes for interactive chat.

## Architecture
- `server4.py` — Streamable HTTP MCP server (FastMCP + Starlette + uvicorn) exposing four Postgres tools: `list_tables`, `describe_table`, `get_table_sample`, and `execute_sql`.
- `db.py` — Async Postgres access (asyncpg pool lifecycle, table metadata, sampling, raw SQL execution).
- `agent_langchain.py` — LangGraph ReAct agent wiring the four tools directly (no MCP hop) using `ChatOpenAI`.
- `agent_mcp_client.py` — LangGraph agent that fetches MCP tools over SSE transport and wraps them as LangChain tools.
- `streamlit_chat.py` — Web UI to chat with the DB, selectable mode (direct vs MCP), model picker, simple per-send execution (no streaming yet).
- `smoke_mcp.py` — CLI smoke test against the MCP server (init, list tools, list tables, describe/sample first table).

## Data flow
1) Streamlit gathers chat history ➝ dispatches to either direct agent or MCP-backed agent.
2) Direct agent calls tool coroutines in-process via LangGraph; MCP agent shells tool calls through the SSE client to the MCP server.
3) Server tools delegate to `db.py`, which uses an asyncpg pool bound to the uvicorn event loop.
4) Results are serialized to JSON/text and surfaced back through LangChain messages.

## Requirements
- Python 3.10+
- Postgres reachable with credentials provided via env vars
- OpenAI API key for `ChatOpenAI` models
- Dependencies in `requirements.txt` (`asyncpg`, `uvicorn`, `starlette`, `langchain/langgraph`, `mcp`, `streamlit`, etc.)

## Configuration
Set env vars for local DB and server/agent defaults:

```
PGHOST=localhost
PGPORT=5432
PGUSER=...            # or POSTGRES_USER
PGPASSWORD=...        # or POSTGRES_PASSWORD
PGDATABASE=...        # or POSTGRES_DB
PGSSL=false           # set to true/require for TLS
PGPOOL_MIN_SIZE=1
PGPOOL_MAX_SIZE=10
PGPOOL_COMMAND_TIMEOUT=30

POSTGRES_MCP_HOST=0.0.0.0
POSTGRES_MCP_PORT=8010
POSTGRES_MCP_PATH=/mcp
POSTGRES_MCP_URL=http://localhost:8010/mcp

OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini  # default for Streamlit UI
```

## Run the MCP server (streamable HTTP)
```
cd postgres_gpt
python -m postgres_gpt.server4
```
- Health check: `GET http://localhost:8010/health`
- MCP endpoint: `POST http://localhost:8010/mcp` with `Accept: application/json, text/event-stream` and `Content-Type: application/json` (required by the SSE transport). Sessions are managed server-side.

## Run the direct LangGraph agent (no MCP)
```
python -m postgres_gpt.agent_langchain
```
- Edit `agent_langchain.build_agent` to swap LLMs or adjust tool set.

## Run the MCP-backed LangGraph agent
```
python -m postgres_gpt.agent_mcp_client
```
- The agent discovers tools from the MCP server at `POSTGRES_MCP_URL` and wraps them dynamically.

## Run the Streamlit chat UI
```
streamlit run postgres_gpt/streamlit_chat.py
```
- Mode toggle: Direct (LangChain) vs MCP client.
- Model selector: defaults to `OPENAI_MODEL`; replace 0.0.0.0 with localhost automatically for convenience.
- Messages are executed per send (no streaming yet); recent trace shown in reverse-chronological order.

## Smoke test the MCP server
```
python -m postgres_gpt.smoke_mcp
```
- Performs init ➝ list_tools ➝ list_tables; if tables exist, also describe and sample the first table.

## Operational notes
- Samples are capped at 1000 rows; `execute_sql` runs user-provided SQL verbatim—harden inputs before exposing externally.
- asyncpg pool is created on the uvicorn loop during Starlette lifespan and reset on shutdown.
- Set `PGSSL` and tighten credentials for non-local deployments; defaults favor fast local testing.
