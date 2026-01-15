import asyncio
import os

from crawl4ai import AsyncWebCrawler
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter
from urllib.parse import urljoin, quote
from typing import List
from crawl4ai.async_configs import ProxyConfig, CrawlerRunConfig
from ..search.bingsearch import search_bing
from ..search.googlesearch import google_search_urls
from ..config import config
config.validate()



class SingleProxyRotationStrategy:
    def __init__(self, proxy_config):
        self.proxy_config = proxy_config
    
    async def get_next_proxy(self):
        
        return self.proxy_config
    
# 创建单一代理配置
proxy_url = config.HTTP_PROXY
single_proxy = ProxyConfig(server=proxy_url)
md_generator = DefaultMarkdownGenerator(
    options={
        "ignore_links": True,
        "ignore_images": True,
        "escape_html": False,
        "body_width": 80
    }
)
prune_filter = PruningContentFilter(
    threshold=0.5,
    threshold_type="fixed",  # or "dynamic"
    min_word_threshold=50
)
# 使用单一代理配置初始化代理轮换策略
proxy_rotation_strategy = SingleProxyRotationStrategy(single_proxy)
crawler_config = CrawlerRunConfig(
    markdown_generator=md_generator,
    excluded_selector=".ads, .comments, #sidebar",
    )

crawler_config_proxy = CrawlerRunConfig(
    markdown_generator=md_generator,
    proxy_rotation_strategy=proxy_rotation_strategy,
    excluded_selector=".ads, .comments, #sidebar",
    )
async def _crawl_page(url):
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url=url,config=crawler_config)
        return result.markdown

async def _crawl_page_proxy(url):
    print("Use Proxy URL",proxy_url)
    async with AsyncWebCrawler(verbose=True) as crawler:
        
        if proxy_url is None:
            result = await crawler.arun(url=url,config=crawler_config)
        else:
            result = await crawler.arun(url=url, config=crawler_config_proxy)
        return result.markdown
    


async def get_uma_info_on_bilibili_wiki(uma_name:str):
    """
    uma_name 爱慕织姬
    bilibi_wiki_url : https://wiki.biligame.com/umamusume/
    
    final_url : https://wiki.biligame.com/umamusume/%E7%88%B1%E6%85%95%E7%BB%87%E5%A7%AC

    craw; on final_url and return the markdown

    """
    encoded_name = quote(uma_name)
    base_bilibi_wiki_url = "https://wiki.biligame.com/umamusume/"
    final_url = urljoin(base_bilibi_wiki_url, encoded_name)
    # markdown = asyncio.run(_crawl_page_proxy(final_url))
    markdown = await _crawl_page(final_url)
    return markdown

async def get_uma_info_bing(uma_name:str,max_num_results:int=5):
    site=" site:wiki.biligame.com/umamusume"
    result_dict=search_bing(uma_name+site,max_num_results)
    urls=[result['link'] for result in result_dict['results']]
    # markdowns=[asyncio.run(_crawl_page(url)) for url in urls]
    tasks = [_crawl_page(url) for url in urls]
    markdowns = await asyncio.gather(*tasks)
    return markdowns

async def get_uma_info_bing_biliwiki(uma_name: str, max_num_results: int = 5):
    """
    在bilibili wiki上搜索角色信息
    """
    site = "site:wiki.biligame.com/umamusume"
    query = f"{quote(uma_name)} {site}"
    result_dict = search_bing(query, max_num_results)
    
    if 'error' in result_dict:
        print(f"Error encountered during Bing search for {query}: {result_dict['error']}")
        return []
    
    urls = [result['link'] for result in result_dict.get('results', [])]
    tasks = [_crawl_page(url) for url in urls]
    markdowns = await asyncio.gather(*tasks)
    return markdowns

async def get_uma_info_bing_moegirl(uma_name: str, max_num_results: int = 5, proxy_url: str = None):
    """
    在萌娘百科上搜索角色信息
    如果需要，可以使用代理
    """
    site = "site:mzh.moegirl.org.cn"
    query = f"{quote(uma_name)} {site}"
    result_dict = search_bing(query, max_num_results)
    
    if 'error' in result_dict:
        print(f"Error encountered during Bing search for {query}: {result_dict['error']}")
        return []
    
    urls = [result['link'] for result in result_dict.get('results', [])]
    # 使用代理爬取页面内容
    tasks = [_crawl_page_proxy(url, proxy_url) for url in urls]
    markdowns = await asyncio.gather(*tasks)
    return markdowns

"""
.moegirl.org.cn
moegirl.uk
"""

async def get_uma_info_google_biliwiki(uma_name: str, max_results: int = 5):
    """
    使用 Google 搜索 Bilibili 赛马娘 Wiki 页面
    """
    search_term = f"{uma_name} site:wiki.biligame.com/umamusume"
    try:
        url_dicts = google_search_urls(search_term, num=max_results)
        urls = [item['url'] for item in url_dicts]
        tasks = [_crawl_page(url) for url in urls]
        markdowns = await asyncio.gather(*tasks)
        return markdowns
    except Exception as e:
        return [f"❌ Bilibili Wiki Search Error: {str(e)}"]
    
async def get_uma_info_google_moegirl(uma_name: str, max_results: int = 5, proxy_url: str = None):
    """
    使用 Google 搜索萌娘百科页面（可选代理）
    """
    search_term = f"{uma_name} site:mzh.moegirl.org.cn"
    try:
        url_dicts = google_search_urls(search_term, num=max_results)
        urls = [item['url'] for item in url_dicts]
        tasks = [_crawl_page_proxy(url, proxy_url) for url in urls]
        markdowns = await asyncio.gather(*tasks)
        return markdowns
    except Exception as e:
        return [f"❌ Moegirl Wiki Search Error: {str(e)}"]