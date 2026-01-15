import uvicorn
import argparse
import asyncio
import subprocess
import sys
import os
import signal
import time
from pathlib import Path

# --- 配置和常量 ---
# 在这里定义默认值

DEFAULT_RAG_PORT = 7777
DEFAULT_WEB_PORT = 7778
DEFAULT_SERVER_PORT = 1111
DEFAULT_HOST = "127.0.0.1"

LOGS_DIR = "./logs"
os.makedirs(LOGS_DIR, exist_ok=True) # 确保日志目录存在

# --- 全局变量用于管理子进程 ---
_processes_to_cleanup = []
def howtorun():
    print("Hello from umamusume-agent!")
    print("bash ./scripts/run-server.sh")
    print("bash ./scripts/run-client.sh")
# --- 工具函数 ---
def cleanup():
    """清理所有启动的子进程"""
    global _processes_to_cleanup
    print("\n🛑 Shutting down all services...")
    for process in _processes_to_cleanup:
        if process and process.poll() is None: # 如果进程还在运行
            try:
                # 尝试优雅关闭
                process.terminate()
                process.wait(timeout=5)
                print(f"   -> Process {process.args[0]} (PID: {process.pid}) terminated gracefully.")
            except subprocess.TimeoutExpired:
                print(f"   -> Process {process.args[0]} (PID: {process.pid}) timed out, forcing kill.")
                process.kill()
                process.wait()
            except Exception as e:
                print(f"   -> Error stopping process {process.args[0]} (PID: {process.pid}): {e}")
        else:
            print(f"   -> Process {process.args[0] if process else 'N/A'} (PID: {process.pid if process else 'N/A'}) already exited.")
    _processes_to_cleanup = []
    print("✅ All services stopped.")

def signal_handler(signum, frame):
    """处理 SIGINT (Ctrl+C)"""
    print(f"\n🛑 Received signal {signum}.")
    cleanup()
    sys.exit(0)

async def wait_for_logs(log_file_path: str, success_indicators: list, timeout: int = 60) -> bool:
    """等待日志文件中出现任一成功的标志"""
    log_file_path = Path(log_file_path)
    print(f"⏳ Waiting for log indicator in {log_file_path.name} (Timeout: {timeout}s)")
    print(f"   Looking for: {success_indicators}")
    wait_count = 0
    sleep_interval = 2
    while wait_count < timeout / sleep_interval:
        if log_file_path.exists():
            try:
                with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    for indicator in success_indicators:
                        if indicator in content:
                            print(f"✅ Found indicator '{indicator}' in {log_file_path.name}.")
                            return True
            except Exception as e:
                print(f"⚠️  Error reading log {log_file_path.name}: {e}")
        await asyncio.sleep(sleep_interval)
        wait_count += 1
    print(f"❌ Timeout waiting for indicator in {log_file_path.name}.")
    return False

# --- 服务启动函数 ---
async def start_rag_mcp(rag_port: int):
    """启动 RAG MCP 服务"""
    global _processes_to_cleanup
    cmd = [
        sys.executable, "-m", "uvicorn",
        "umamusume_agent.rag.raginfomcp:rag_mcp_app",
        "--host", DEFAULT_HOST, "--port", str(rag_port),
        "--log-level", "info"
    ]
    log_file = f"{LOGS_DIR}/rag_mcp.log"
    print(f"🚀 Starting RAG MCP: {' '.join(cmd)}")
    with open(log_file, 'w') as f_log:
        process = subprocess.Popen(cmd, stdout=f_log, stderr=subprocess.STDOUT)
    _processes_to_cleanup.append(process)
    print(f"   -> RAG MCP PID: {process.pid}, Log: {log_file}")
    return process, log_file

async def start_web_mcp(web_port: int):
    """启动 Web MCP 服务"""
    global _processes_to_cleanup
    cmd = [
        sys.executable, "-m", "uvicorn",
        "umamusume_agent.web.webinfomcp:web_mcp_app",
        "--host", DEFAULT_HOST, "--port", str(web_port),
        "--log-level", "info"
    ]
    log_file = f"{LOGS_DIR}/web_mcp.log"
    print(f"🚀 Starting Web MCP: {' '.join(cmd)}")
    with open(log_file, 'w') as f_log:
        process = subprocess.Popen(cmd, stdout=f_log, stderr=subprocess.STDOUT)
    _processes_to_cleanup.append(process)
    print(f"   -> Web MCP PID: {process.pid}, Log: {log_file}")
    return process, log_file

