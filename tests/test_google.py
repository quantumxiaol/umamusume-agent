import os,sys
import asyncio
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# from search.bingsearch import search_bing
from umamusume_agent.search.googlesearch import google_search_urls
from umamusume_agent.crawler.crawlonweb import get_uma_info_bing,get_uma_info_on_bilibili_wiki

query_name="爱慕织姬"

query_wrong_name="爱慕guangzuan"



async def main():


    query_str=f"{query_name} site:wiki.biligame.com/umamusume"

    query_miss_str=f"{query_wrong_name} site:wiki.biligame.com/umamusume"

    print(f"search in Google for {query_str}\n",google_search_urls(search_term=query_str))

    print(f"search in Google for {query_miss_str}\n",google_search_urls(search_term=query_miss_str))


    # print("Google")

    # result1 = await get_uma_info_bing(query_name)
    # print(result1)

    # result2 = await get_uma_info_bing(query_wrong_name)
    # print(result2)


    # print("Bilibili wiki")
    # result3 = await get_uma_info_on_bilibili_wiki(query_name)
    # print(result3)

    # result4 = await get_uma_info_on_bilibili_wiki(query_wrong_name)
    # print(result4)

if __name__ == "__main__":
    

    # 运行异步主函数
    asyncio.run(main())