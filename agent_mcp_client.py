import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


load_dotenv()

@asynccontextmanager
async def mcp_session(endpoint: str):
    async with sse_client(endpoint) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _tool_from_mcp(session: ClientSession, tool: Any) -> Tool:
    async def _invoke(*args: Any, **kwargs: Any) -> str:
        # LangChain may pass input as a single positional dict or kwargs; normalize.
        payload: dict[str, Any] | None = None
        if args and isinstance(args[0], dict):
            payload = args[0]
        elif kwargs:
            payload = kwargs

        response = await session.call_tool(tool.name, payload or None)
        if response.isError:
            # Prefer textual error content if present
            err_parts: list[str] = []
            for item in response.content or []:
                text = getattr(item, "text", None)
                if text:
                    err_parts.append(text)
            if err_parts:
                return "\n".join(err_parts)
            return json.dumps(response.model_dump(), indent=2)

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

        return "\n".join(parts) if parts else json.dumps(response.model_dump(), indent=2)

    return Tool.from_function(
        func=None,
        coroutine=_invoke,
        name=tool.name,
        description=tool.description or "MCP remote tool",
    )


async def build_mcp_agent(endpoint: str, model: str = "gpt-4.1"):
    async def runner(messages: List[Dict[str, str]]):
        async with mcp_session(endpoint) as session:
            tools_info = (await session.list_tools()).tools
            lc_tools = [_tool_from_mcp(session, t) for t in tools_info]

            llm = ChatOpenAI(model=model, temperature=0)
            agent = create_react_agent(llm, lc_tools)

            chain_input = {
                "messages": [
                    HumanMessage(content=m["content"]) if m.get("role") == "user" else AIMessage(content=m["content"])
                    for m in messages
                ]
            }
            result = await agent.ainvoke(chain_input, config={"recursion_limit": 50})
            return result["messages"]

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
