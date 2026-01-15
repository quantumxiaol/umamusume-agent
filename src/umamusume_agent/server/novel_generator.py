"""
Run
    Terminal 1:
    python webinfomcp.py -p 7777

    Terminal 2:
    python raginfomcp.py -p 7778    

    Terminal 3:
    (Linux MacOS)
    python umamusume_create_novel.py -p 1111 \
        -w http://127.0.0.1:7777/mcp \
        -r http://127.0.0.1:7778/mcp
    (Windows PS)
    python umamusume_create_novel.py -p 1111 `
            -w http://127.0.0.1:7777/mcp `
            -r http://127.0.0.1:7778/mcp    

    Terminal 4:
    python umamusume_client.py -u http://127.0.0.1:1111/ask

curl -X POST http://127.0.0.1:1111/ask -H "Content-Type: application/json" \
-d '{"question":"帮我创作一篇爱慕织姬的甜甜的同人文，你可以先去wiki上搜索相关角色的信息，据此创作符合性格的同人小说"}'

curl -X POST http://localhost:1122/ask -json {"question": "请告诉我关于特别周的一些信息"}


curl -X POST "http://127.0.0.1:1111/askstream" \
     -H "Content-Type: application/json" \
     -d '{"question": "请创作赛马娘米浴和训练员的感人故事"}'

"""


import io
import os
import sys
import uvicorn
import json
import argparse
import asyncio
import traceback
from langchain.prompts import PromptTemplate
from langchain.prompts import ChatPromptTemplate
from langchain_openai import OpenAI, ChatOpenAI
from openai import OpenAI as OpenAIClient
from langchain_core.messages import HumanMessage, SystemMessage, AIMessageChunk
from typing import TypedDict, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_openai import ChatOpenAI
from starlette.middleware.cors import CORSMiddleware

from langchain.agents import  AgentExecutor
from langgraph.prebuilt import create_react_agent

from langchain.prompts import MessagesPlaceholder
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_core.messages import AIMessage,ToolMessage
from ..config import config
config.validate()


model_name=config.INFO_LLM_MODEL_NAME
api_key=config.INFO_LLM_MODEL_API_KEY
api_base=config.INFO_LLM_MODEL_BASE_URL
ua=config.USER_AGENT

model_name_writer=config.WRITER_LLM_MODEL_NAME
api_key_writer=config.WRITER_LLM_MODEL_API_KEY
api_base_writer=config.WRITER_LLM_MODEL_BASE_URL

prompt_dir=config.PROMPT_DIRECTORY

searchinrag_prompt_path = os.path.join(config.PROMPT_DIRECTORY, "searchinrag.md")
searchinweb_prompt_path = os.path.join(config.PROMPT_DIRECTORY, "searchinweb.md")
writenovel_prompt_path = os.path.join(config.PROMPT_DIRECTORY, "writenovel.md")


# 初始化 LLM 模型
model = ChatOpenAI(
    model_name= model_name,
    api_key= api_key,
    base_url=api_base,
)
model_writer=ChatOpenAI(
    model_name= model_name_writer,
    api_key= api_key_writer,
    base_url=api_base_writer,
)

app = FastAPI(title="LangChain-Umamusume-Server")



# 前端报CORS时
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

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

# 定义请求模型
class QuestionRequest(BaseModel):
    question: str

# 定义响应模型
class AnswerResponse(BaseModel):
    answer: str


