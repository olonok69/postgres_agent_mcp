import asyncio
import json
import logging
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

# Set up logging to file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('agent_activity.log', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('agent_langchain')

@tool
async def list_tables_tool(schema_name: Annotated[str | None, Field(alias="schema")] = None) -> str:
    """List Postgres tables. Optionally filter by schema."""
    try:
        logger.info(f"Tool called: list_tables_tool with schema_name={schema_name}")
        payload = await db.list_tables(schema_name)
        logger.info(f"Tool result: list_tables_tool returned {json.dumps(payload, indent=2)}")
        return json.dumps(payload, indent=2)
    except Exception as e:
        error_msg = f"Error executing tool list_tables_tool: {str(e)}"
        logger.error(error_msg)
        return error_msg


@tool
async def describe_table_tool(
    table_name: str,
    schema_name: Annotated[str | None, Field(alias="schema")] = None,
) -> str:
    """Describe columns and row count for a table."""
    try:
        logger.info(f"Tool called: describe_table_tool with table_name={table_name}, schema_name={schema_name}")
        payload = await db.describe_table(table_name, schema_name)
        logger.info(f"Tool result: describe_table_tool returned {json.dumps(payload, indent=2)}")
        return json.dumps(payload, indent=2)
    except Exception as e:
        error_msg = f"Error executing tool describe_table_tool: {str(e)}"
        logger.error(error_msg)
        return error_msg


@tool
async def get_table_sample_tool(
    table_name: str,
    limit: int = 5,
    schema_name: Annotated[str | None, Field(alias="schema")] = None,
) -> str:
    """Return a sample of rows from a table (max 1000)."""
    try:
        logger.info(f"Tool called: get_table_sample_tool with table_name={table_name}, limit={limit}, schema_name={schema_name}")
        payload = await db.get_table_sample(table_name, limit, schema_name)
        logger.info(f"Tool result: get_table_sample_tool returned {json.dumps(payload, indent=2)}")
        return json.dumps(payload, indent=2)
    except Exception as e:
        error_msg = f"Error executing tool get_table_sample_tool: {str(e)}"
        logger.error(error_msg)
        return error_msg


@tool
async def execute_sql_tool(query: str) -> str:
    """Execute arbitrary SQL against Postgres."""
    try:
        logger.info(f"Tool called: execute_sql_tool with query={query}")
        payload = await db.execute_sql(query)
        logger.info(f"Tool result: execute_sql_tool returned {json.dumps(payload, indent=2)}")
        return json.dumps(payload, indent=2)
    except Exception as e:
        error_msg = f"Error executing tool execute_sql_tool: {str(e)}"
        logger.error(error_msg)
        return error_msg


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
    logger.info(f"Langchain Agent - Input messages: {json.dumps(messages, indent=2)}")
    
    agent = build_agent(model=model)
    # Convert incoming list to LangChain message objects
    chain_input = {
        "messages": [
            AIMessage(
                content=(
                    "You are an AI assistant with access to specialized tools for PostgreSQL database operations.\n\n"
                    "**Your Capabilities:**\n"
                    "- **PostgreSQL Database Operations**: Execute SQL queries, explore tables, and manage database data\n"
                    "  * Use standard PostgreSQL syntax\n"
                    "  * Available tools: list_tables, describe_table, get_table_sample, execute_sql\n\n"
                    "**Usage Guidelines:**\n"
                    "1. Always use the appropriate tool for the user's request\n"
                    "2. For database operations, use proper PostgreSQL syntax\n"
                    "3. Provide clear explanations of what you're doing and why\n"
                    "4. If you need to explore or understand data structure, use list_tables and describe_table first\n"
                    "5. Handle errors gracefully and suggest alternatives when needed\n"
                    "6. When describing tables, use schema-qualified names like 'public.actor'\n"
                    "7. For complex queries, break them down and explain each step\n\n"
                    "**PostgreSQL Specific Instructions:**\n"
                    "- Use LIMIT instead of TOP for row limiting\n"
                    "- Use NOW() or CURRENT_TIMESTAMP for current date/time\n"
                    "- Use LENGTH() for string length\n"
                    "- Use POSITION() for substring search\n"
                    "- Always explore table structure with describe_table before complex queries\n"
                    "- Use schema-qualified table names when necessary\n\n"
                    "**Tool Usage:**\n"
                    "- list_tables: Get overview of available tables\n"
                    "- describe_table: Get detailed column information and row count\n"
                    "- get_table_sample: See actual data from a table\n"
                    "- execute_sql: Run any SQL query (SELECT, INSERT, UPDATE, DELETE, etc.)\n\n"
                    "**Remember**: You can see the full conversation history, so maintain context across interactions."
                ),
                role="assistant",
            )
        ] + [
            HumanMessage(content=item["content"]) if item.get("role") == "user" else AIMessage(content=item["content"])
            for item in messages
        ]
    }
    try:
        result = await agent.ainvoke(chain_input)
    except Exception as e:
        error_msg = f"Error during agent execution: {str(e)}"
        logger.error(error_msg)
        # Return an error message
        return [AIMessage(content=error_msg, role="assistant")]
    
    # Extract only the new messages generated by the agent (not the input messages)
    new_messages = []
    input_message_count = len(chain_input["messages"])
    
    for msg in result["messages"][input_message_count:]:
        if hasattr(msg, 'type') and msg.type == "ai":
            new_messages.append(msg)
        elif hasattr(msg, 'content') and msg.content:
            # Also include tool messages and other responses
            new_messages.append(msg)
    
    # Log the output messages
    output_messages = []
    for msg in new_messages:
        if hasattr(msg, 'content'):
            output_messages.append({"type": getattr(msg, 'type', 'unknown'), "content": msg.content})
        else:
            output_messages.append({"type": str(type(msg)), "content": str(msg)})
    
    logger.info(f"Langchain Agent - Output messages: {json.dumps(output_messages, indent=2)}")
    
    return new_messages


if __name__ == "__main__":
    sample_messages = [{"role": "user", "content": "List the first five tables"}]
    responses = asyncio.run(run_chat(sample_messages))
    print("Agent responses:")
    for msg in responses:
        print(f"- {msg.type}: {msg.content}")
