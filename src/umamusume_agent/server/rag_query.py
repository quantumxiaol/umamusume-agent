"""
Run
    Terminal1:
    python umamusume_query.py -p 1122
    Terminal2:
    python umamusume_client.py -u http://127.0.0.1:1122/ask

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
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.schema.runnable import RunnableSequence
from typing import TypedDict, List
from langgraph.prebuilt import create_react_agent
from langchain.schema import StrOutputParser
from langchain.schema.runnable import RunnablePassthrough
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain.chains import RetrievalQA
from langchain_openai import ChatOpenAI
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from dashscope import embeddings
from langchain_community.document_loaders import TextLoader,CSVLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.prompts import ChatPromptTemplate
from starlette.middleware.cors import CORSMiddleware
from langchain_community.document_loaders import PyMuPDFLoader
from langchain import hub
from langchain.schema import Document
# from langchain.embeddings import HuggingFaceEmbeddings
# from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings
from ..rag.rag import initialize_rag, rag_manager
from ..config import config
config.validate()

# 获取配置
model_name=config.INFO_LLM_MODEL_NAME
api_key=config.INFO_LLM_MODEL_API_KEY
api_base=config.INFO_LLM_MODEL_BASE_URL

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
    return_source_documents=True  # 返回源文档
)
# 构建 FastAPI 应用，提供服务
app = FastAPI(title="LangChain-Umamusume-Server")

# CORS设置
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


# 定义请求模型
class QuestionRequest(BaseModel):
    question: str

# 定义响应模型
class AnswerResponse(BaseModel):
    answer: str


# 查询接口
@app.post("/ask", response_model=AnswerResponse)
async def ask_question(request: QuestionRequest):
    try:
        user_question = request.question
        print(f"收到问题: {user_question}")

        # 通过RAG链生成回答
        result = qa_chain.invoke(user_question)
        
        # 处理回答结果
        if isinstance(result, dict):
            answer_text = result.get("result", str(result))
            source_docs = result.get("source_documents", [])
        else:
            answer_text = str(result)
            source_docs = []

        # 提取源文档内容
        source_contents = []
        for doc in source_docs[:3]:  # 只返回前3个源文档
            content = doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content
            source_contents.append(f"来源: {doc.metadata.get('source', 'Unknown')} - {content}")

        # 返回答案
        answer = AnswerResponse(
            answer=answer_text,
            source_documents=source_contents
        )
        print(f"回答: {answer_text}")
        return answer
        
    except Exception as e:
        print(f"处理问题时出错: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 健康检查接口
@app.get("/health")
async def health_check():
    return {"status": "healthy", "rag_initialized": rag_manager.vectorstore is not None}

# 重新加载RAG接口
@app.post("/reload")
async def reload_rag():
    try:
        initialize_rag(mode="auto", force_rebuild=True)
        retriever = rag_manager.vectorstore.as_retriever(search_kwargs={"k": 10})
        # 更新qa_chain的retriever
        global qa_chain
        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            chain_type_kwargs={"prompt": prompt_template},
            retriever=retriever,
            return_source_documents=True
        )
        return {"status": "success", "message": "RAG系统已重新加载"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    arg=argparse.ArgumentParser()
    
    arg.add_argument("--port","-p", type=int, default=1145)
    args=arg.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port)