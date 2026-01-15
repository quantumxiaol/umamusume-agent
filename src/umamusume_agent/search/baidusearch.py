from bs4 import BeautifulSoup
from typing import Any, Dict, List, cast
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup, Tag
from lxml import html

def search_baidu(query: str, max_results: int = 5) -> Dict[str, Any]:
    r"""Search Baidu using web scraping to retrieve relevant search
    results. This method queries Baidu's search engine and extracts search
    results including titles, descriptions, and URLs.

    Args:
        query (str): Search query string to submit to Baidu.
        max_results (int): Maximum number of results to return.
            (default: :obj:`5`)

    Returns:
        Dict[str, Any]: A dictionary containing search results or error
            message.
    """
    try:
        url = "https://www.baidu.com/s"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.baidu.com",
        }
        params = {"wd": query, "rn": str(max_results)}

        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code != 200:
            raise RuntimeError(f"Request failed with status code {response.status_code}")

        response.encoding = "utf-8"

        # 使用 lxml 解析 HTML
        tree = html.fromstring(response.text)

        # 提取所有搜索结果容器
        items = tree.xpath('//div[@id="content_left"]/div[contains(@class, "c-container")]')

        results = []
        for idx, item in enumerate(items, 1):
            # 提取标题
            title_list = item.xpath('.//h3/a//text()')
            title = ''.join(title_list).strip() if title_list else ""

            # 提取链接
            link_list = item.xpath('.//h3/a/@href')
            link = link_list[0] if link_list else ""

            # 提取描述
            desc_list = item.xpath(
                './/div[contains(@class, "c-abstract") or contains(@class, "c-line-clamp1")]//text()'
            )
            desc = ''.join(desc_list).strip() if desc_list else ""

            results.append({
                "result_id": idx,
                "title": title,
                "description": desc,
                "url": link,
            })

            if len(results) >= max_results:
                break

        if not results:
            print("Warning: No results found. Check if Baidu HTML structure has changed.")
            first_container = tree.xpath('//div[@id="content_left"]/div[contains(@class, "c-container")]')
            if first_container:
                print(html.tostring(first_container[0], pretty_print=True, encoding='unicode'))
            else:
                print("No containers found with selector '#content_left .c-container'")
            raise ValueError("No results found")

        return {"results": results}

    except requests.exceptions.RequestException as request_err:
        raise RuntimeError(f"Network error occurred: {request_err}") from request_err
    except Exception as e:
        raise RuntimeError(f"Baidu scraping error: {e!s}") from e