# 提供创作接口 http://127.0.0.1:1145/ask
@app.post("/ask", response_model=AnswerResponse)
async def ask_question(request: Request, user_request: QuestionRequest):
    user_question = str(user_request.question) if user_request.question else ""
    print(f"用户问题：{user_question}")

    rag_url = request.app.state.rag_mcp_url
    web_url = request.app.state.web_mcp_url
    # --- 可选：添加防御性检查 ---
    if not rag_url or not web_url:
        error_msg = "MCP server URLs are not configured. Please start the server with -r and -w arguments."
        print(f"[ERROR] {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
    try:
        # 第一阶段：使用 RAG Agent 获取基础信息
        async with streamablehttp_client(rag_url) as (read_stream, write_stream, get_session_id):
        # print('MCP server连接成功')
            async with ClientSession(read_stream, write_stream) as session:

                await session.initialize()
                rag_tools = await load_mcp_tools(session)
                print("RAG可用MCP工具:", [tool.name for tool in rag_tools])

                rag_agent = create_react_agent(model, rag_tools)
                with open(searchinrag_prompt_path, "r", encoding="utf-8") as file:
                    template = file.read()
                first_input = template.format(user_question=user_question)
                rag_result = await rag_agent.ainvoke({"messages": [HumanMessage(content=first_input)]})
                base_info = rag_result["messages"][-1].content
                result1 = extract_tool_info(rag_result)
                print(f"[第一阶段结果] 基础信息: {base_info}")
                print("\n[第一阶段Tool Call] : ", result1["tool_calls"])

        # 第二阶段：使用 Web Agent 结合基础信息回答问题
        async with streamablehttp_client(web_url) as (read_stream, write_stream, get_session_id):
        # print('MCP server连接成功')
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                web_tools = await load_mcp_tools(session)
                print("Web可用MCP工具:", [tool.name for tool in web_tools])

                web_agent = create_react_agent(model, web_tools)
                with open(searchinweb_prompt_path, "r", encoding="utf-8") as file:
                    template = file.read()
                final_input = template.format(user_question=user_question,base_info=base_info)
                web_result = await web_agent.ainvoke(
                    {"messages": [HumanMessage(content=final_input)]},
                    config={"recursion_limit": 75}
                    )
                final_answer = web_result["messages"][-1].content
                result2 = extract_tool_info(web_result)
                print(f"[第二阶段结果] 最终回答: {final_answer}")
                print("\n[第二阶段Tool Call]: ", result2["tool_calls"])
                print("\n[第二阶段Tool Results]: ", result2["tool_results"])
                web_info=final_answer

                # return AnswerResponse(answer=final_answer)
            
        # 第三阶段：使用结合基础信息创作小说
        with open(writenovel_prompt_path, "r", encoding="utf-8") as file:
            template = file.read()
        final_input = template.format(user_question=user_question,base_info=base_info,web_info=web_info)
        novel_agent = create_react_agent(model_writer,tools=[])
        result = await novel_agent.ainvoke({"messages": [HumanMessage(content=final_input)]})
        final_answer = result["messages"][-1].content
        print(" Final Answer:\n", final_answer)
        return AnswerResponse(answer=final_answer)


    except BaseException as e: 
        # --- 打印详细的错误信息 ---
        error_msg = f"[ERROR] Unhandled error during processing in /ask route: {type(e).__name__}: {e}"
        print(error_msg)
        
        # --- 打印完整的堆栈跟踪 ---
        traceback_str = traceback.format_exc()
        print(f"[ERROR] Full Traceback:\n{traceback_str}")
        # --- 打印结束 ---
        
        # --- 返回给客户端更具体的错误信息---
        # 将 traceback_str 也包含在 detail 中可以帮助开发者快速定位问题
        # 不要将敏感信息通过 detail 暴露给最终用户
        detailed_error = f"{str(e)}\n(See server logs for full traceback)" 
        raise HTTPException(status_code=500, detail=detailed_error)


@app.post("/askstream")
async def ask_question_stream(request: Request, user_request: QuestionRequest):
    user_question = str(user_request.question) if user_request.question else ""
    print(f"[STREAM] 用户问题：{user_question}")

    rag_url = request.app.state.rag_mcp_url
    web_url = request.app.state.web_mcp_url

    if not rag_url or not web_url:
        raise HTTPException(status_code=500, detail="MCP server URLs not configured.")

    async def event_stream():
        try:
            # --- 第一阶段：RAG ---
            yield json.dumps({"event": "status", "data": "正在RAG搜索中，查找相关角色信息..."}, ensure_ascii=False) + "\n"
            print("[STREAM] 开始第一阶段：RAG 搜索")
            
            try:
                async with streamablehttp_client(rag_url) as (read_stream, write_stream, get_session_id):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        rag_tools = await load_mcp_tools(session)
                        rag_agent = create_react_agent(model, rag_tools)
                        
                        with open(searchinrag_prompt_path, "r", encoding="utf-8") as file:
                            template = file.read()
                        first_input = template.format(user_question=user_question)
                        rag_result = await rag_agent.ainvoke({"messages": [HumanMessage(content=first_input)]})
                        base_info = rag_result["messages"][-1].content
                        print(f"[STREAM] RAG 阶段完成，基础信息长度: {len(base_info)}")
                        yield json.dumps({"event": "status", "data": "RAG搜索完成！"}, ensure_ascii=False) + "\n"
                        yield json.dumps({"event": "rag_result", "data": base_info}, ensure_ascii=False) + "\n"
            except asyncio.CancelledError:
                print("[STREAM] RAG 阶段被用户取消")
                yield json.dumps({"event": "error", "data": "生成已被取消"}, ensure_ascii=False) + "\n"
                return

            # --- 第二阶段：Web Search ---
            yield json.dumps({"event": "status", "data": "正在Web搜索中，获取更多相关信息..."}, ensure_ascii=False) + "\n"
            print("[STREAM] 开始第二阶段：Web 搜索")
            
            try:
                async with streamablehttp_client(web_url) as (read_stream, write_stream, get_session_id):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        web_tools = await load_mcp_tools(session)
                        web_agent = create_react_agent(model, web_tools)
                        
                        with open(searchinweb_prompt_path, "r", encoding="utf-8") as file:
                            template = file.read()
                        final_input = template.format(user_question=user_question, base_info=base_info)
                        web_result = await web_agent.ainvoke(
                            {"messages": [HumanMessage(content=final_input)]},
                            config={"recursion_limit": 75}
                        )
                        web_info = web_result["messages"][-1].content
                        print(f"[STREAM] Web 搜索阶段完成，信息长度: {len(web_info)}")
                        yield json.dumps({"event": "status", "data": "Web搜索完成！"}, ensure_ascii=False) + "\n"
                        yield json.dumps({"event": "web_result", "data": web_info}, ensure_ascii=False) + "\n"
            except asyncio.CancelledError:
                print("[STREAM] Web 搜索阶段被用户取消")
                yield json.dumps({"event": "error", "data": "生成已被取消"}, ensure_ascii=False) + "\n"
                return

            # --- 第三阶段：小说生成（流式）---
            yield json.dumps({"event": "status", "data": "开始生成小说..."}, ensure_ascii=False) + "\n"
            print("[STREAM] 开始第三阶段：小说生成")
            try:
                with open(writenovel_prompt_path, "r", encoding="utf-8") as file:
                    template = file.read()
                final_input = template.format(user_question=user_question, base_info=base_info, web_info=web_info)

                # 使用流式调用模型生成小说
                print("[STREAM] 调用模型生成小说（流式）...")
                
                # 直接使用 model_writer 的流式接口，而不是通过 agent
                async for chunk in model_writer.astream([HumanMessage(content=final_input)]):
                    # LangChain 的流式输出返回 AIMessageChunk 对象
                    if isinstance(chunk, AIMessageChunk):
                        if chunk.content:
                            # 流式输出每个 token chunk
                            yield json.dumps({"event": "token", "data": chunk.content}, ensure_ascii=False) + "\n"
                    elif hasattr(chunk, 'content') and chunk.content:
                        # 兼容其他格式
                        yield json.dumps({"event": "token", "data": chunk.content}, ensure_ascii=False) + "\n"
                    elif isinstance(chunk, dict) and 'content' in chunk:
                        yield json.dumps({"event": "token", "data": chunk['content']}, ensure_ascii=False) + "\n"
                
                print("[STREAM] 模型流式调用完成")

                # 完成信号
                print("[STREAM] 发送完成信号")
                yield json.dumps({"event": "done", "data": ""}, ensure_ascii=False) + "\n"
                
            except asyncio.CancelledError:
                print("[STREAM] 小说生成阶段被用户取消")
                yield json.dumps({"event": "error", "data": "生成已被取消"}, ensure_ascii=False) + "\n"
                return
            except Exception as e:
                error_msg = f"Error in post_writer: {type(e).__name__}: {str(e)}"
                print(f"[STREAM ERROR] {error_msg}")
                traceback_str = traceback.format_exc()
                print(f"[STREAM ERROR] Full Traceback:\n{traceback_str}")
                yield json.dumps({"event": "error", "data": error_msg}, ensure_ascii=False) + "\n"

        except asyncio.CancelledError:
            # 专门处理用户取消操作
            print("[STREAM] 流式生成被用户取消")
            yield json.dumps({"event": "cancelled", "data": "生成已被用户取消"}, ensure_ascii=False) + "\n"
            return
        except ExceptionGroup as eg:
            # 处理 ExceptionGroup
            error_msg = f"ExceptionGroup: {len(eg.exceptions)} sub-exceptions"
            print(f"[STREAM ERROR] {error_msg}")
            for i, exc in enumerate(eg.exceptions):
                print(f"[STREAM ERROR] Sub-exception {i}: {type(exc).__name__}: {str(exc)}")
            traceback_str = traceback.format_exc()
            print(f"[STREAM ERROR] Full Traceback:\n{traceback_str}")
            yield json.dumps({"event": "error", "data": error_msg}, ensure_ascii=False) + "\n"
        except BaseException as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            print(f"[STREAM ERROR] {error_msg}")
            traceback_str = traceback.format_exc()
            print(f"[STREAM ERROR] Full Traceback:\n{traceback_str}")
            yield json.dumps({"event": "error", "data": error_msg}, ensure_ascii=False) + "\n"

    # 返回流式响应
    return StreamingResponse(event_stream(), media_type="text/event-stream")
    
