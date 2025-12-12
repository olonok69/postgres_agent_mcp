# Postgres MCP Server (server4)

New MCP server built on Streamable HTTP (no SSE) plus two LangChain/LangGraph agent entrypoints.

## What was added
- `server4.py`: Streamable HTTP MCP server exposing four tools (`list_tables`, `describe_table`, `get_table_sample`, `execute_sql`).
- `db.py`: Async Postgres helpers shared by server and agents.
- `agent_langchain.py`: LangGraph ReAct agent wired directly to Postgres tools (no MCP hop).
- `agent_mcp_client.py`: LangGraph agent that calls the MCP server over Streamable HTTP using the MCP Python client.
- `requirements.txt`: Extra deps for this stack (asyncpg, langchain/langgraph, MCP, uvicorn, etc.).

All files live under `postgres_gpt/`.

## Configuration
Set Postgres env vars (local DB for first tests):

```
PGHOST=localhost
PGPORT=5432
PGUSER=...
PGPASSWORD=...
PGDATABASE=...
# Optional
PGSSL=false
POSTGRES_MCP_HOST=0.0.0.0
POSTGRES_MCP_PORT=8010
POSTGRES_MCP_PATH=/mcp
OPENAI_API_KEY=...
```

## Running the MCP server (streamable HTTP)
```
cd postgres_gpt
python -m postgres_gpt.server4
```
- Health: `GET http://localhost:8010/health`
- Streamable MCP endpoint: `POST http://localhost:8010/mcp` with `Accept: application/json, text/event-stream` and `Content-Type: application/json` (as required by the transport). The server will manage sessions and stream responses.

## Using the direct LangChain agent
```
python -m postgres_gpt.agent_langchain
```
- Uses the same four Postgres tools directly (no MCP layer).
- Edit `agent_langchain.build_agent` to change the OpenAI model or plug a different LLM.

## Using the MCP-backed agent
```
python -m postgres_gpt.agent_mcp_client
```
- Connects to the streamable HTTP server at `http://localhost:8010/mcp` (adjust as needed).
- Fetches the four MCP tools dynamically and wraps them as LangChain tools for the agent.

## Notes
- The streamable transport requires both `application/json` and `text/event-stream` in `Accept` headers (mirrors the upstream MCP transport expectations).
- Tool limits: samples capped at 1000 rows; `execute_sql` passes queries straight through—use carefully in production.
- For production, tighten SSL and credentials; the current defaults favor quick local testing.
