"""
Run
    Terminal:
        python umamusume_client.py -u http://127.0.0.1:1111/ask

curl -X POST http://127.0.0.1:1111/ask \
     -H "Content-Type: application/json" \
     -d '{"question":"帮我创作一篇爱慕织姬的甜甜的同人文"}'


curl -X POST "http://127.0.0.1:1111/askstream" \
     -H "Content-Type: application/json" \
     -d '{"question": "请创作赛马娘米浴和训练员的感人故事"}'
        
    帮我创作一篇"爱慕织姬"的甜甜的同人文，你可以先去RAG上查找准确的信息，再通过web到wiki上搜索相关角色的信息，据此创作符合性格的同人小说
"""

import requests
from langchain.memory import ConversationBufferMemory
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.runnables import RunnableLambda
import os,sys,argparse

from dotenv import load_dotenv
load_dotenv()

model=os.getenv("INFO_LLM_MODEL_NAME")
api_key=os.getenv("INFO_LLM_MODEL_API_KEY")
api_base=os.getenv("INFO_LLM_MODEL_BASE_URL")
# 初始化记忆对象
memory = ConversationBufferMemory()
llm = ChatOpenAI(
    model_name= model,
    api_key= api_key,
    base_url=api_base,
)
# 设置服务端地址
url = "http://127.0.0.1:1145/ask"

def ask_question(question: str,url: str):
    """
    发送问题到服务端，并获取回答。
    
    :param question: 用户提出的问题
    :return: 服务端返回的回答
    """
    payload = {
        "question": question
    }
    
    try:
        # 发送POST请求
        response = requests.post(url, json=payload)
        
        # 检查响应状态码
        if response.status_code == 200:
            answer = response.json().get("answer")
            # print(f"Answer: {answer}")
            return str(answer) if answer is not None else ""
        else:
            print(f"Error: Received status code {response.status_code}")
            print(f"Response: {response.text}")
            return "Error internal Server Error"
            
    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return "Error internal Server Error"
# 构建 Prompt 模板（用于包装用户问题 + 历史记录）
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个对话机器人助手。请根据以下历史对话和当前问题给出回答。\n\n历史对话:\n{history}\n\n当前问题:\n{input}"),
    ("user", "{input}")
])

# 创建处理链：将 prompt 格式化后传给 ask_question 函数（模拟LLM）
def format_and_query(input_dict, server_url):
    # 手动提取历史消息并转换为字符串
    raw_history = input_dict.get("history", [])
    
    history_lines = []
    for msg in raw_history:
        content = msg.content
        if isinstance(content, dict):  # 如果是 dict，只取 content 字段
            content = content.get("content", "")
        elif not isinstance(content, str):  # 其他非字符串也转成字符串
            content = str(content)
        
        history_lines.append(f"{msg.type}: {content}")

    history_str = "\n".join(history_lines)
    user_input = input_dict["input"]

    full_prompt = f"""
                    你是一个助手，请根据以下历史对话和当前问题给出回答。

                    历史对话:
                    {history_str}

                    当前问题:
                    {user_input}
                    """

    # print("Full Prompt Type", full_prompt.__class__)
    # print("Full Prompt Sample:\n", full_prompt[:300] + "...")

    return ask_question(full_prompt, server_url)


if __name__ == "__main__":
    arg=argparse.ArgumentParser()
    arg.add_argument("--server_url","-u", type=str, default="http://127.0.0.1:1145/ask")
    arg.add_argument("--question","-q", type=str, default="创作米浴（Rice Shower）的同人文，请先去wiki上搜索相关角色的信息，据此创作符合性格的同人小说")
    args=arg.parse_args()
    print("开始对话！输入 'exit' 退出。\n")
    chain = (
        {
            "input": lambda x: x["input"],
            "history": lambda _: memory.chat_memory.messages,
        }
        | RunnablePassthrough.assign(
            context=RunnableLambda(lambda x: format_and_query(x, args.server_url))
        )
    )
    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            break

        # 添加用户输入到记忆
        memory.chat_memory.add_user_message(user_input)

        # 调用链
        result = chain.invoke({"input": user_input})

        # 获取回答（这里取决于如何封装结果）
        answer = result.get("context", "没有收到回答。")

        # 添加 AI 回答到记忆
        memory.chat_memory.add_ai_message(answer)

        print(f"Bot: {answer}")