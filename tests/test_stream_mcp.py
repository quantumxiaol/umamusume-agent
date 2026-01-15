"""
check streamable http mcp server and list tools

python ./tests/test_stream_mcp.py -u "http://127.0.0.1:7778/mcp" -q "can you tell me about umamusume 爱慕织姬?from local RAG"

python ./tests/test_stream_mcp.py -u "http://127.0.0.1:7777/mcp" -q "can you tell me about umamusume 爱慕织姬?from bilibili wiki"

"""



import asyncio
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from langchain_openai import ChatOpenAI
from mcp import ClientSession
import argparse
from langchain_core.messages import AIMessage,ToolMessage
import os
from dotenv import load_dotenv

load_dotenv()

model = ChatOpenAI(model_name=os.getenv("INFO_LLM_MODEL_NAME"),
    api_key=os.getenv("INFO_LLM_MODEL_API_KEY"),
    base_url=os.getenv("INFO_LLM_MODEL_BASE_URL"),
    temperature=0.7,
    )

server_url="http://127.0.0.1:1234/mcp"
async def async_main(server_url:str="",question:str=""):
    async with streamablehttp_client(server_url) as (read_stream, write_stream, get_session_id):
        # print('MCP server连接成功')
        async with ClientSession(read_stream, write_stream) as session:
            # 初始化连接
            # print('MCP server session已建立')
            await session.initialize()
            print('MCP server session已初始化')

            tools = await load_mcp_tools(session)
            print("可用工具:", [tool.name for tool in tools])

            if question != "":
                agent = create_react_agent(model, tools)
                agent_response = await agent.ainvoke(
                    {"messages": [question]},
                    config={"recursion_limit": 75}
                    
                    )
                # print("Final answer:", agent_response["messages"][-1].content)
                result = extract_tool_info(agent_response)

                print("Final Answer:\n", result["final_answer"])
                print("\nTool Calls:", result["tool_calls"])
                print("\nTool Results:", result["tool_results"])



def extract_tool_info(agent_response):
    tool_calls = []
    tool_results = [] 

    for msg in agent_response["messages"]:
        # 判断消息类型
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls"):
            for tool_call in msg.tool_calls:
                tool_calls.append({
                "name": tool_call["name"],
                "arguments": tool_call["args"]
                })

        elif isinstance(msg, ToolMessage):
            tool_results.append({
            "id": msg.tool_call_id,
            "name": msg.name,
            "content": msg.content,
            "status": msg.status
            })

    # 提取最终回答
    final_answer = None
    for msg in reversed(agent_response["messages"]):
        if isinstance(msg, AIMessage):
            final_answer = msg.content  
        break

    return {
    "tool_calls": tool_calls,
    "tool_results": tool_results,
    "final_answer": final_answer
    }

if __name__ == "__main__":
    arg = argparse.ArgumentParser()
    arg.add_argument("--base_url",
    "-u", 
    type=str, 
    default="http://127.0.0.1:1234/mcp",
    help="MCP server base url"
    )
    arg.add_argument("--question",
    "-q", 
    type=str, 
    default="",
    help="question to ask agent, None for donot ask"
    )
    arg=arg.parse_args()
    asyncio.run(async_main(server_url=arg.base_url,question=arg.question))