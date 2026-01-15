from googleapiclient.discovery import build
from httplib2 import Http, ProxyInfo
from typing import List, Dict, Optional, Any
from ..config import config
config.validate()
# 获取 Google API 配置
my_api_key = config.GOOGLE_API_KEY
my_cse_id = config.GOOGLE_CSE_ID

# 获取代理配置
proxy_info: Optional[ProxyInfo] = None
if config.PROXY_TYPE and config.PROXY_HOST and config.PROXY_PORT:
    proxy_type = 3 if config.PROXY_TYPE.lower() == "http" else None
    if proxy_type is None:
        raise ValueError(f"Unsupported proxy type: {config.PROXY_TYPE}")
    proxy_info = ProxyInfo(
        proxy_type=proxy_type,
        proxy_host=config.PROXY_HOST,
        proxy_port=config.PROXY_PORT
    )

# 创建带代理设置的 Http 实例
http = Http(proxy_info=proxy_info) if proxy_info else None


def extract_formatted_urls(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    提取搜索结果中的 formattedUrl 和优先级（排名）
    """
    return [
        {'url': item['formattedUrl'], 'priority': idx + 1}
        for idx, item in enumerate(results)
        if 'formattedUrl' in item
    ]


def google_search(search_term: str, **kwargs) -> List[Dict[str, Any]]:
    """
    使用 Google Custom Search API 搜索
    """
    service = build("customsearch", "v1", developerKey=my_api_key, http=http)
    res = service.cse().list(q=search_term, cx=my_cse_id, **kwargs).execute()
    return extract_formatted_urls(res.get('items', []))


def google_search_urls(search_term: str, **kwargs) -> List[Dict[str, Any]]:
    """
    搜索并返回 URL 列表（带优先级）
    """
    urls = google_search(search_term, **kwargs)
    if not urls:
        raise ValueError("No results found")
    return urls


def google_search_all(search_term: str, **kwargs) -> List[Dict[str, Any]]:
    """
    返回完整的搜索结果
    """
    service = build("customsearch", "v1", developerKey=my_api_key, http=http)
    res = service.cse().list(q=search_term, cx=my_cse_id, **kwargs).execute()
    items = res.get('items', [])
    if not items:
        raise ValueError("No results found")
    return items