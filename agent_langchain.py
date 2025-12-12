import asyncio
import json
from typing import Annotated, Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv
from pydantic import Field

# Support both package and script execution
try:
    from . import db
except ImportError:
    import importlib
    import pathlib
    import sys

    sys.path.append(str(pathlib.Path(__file__).resolve().parent))
    db = importlib.import_module("db")


load_dotenv()

@tool
async def list_tables_tool(schema_name: Annotated[str | None, Field(alias="schema")] = None) -> str:
    """List Postgres tables. Optionally filter by schema."""
    payload = await db.list_tables(schema_name)
    return json.dumps(payload, indent=2)


@tool
async def describe_table_tool(
    table_name: str,
    schema_name: Annotated[str | None, Field(alias="schema")] = None,
) -> str:
    """Describe columns and row count for a table."""
    payload = await db.describe_table(table_name, schema_name)
    return json.dumps(payload, indent=2)


@tool
async def get_table_sample_tool(
    table_name: str,
    limit: int = 5,
    schema_name: Annotated[str | None, Field(alias="schema")] = None,
) -> str:
    """Return a sample of rows from a table (max 1000)."""
    payload = await db.get_table_sample(table_name, limit, schema_name)
    return json.dumps(payload, indent=2)


@tool
async def execute_sql_tool(query: str) -> str:
    """Execute arbitrary SQL against Postgres."""
    payload = await db.execute_sql(query)
    return json.dumps(payload, indent=2)


def build_agent(model: str = "gpt-4o-mini"):
    """Create a LangGraph ReAct agent wired to direct Postgres tools."""
    tools = [
        list_tables_tool,
        describe_table_tool,
        get_table_sample_tool,
        execute_sql_tool,
    ]
    llm = ChatOpenAI(model=model, temperature=0)
    return create_react_agent(llm, tools)


async def run_chat(messages: List[Dict[str, str]], model: str = "gpt-4.1") -> List[Any]:
    """Run a conversation through the direct-tool agent."""
    agent = build_agent(model=model)
    # Convert incoming list to LangChain message objects
    chain_input = {
        "messages": [
            HumanMessage(content=item["content"]) if item.get("role") == "user" else AIMessage(content=item["content"])
            for item in messages
        ]
    }
    result = await agent.ainvoke(chain_input)
    return result["messages"]


if __name__ == "__main__":
    sample_messages = [{"role": "user", "content": "List the first five tables"}]
    responses = asyncio.run(run_chat(sample_messages))
    print("Agent responses:")
    for msg in responses:
        print(f"- {msg.type}: {msg.content}")
