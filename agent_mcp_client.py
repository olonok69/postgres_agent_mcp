import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Tuple

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, create_model, Field, ValidationError

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


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
logger = logging.getLogger('agent_mcp_client')

@asynccontextmanager
async def mcp_session(endpoint: str):
    async with sse_client(endpoint) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _tool_from_mcp(session: ClientSession, tool: Any) -> Tool:
    def _build_args_schema() -> type[BaseModel] | None:
        schema = getattr(tool, "inputSchema", {}) or {}
        properties: Dict[str, Any] = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", []) if isinstance(schema, dict) else [])

        field_definitions: Dict[str, Tuple[Any, Field]] = {}

        def _map_type(prop: dict | None) -> Any:
            if not isinstance(prop, dict):
                return Any
            t = prop.get("type")
            if t == "string":
                return str
            if t == "integer":
                return int
            if t == "number":
                return float
            if t == "boolean":
                return bool
            return Any

        for name, prop in properties.items():
            mapped = _map_type(prop)
            safe_name = name if name != "schema" else "schema_name"
            field = Field(
                ... if name in required else None,
                description=prop.get("description") if isinstance(prop, dict) else None,
                alias=name,
            )
            field_definitions[safe_name] = (mapped if name in required else mapped | None, field)

        try:
            model = create_model(
                f"{tool.name}_Args",
                __config__=ConfigDict(extra="allow"),
                __base__=BaseModel,
                **field_definitions,
            )
            return model
        except Exception:
            return None

    async def _invoke(*args: Any, **kwargs: Any) -> str:
        try:
            # LangChain may pass input as a single positional dict or kwargs; normalize.
            payload: dict[str, Any] | None = None

            if args and isinstance(args[0], dict):
                payload = args[0]
            elif kwargs:
                payload = kwargs
            elif args:
                # Handle single-input tools
                input_value = args[0]
                if tool.name == "list_tables":
                    payload = {"schema": input_value} if input_value else {}
                else:
                    # For other tools, assume it's the first required parameter
                    if tool.name == "describe_table":
                        payload = {"table_name": input_value}
                    elif tool.name == "get_table_sample":
                        payload = {"table_name": input_value}
                    elif tool.name == "execute_sql":
                        payload = {"query": input_value}

            logger.info(f"MCP Tool called: {tool.name} with payload {json.dumps(payload, indent=2)}")

            response = await session.call_tool(tool.name, payload or None)
            if response.isError:
                # Prefer textual error content if present
                err_parts: list[str] = []
                for item in response.content or []:
                    text = getattr(item, "text", None)
                    if text:
                        err_parts.append(text)
                if err_parts:
                    result = "\n".join(err_parts)
                else:
                    result = json.dumps(response.model_dump(), indent=2)
                logger.info(f"MCP Tool result: {tool.name} returned error: {result}")
                return result

            # Extract text from content blocks (TextContent/ImageContent/EmbeddedResource)
            parts: list[str] = []
            for item in response.content or []:
                text = getattr(item, "text", None)
                if text:
                    parts.append(text)
                    continue

                if getattr(item, "type", None) == "resource":
                    try:
                        parts.append(json.dumps(item.resource.model_dump(), indent=2))
                    except Exception:
                        parts.append(str(item))
                    continue

                try:
                    parts.append(json.dumps(item.model_dump(), indent=2))
                except Exception:
                    parts.append(str(item))

            result = "\n".join(parts) if parts else json.dumps(response.model_dump(), indent=2)
            logger.info(f"MCP Tool result: {tool.name} returned {result}")
            return result
        except Exception as e:
            error_msg = f"Error executing tool {tool.name}: {str(e)}"
            logger.error(error_msg)
            return error_msg

    args_schema = _build_args_schema()

    if args_schema is None:
        class _DefaultArgs(BaseModel):
            model_config = ConfigDict(extra="allow")
            table_name: str
            schema_name: str | None = Field(default=None, alias="schema")
            limit: int | None = None

        args_schema = _DefaultArgs

    async def _invoke_wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return await _invoke(*args, **kwargs)
        except ValidationError as e:
            error_msg = f"Validation error for tool {tool.name}: {str(e)}"
            logger.error(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"Error executing tool {tool.name}: {str(e)}"
            logger.error(error_msg)
            return error_msg

    # Use args_schema if available, otherwise let LangChain treat it as single-input tool
    if args_schema is not None:
        pass  # use the args_schema
    else:
        args_schema = None

    return Tool.from_function(
        func=None,
        coroutine=_invoke_wrapper,
        name=tool.name,
        description=tool.description or "MCP remote tool",
        args_schema=args_schema,
    )


async def build_mcp_agent(endpoint: str, model: str = "gpt-4.1"):
    async def runner(messages: List[Dict[str, str]]):
        logger.info(f"MCP Agent - Input messages: {json.dumps(messages, indent=2)}")
        
        async with mcp_session(endpoint) as session:
            tools_info = (await session.list_tools()).tools
            lc_tools = [_tool_from_mcp(session, t) for t in tools_info]

            llm = ChatOpenAI(model=model, temperature=0)
            agent = create_react_agent(llm, lc_tools)

            system_msg = AIMessage(
                content=(
                    "You are an AI assistant with access to specialized tools through MCP (Model Context Protocol) servers.\n\n"
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

            chain_input = {
                "messages": [system_msg]
                + [
                    HumanMessage(content=m["content"]) if m.get("role") == "user" else AIMessage(content=m["content"])
                    for m in messages
                ]
            }
            try:
                result = await agent.ainvoke(chain_input, config={"recursion_limit": 50})
            except Exception as e:
                error_msg = f"Error during agent execution: {str(e)}"
                logger.error(error_msg)
                # Return an error message as if it was a response
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
            
            logger.info(f"MCP Agent - Output messages: {json.dumps(output_messages, indent=2)}")
            
            return new_messages

    return runner


async def demo(endpoint: str):
    runner = await build_mcp_agent(endpoint)
    messages = [{"role": "user", "content": "List all tables"}]
    responses = await runner(messages)
    print("Agent responses:")
    for msg in responses:
        print(f"- {msg.type}: {msg.content}")


if __name__ == "__main__":
    asyncio.run(demo("http://localhost:8010/mcp"))
