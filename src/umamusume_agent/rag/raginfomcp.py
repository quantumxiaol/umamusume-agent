"""
RAG MCP
python raginfomcp.py --http -p 7778
to activate the MCP server

"""
import io
import os
import sys
import uvicorn
import json
import argparse
import uvicorn

from langchain.prompts import PromptTemplate
from langchain.prompts import ChatPromptTemplate
from langchain_openai import OpenAI, ChatOpenAI
from typing import TypedDict, List
from langchain.text_splitter import RecursiveCharacterTextSplitter
from pydantic import BaseModel
from langchain.chains import RetrievalQA
from langchain_openai import ChatOpenAI
from langchain_community.vectorstores import FAISS

from langchain_community.document_loaders import TextLoader,CSVLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.prompts import ChatPromptTemplate
from starlette.middleware.cors import CORSMiddleware

from langchain.schema import Document
# from langchain.embeddings import HuggingFaceEmbeddings
# from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings
from dotenv import load_dotenv
import argparse
import uvicorn
import contextlib
from mcp.server import FastMCP
from collections.abc import AsyncIterator
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from mcp.server.sse import SseServerTransport
from starlette.requests import Request
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .rag import initialize_rag, rag_manager
from ..config import config
config.validate()

model_name=config.INFO_LLM_MODEL_NAME
api_key=config.INFO_LLM_MODEL_API_KEY
api_base=config.INFO_LLM_MODEL_BASE_URL
ua=config.USER_AGENT



# 初始化 LLM 模型
llm = ChatOpenAI(
    model_name=model_name,
    api_key=api_key,
    base_url=api_base,
)

# 初始化RAG系统
print("正在初始化RAG系统...")
initialize_rag(mode="auto", force_rebuild=False)

# 获取检索器
retriever = rag_manager.vectorstore.as_retriever(search_kwargs={"k": 10})

# 创建带有 system 消息的模板
prompt_template = ChatPromptTemplate.from_messages([
    ("system", """你是一个赛马娘信息查询助手。
               你的任务是根据下述给定的已知信息回答用户问题。
               确保你的回复完全依据下述已知信息，不要编造答案。
               请用中文回答用户问题。

               已知信息:
               {context} """),
    ("user", "{question}")
])

# 定义RetrievalQA链
qa_chain = RetrievalQA.from_chain_type(
    llm=llm,
    chain_type="stuff",
    chain_type_kwargs={"prompt": prompt_template},
    retriever=retriever,
    return_source_documents=True
)


mcp = FastMCP("RAG Search MCP")


@mcp.tool(description="""
            Use Local Vector Database to answer questions about Umamusume characters.
            contain accurate and basic information about Umamusume characters.
            input: AnswerResponse
            Parameters:{question: str}
            output: AnswerResponse
            Example:{
                "question": "What is the birthday of Rice Shower?"
            }
            Result:{
                "answer": "Rice Shower was born on 3/05."
            }

""")

async def rag(question: str):
    try:
        # 获取用户问题
        user_question = question
        print(user_question)

        # 通过RAG链生成回答
        raw_answer = qa_chain.invoke(user_question)

        if isinstance(raw_answer, dict):
            answer_text = raw_answer.get("result", str(raw_answer))  # 提取 result 字段
        else:
            answer_text = raw_answer

        # 返回答案
        print(answer_text)
        return {
            "status":"success",
            "result":str(answer_text)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }        

@mcp.tool(description="""
    搜索相关的文档片段。
    
    参数:
        query (str): 搜索查询
        k (int): 返回的文档数量，默认为3
    
    返回:
        dict: 包含搜索结果的字典
""")
async def search_documents(query: str, k: int = 3):
    try:
        # 使用RAG管理器进行搜索
        results = rag_manager.search(query, k=k)
        
        search_results = []
        for i, doc in enumerate(results, 1):
            search_results.append({
                "id": i,
                "content": doc.page_content,
                "source": doc.metadata.get("source", "Unknown"),
                "metadata": doc.metadata
            })
        
        return {
            "status": "success",
            "results": search_results
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

@mcp.tool(description="""
    重新加载RAG系统。
    
    参数:
        force_rebuild (bool): 是否强制重建向量数据库，默认为True
    
    返回:
        dict: 操作结果
""")
async def reload_rag(force_rebuild: bool = True):
    try:
        initialize_rag(mode="auto", force_rebuild=force_rebuild)
        
        # 更新qa_chain的retriever
        global qa_chain, retriever
        retriever = rag_manager.vectorstore.as_retriever(search_kwargs={"k": 10})
        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            chain_type_kwargs={"prompt": prompt_template},
            retriever=retriever,
            return_source_documents=True
        )
        
        return {
            "status": "success",
            "message": "RAG系统已重新加载"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


def create_starlette_app(mcp_server: Server, *, debug: bool = False) -> Starlette:
    sse = SseServerTransport("/messages/")
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        event_store=None,
        json_response=True,
        stateless=True,
    )

    async def handle_sse(request: Request) -> None:
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    async def handle_streamable_http(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Context manager for session manager."""
        async with session_manager.run():
            print("Application started with StreamableHTTP session manager!")
            try:
                yield
            finally:
                print("Application shutting down...")

    return Starlette(
        debug=debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/mcp", app=handle_streamable_http),
            Mount("/messages/", app=sse.handle_post_message),
        ],
        lifespan=lifespan,
    )

rag_mcp_app = create_starlette_app(mcp._mcp_server, debug=True)
# Main entry point
def main():
    mcp_server = mcp._mcp_server

    parser = argparse.ArgumentParser(description="Run Search and Crawl MCP server")

    parser.add_argument(
        "--http",
        action="store_true",
        help="Run the server with Streamable HTTP and SSE transport rather than STDIO (default: False)",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="(Deprecated) An alias for --http (default: False)",
    )
    parser.add_argument(
        "--host", default=None, help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port","-p", type=int, default=None, help="Port to listen on (default: 8887)"
    )
    args = parser.parse_args()
    print(f"🚀 Starting server on port {args.port}")
    use_http = args.http or args.sse

    if not use_http and (args.host or args.port):
        parser.error(
            "Host and port arguments are only valid when using streamable HTTP or SSE transport (see: --http)."
        )
        sys.exit(1)

    if use_http:
        starlette_app = create_starlette_app(mcp_server, debug=True)
        uvicorn.run(
            starlette_app,
            host=args.host if args.host else "127.0.0.1",
            port=args.port if args.port else 7778,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()