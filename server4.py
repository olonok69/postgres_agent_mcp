import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from pydantic import Field
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
import uvicorn

from . import db
from mcp.server.sse import SseServerTransport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("postgres_mcp")

load_dotenv()

HOST = os.getenv("POSTGRES_MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("POSTGRES_MCP_PORT", "8010"))
STREAM_PATH = os.getenv("POSTGRES_MCP_PATH", "/mcp")


@asynccontextmanager
async def _lifespan(_: FastMCP):
    # Ensure pool is created on the same event loop uvicorn uses
    await db.get_pool()
    try:
        yield
    finally:
        await db.close_pool()

mcp = FastMCP(
    "Postgres MCP Server",
    host=HOST,
    port=PORT,
    sse_path=STREAM_PATH,
    json_response=False,
    lifespan=_lifespan,
)


def _as_text(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


def _error_content(exc: Exception) -> list[TextContent]:
    error_result = {
        "error": str(exc),
        "status": "error",
        "type": type(exc).__name__
    }
    return [TextContent(type="text", text=json.dumps(error_result, indent=2))]


@mcp.tool()
async def list_tables(schema_name: Annotated[str | None, Field(alias="schema")] = None) -> list[TextContent]:
    """List all tables in the database. Optionally filter by schema name."""
    try:
        payload = await db.list_tables(schema_name)
        return _as_text(payload)
    except Exception as exc:
        logger.exception("list_tables failed")
        return _error_content(exc)


@mcp.tool()
async def describe_table(
    table_name: str,
    schema_name: Annotated[str | None, Field(alias="schema")] = None,
) -> list[TextContent]:
    """Get detailed information about a table including columns, data types, constraints, and row count.
    
    Args:
        table_name: Name of the table to describe (can include schema like 'public.actor')
        schema_name: Optional schema name if not included in table_name
    """
    try:
        payload = await db.describe_table(table_name, schema_name)
        return _as_text(payload)
    except Exception as exc:
        logger.exception("describe_table failed")
        return _error_content(exc)


@mcp.tool()
async def get_table_sample(
    table_name: str,
    limit: int = 5,
    schema_name: Annotated[str | None, Field(alias="schema")] = None,
) -> list[TextContent]:
    """Get a sample of records from a specific table (maximum 1000 rows).
    
    Args:
        table_name: Name of the table to sample (can include schema like 'public.actor')
        limit: Number of rows to return (1-1000, default 5)
        schema_name: Optional schema name if not included in table_name
    """
    try:
        payload = await db.get_table_sample(table_name, limit, schema_name)
        return _as_text(payload)
    except Exception as exc:
        logger.exception("get_table_sample failed")
        return _error_content(exc)


@mcp.tool()
async def execute_sql(query: str) -> list[TextContent]:
    """Execute an SQL query on the PostgreSQL database. Supports SELECT, INSERT, UPDATE, DELETE, and other SQL commands.
    
    Args:
        query: The SQL query to execute
    """
    try:
        payload = await db.execute_sql(query)
        return _as_text(payload)
    except Exception as exc:
        logger.exception("execute_sql failed")
        return _error_content(exc)


async def health_check(request):
    try:
        await db.get_pool()
        return JSONResponse({"status": "healthy", "database": os.getenv("PGDATABASE")})
    except Exception as exc:  # pragma: no cover - health only
        logger.exception("Health check failed")
        return JSONResponse({"status": "unhealthy", "error": str(exc)}, status_code=500)


def build_app() -> Starlette:
    """Create SSE Starlette app with MCP routes and /health."""
    sse = SseServerTransport(mcp.settings.message_path)

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:  # type: ignore[arg-type]
            await mcp._mcp_server.run(  # type: ignore[attr-defined]
                streams[0],
                streams[1],
                mcp._mcp_server.create_initialization_options(),  # type: ignore[attr-defined]
            )

    return Starlette(
        debug=mcp.settings.debug,
        routes=[
            Route(mcp.settings.sse_path, endpoint=handle_sse),
            Mount(mcp.settings.message_path, app=sse.handle_post_message),
            Route("/health", health_check, methods=["GET"]),
        ],
    )


def main() -> None:
    app = build_app()
    logger.info("Starting Postgres MCP streamable HTTP server")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