async def start_main_server(server_port: int, rag_port: int, web_port: int):
    """启动主小说生成服务器"""
    global _processes_to_cleanup
    cmd = [
        sys.executable, "src/umamusume_agent/main.py", # 注意：这是 src/umamusume_agent/main.py
        "-p", str(server_port),
        "-w", f"http://{DEFAULT_HOST}:{web_port}/mcp",
        "-r", f"http://{DEFAULT_HOST}:{rag_port}/mcp",
    ]
    log_file = f"{LOGS_DIR}/server.log"
    print(f"🚀 Starting Main Server: {' '.join(cmd)}")
    with open(log_file, 'w') as f_log:
        # 使用 subprocess.Popen 而非 asyncio.create_subprocess_exec
        # 因为我们希望它在前台运行并阻塞，直到用户想退出
        process = subprocess.Popen(cmd, stdout=f_log, stderr=subprocess.STDOUT)
    _processes_to_cleanup.append(process)
    print(f"   -> Main Server PID: {process.pid}, Log: {log_file}")
    return process, log_file

async def run_client(server_port: int, stream_mode: bool = True):
    """启动客户端
    
    Args:
        server_port: 服务器端口
        stream_mode: 是否使用流式模式（默认为 True）
    """
    server_url = f"http://{DEFAULT_HOST}:{server_port}"
    
    cmd = [
        sys.executable, "-m", "src.umamusume_agent.client.cli",
        "-u", server_url
    ]
    
    # 如果启用流式模式，添加 --stream 参数
    if stream_mode:
        cmd.append("--stream")
    
    mode_text = "流式" if stream_mode else "非流式"
    print(f"💬 Starting Client ({mode_text} 模式): {' '.join(cmd)}")
    
    # 客户端是交互式的，让它在前台运行并接管终端
    # 使用 subprocess.run 会阻塞，直到客户端退出
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Client exited with error: {e}")
    except KeyboardInterrupt:
        print("\n🛑 Client interrupted by user.")

