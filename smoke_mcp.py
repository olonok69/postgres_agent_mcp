"""Quick MCP smoke test over Streamable HTTP.

Usage (PowerShell):
  python -m postgres_gpt.smoke_mcp

Optionally set POSTGRES_MCP_URL (default http://localhost:8010/mcp).
"""
import asyncio
import os
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


def _render_call_result(result: Any) -> str:
    """Convert CallToolResult to readable text."""
    # Prefer structured content
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        import json

        return json.dumps(structured, indent=2)

    # Fallback to text content blocks
    lines: list[str] = []
    for block in result.content or []:
        text = getattr(block, "text", None)
        if text:
            lines.append(text)
    return "\n".join(lines) if lines else result.model_dump_json(indent=2)


def _extract_tables(result: Any) -> list[dict]:
    """Best-effort extraction of tables array from CallToolResult."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and isinstance(structured.get("tables"), list):
        return structured.get("tables") or []

    tables: list[dict] = []
    # Try to parse any text blocks as JSON
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            import json

            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("tables"), list):
                tables = data.get("tables") or []
                break
        except Exception:
            continue
    return tables


async def main() -> None:
    url = os.getenv("POSTGRES_MCP_URL", "http://localhost:8010/mcp")
    print(f"Connecting to MCP server at {url} ...")

    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("Initialized with server:", init.serverInfo)

            tools = await session.list_tools()
            print("Tools available:")
            for t in tools.tools:
                print(f"- {t.name}: {t.description}")

            # List tables
            print("\nCalling list_tables ...")
            tables_res = await session.call_tool("list_tables", {})
            print(_render_call_result(tables_res))

            # If tables found, describe and sample the first one
            tables_list = _extract_tables(tables_res)

            first_full_name = None
            if tables_list:
                first_entry = tables_list[0]
                if isinstance(first_entry, dict):
                    first_full_name = first_entry.get("full_name") or first_entry.get("table_name")

            if first_full_name:
                print(f"\nDescribing table {first_full_name} ...")
                desc_res = await session.call_tool("describe_table", {"table_name": first_full_name})
                print(_render_call_result(desc_res))

                print(f"\nSampling table {first_full_name} ...")
                sample_res = await session.call_tool(
                    "get_table_sample", {"table_name": first_full_name, "limit": 5}
                )
                print(_render_call_result(sample_res))
            else:
                print("No tables found to describe/sample.")


if __name__ == "__main__":
    asyncio.run(main())
