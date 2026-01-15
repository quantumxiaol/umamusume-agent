"""
Search And Crawl MCP Server

python webinfomcp.py --http -p 7777
to activate the MCP server

    playwright install


"""
import os
import asyncio
import sys
import argparse
import uvicorn
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

from typing import List,Dict
from ..crawler.crawlonweb import get_uma_info_bing,get_uma_info_on_bilibili_wiki
from ..crawler.crawlonweb import get_uma_info_bing_biliwiki,get_uma_info_bing_moegirl

from ..crawler.crawlonweb import _crawl_page,_crawl_page_proxy
from ..search.bingsearch import search_bing
from ..search.googlesearch import google_search_urls


mcp = FastMCP("Web Search MCP")

@mcp.tool(description="""
Performs a web search with google for the given query and returns a list of relevant URLs with ranking.
Use this tool first to find potential pages about a character, event, or topic.
The model can then choose which URLs to crawl using the crawl_web_page tool.
Returns up to 5 results ranked by relevance.

You can use site:wiki.biligame.com/umamusume to search for pages within the Bilibili wiki.
You can use site:site:mzh.moegirl.org.cn to search for pages within the moegirl wiki.
          
Parameters:
- query: The full search query, including keywords and site restrictions if needed.
       Example: "爱慕织姬 site:wiki.biligame.com/umamusume"
""")
async def web_search_google(query: str) -> Dict[str, List[Dict[str, str]]]:
    """
    Search the web using Google Custom Search API and return ranked URLs.
    Does NOT crawl the pages.
    """
    try:
        # 使用 Google 搜索（更稳定）
        results = google_search_urls(search_term=query, num=5)
        # 如果使用 Bing，也可以替换为 search_bing 并提取 links
        # results = [{'url': r['link'], 'priority': i+1} for i, r in enumerate(search_bing(query)['results'])]
        results = [
            {"url": item["url"], "priority": str(item["priority"])}
            for item in results
        ]
        print("\nGoogle:\n")
        print(query)
        print(results)
        return {
            "results": results,
            "error": None
        }

    except Exception as e:
        return {
            "results": [],
            "error": str(e)
        }

@mcp.tool(description="""
Performs a web search with bing engine for the given query and returns a list of relevant URLs with ranking.
Use this tool first to find potential pages about a character, event, or topic.
The model can then choose which URLs to crawl using the crawl_web_page tool.
Returns up to 5 results ranked by relevance.

You can use site:wiki.biligame.com/umamusume to search for pages within the Bilibili wiki.
You can use site:site:mzh.moegirl.org.cn to search for pages within the moegirl wiki.
          
Parameters:
- query: The full search query, including keywords and site restrictions if needed.
       Example: "爱慕织姬 site:wiki.biligame.com/umamusume"
""")
async def web_search_bing(query: str) -> Dict[str, List[Dict[str, str]]]:
    """
    Search the web using Bing Search API and return ranked URLs.
    Does NOT crawl the pages.
    Returns results in the format: {"results": [{"url": "...", "rank": "1"}, ...]}
    """
    try:
        bing_response = search_bing(query)
        
        # 检查是否有错误
        if "error" in bing_response:
            return {
                "results": [],
                "error": bing_response["error"]
            }
        
        # 提取 results 列表
        bing_results = bing_response.get("results", [])
        
        # 转换格式：从 Bing 的 result_id/title/snippet/link 转为 url/rank
        formatted_results = []
        for item in bing_results:
            formatted_results.append({
                "url": item["link"],       # 使用 link 作为 url
                "rank": str(item["result_id"])
            })
        print("\nBing:\n")
        print(query)
        print(formatted_results)
        
        return {
            "results": formatted_results
        }

    except Exception as e:
        return {
            "results": [],
            "error": str(e)
        }

@mcp.tool(description="""
          Crawl a page from url and return the result as markdown.
          For website in mainland china, please use this tool.
          """,
)
async def crawl_page(url: str):
    try:
        result=await _crawl_page(url)
        # print("Raw result type:", type(result))         # 查看类型
        # print("Raw result (first 500 chars):", str(result)[:500])  # 打印部分原始内容
        # print(result)
        return {
            "status": "success",
            "result": str(result)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
       }


@mcp.tool(description="""
          Crawl a page from url via local proxy,
          able to access wikipedia ,github .etc,
          and return the result as markdown
          """,
)
async def crawl_page_via_proxy(url: str):
    try:
        print("start crawl_page_via_proxy")
        result=await _crawl_page_proxy(url)
        # print("Raw result type:", type(result))         # 查看类型
        # print("Raw result (first 500 chars):", str(result)[:500])  # 打印部分原始内容
        # print(result)
        return {
            "status": "success",
            "result": str(result)
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

web_mcp_app = create_starlette_app(mcp._mcp_server, debug=True)
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
            port=args.port if args.port else 7777,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()