# --- 主逻辑 ---
async def run_full_stack(
    rag_port: int, 
    web_port: int, 
    server_port: int, 
    start_client: bool = False,
    client_stream_mode: bool = True
):
    """运行完整的应用栈
    
    Args:
        rag_port: RAG MCP 服务端口
        web_port: Web MCP 服务端口
        server_port: 主服务器端口
        start_client: 是否启动客户端
        client_stream_mode: 客户端是否使用流式模式（默认为 True）
    """
    global _processes_to_cleanup

    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)

    print("=" * 60)
    print("  赛马娘交互式语音Agent - 完整服务栈启动".center(56))
    print("=" * 60)
    print(f"📌 环境: 请确保已激活 Python 虚拟环境")
    print(f"🔧 端口配置:")
    print(f"   • RAG MCP:    {rag_port}")
    print(f"   • Web MCP:    {web_port}")
    print(f"   • 主服务器:   {server_port}")
    if start_client:
        mode_text = "流式" if client_stream_mode else "非流式"
        print(f"   • 客户端:     {mode_text} 模式")
    print("=" * 60)
    print()

    try:
        # 1. 启动 RAG 和 Web MCP (并发)
        print("🚀 [步骤 1/3] 启动 MCP 服务...")
        rag_task = start_rag_mcp(rag_port)
        web_task = start_web_mcp(web_port)
        
        rag_process, rag_log = await rag_task
        web_process, web_log = await web_task

        # 2. 等待 RAG 和 Web MCP 就绪 (并发等待)
        rag_indicator = [f"Uvicorn running on http://{DEFAULT_HOST}:{rag_port}"]
        web_indicator = [f"Uvicorn running on http://{DEFAULT_HOST}:{web_port}"]
        
        wait_rag_task = wait_for_logs(rag_log, rag_indicator, timeout=600)
        wait_web_task = wait_for_logs(web_log, web_indicator, timeout=600)

        rag_ready, web_ready = await asyncio.gather(wait_rag_task, wait_web_task)

        if not (rag_ready and web_ready):
            print("❌ MCP 服务启动失败，退出。")
            cleanup()
            sys.exit(1)

        print("✅ MCP 服务就绪")
        print()

        # 3. 启动主服务器
        print("🚀 [步骤 2/3] 启动主服务器...")
        server_process, server_log = await start_main_server(server_port, rag_port, web_port)
        
        # 4. 等待主服务器就绪
        server_indicator = [f"Uvicorn running on http://{DEFAULT_HOST}:{server_port}", "Application startup complete"]
        server_ready = await wait_for_logs(server_log, server_indicator, timeout=30)
        
        if not server_ready:
            print("❌ 主服务器启动超时，退出。")
            cleanup()
            sys.exit(1)
        
        print("✅ 主服务器就绪")
        print()

        # 5. 启动客户端 (如果需要)
        if start_client:
            print("🚀 [步骤 3/3] 启动客户端...")
            await run_client(server_port, client_stream_mode)
        else:
            print("=" * 60)
            print("🟢 所有服务已启动并在后台运行")
            print("=" * 60)
            print(f"📍 服务地址:")
            print(f"   • 非流式: http://{DEFAULT_HOST}:{server_port}/ask")
            print(f"   • 流式:   http://{DEFAULT_HOST}:{server_port}/askstream")
            print(f"   • RAG MCP: http://{DEFAULT_HOST}:{rag_port}/mcp")
            print(f"   • Web MCP: http://{DEFAULT_HOST}:{web_port}/mcp")
            print()
            print(f"📄 日志目录: {LOGS_DIR}/")
            print(f"🛑 按 Ctrl+C 停止所有服务")
            print("=" * 60)
            
            # 如果不启动客户端，主进程需要等待中断信号
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                pass

    except Exception as e:
        print(f"\n💥 启动过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cleanup()
        print("\n👋 服务栈已完全关闭")

# --- 命令行入口 ---
def main():
    parser = argparse.ArgumentParser(
        description="赛马娘交互式语音Agent - 完整服务栈启动工具",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
使用示例:
  # 仅启动服务器（后台运行）
  python main.py server-only
  
  # 启动服务器和客户端（流式模式，默认）
  python main.py with-client
  
  # 启动服务器和客户端（非流式模式）
  python main.py with-client --no-stream
  
  # 自定义端口
  python main.py server-only -sp 8080 -rp 7777 -wp 7778
        """
    )
    
    # 位置参数：启动模式
    parser.add_argument(
        "action",
        nargs='?', 
        choices=['server-only', 'with-client'],
        default='server-only',
        help=(
            "启动模式:\n"
            "  server-only   - 仅启动服务 (默认)\n"
            "  with-client   - 启动服务 + 客户端"
        )
    )
    
    # 端口配置
    port_group = parser.add_argument_group('端口配置')
    port_group.add_argument(
        "-rp", "--rag-port", 
        type=int, 
        default=DEFAULT_RAG_PORT, 
        help=f"RAG MCP 端口 (默认: {DEFAULT_RAG_PORT})"
    )
    port_group.add_argument(
        "-wp", "--web-port", 
        type=int, 
        default=DEFAULT_WEB_PORT, 
        help=f"Web MCP 端口 (默认: {DEFAULT_WEB_PORT})"
    )
    port_group.add_argument(
        "-sp", "--server-port", 
        type=int, 
        default=DEFAULT_SERVER_PORT, 
        help=f"主服务器端口 (默认: {DEFAULT_SERVER_PORT})"
    )
    
    # 客户端配置
    client_group = parser.add_argument_group('客户端配置')
    client_group.add_argument(
        "--stream", 
        dest="stream_mode",
        action="store_true",
        default=True,
        help="客户端使用流式模式 (默认)"
    )
    client_group.add_argument(
        "--no-stream", 
        dest="stream_mode",
        action="store_false",
        help="客户端使用非流式模式"
    )
    
    args = parser.parse_args()

    # 确定是否启动客户端
    start_client = (args.action == 'with-client')

    # 运行异步主函数
    try:
        asyncio.run(run_full_stack(
            rag_port=args.rag_port,
            web_port=args.web_port,
            server_port=args.server_port,
            start_client=start_client,
            client_stream_mode=args.stream_mode
        ))
    except KeyboardInterrupt:
        print("\n🛑 收到中断信号")
    except Exception as e:
        print(f"\n💥 主进程发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cleanup()





if __name__ == "__main__":
    main()
