from bs4 import BeautifulSoup
from typing import Any, Dict, List, cast
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup, Tag

def search_bing(query: str, max_results: int = 5) -> Dict[str, Any]:
    r"""Use Bing search engine to search information for the given query.

    This function queries the Chinese version of Bing search engine (cn.
    bing.com) using web scraping to retrieve relevant search results. It
    extracts search results including titles, snippets, and URLs. This
    function is particularly useful when the query is in Chinese or when
    Chinese search results are desired.

    Args:
        query (str): The search query string to submit to Bing. Works best
            with Chinese queries or when Chinese results are preferred.
        max_results (int): Maximum number of results to return.
            (default: :obj:`5`)

    Returns:
        Dict ([str, Any]): A dictionary containing either:
            - 'results': A list of dictionaries, each with:
                - 'result_id': The index of the result.
                - 'snippet': A brief description of the search result.
                - 'title': The title of the search result.
                - 'link': The URL of the search result.
            - or 'error': An error message if something went wrong.
    """


    try:
        query = urlencode({"q": query})
        url = f'https://cn.bing.com/search?{query}'
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        # Add timeout to prevent hanging
        response = requests.get(url, headers=headers, timeout=10)

        # Check if the request was successful
        if response.status_code != 200:
            return {
                "error": (
                    f"Bing returned status code: "
                    f"{response.status_code}"
                )
            }

        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        b_results_element = soup.find("ol", id="b_results")
        if b_results_element is None:
            return {"results": []}

        # Ensure b_results is a Tag and find all li elements
        b_results_tag = cast(Tag, b_results_element)
        result_items = b_results_tag.find_all("li")

        results: List[Dict[str, Any]] = []
        for i in range(min(len(result_items), max_results)):
            row = result_items[i]
            if not isinstance(row, Tag):
                continue

            h2_element = row.find("h2")
            if h2_element is None:
                continue
            h2_tag = cast(Tag, h2_element)

            title = h2_tag.get_text().strip()

            link_tag_element = h2_tag.find("a")
            if link_tag_element is None:
                continue
            link_tag = cast(Tag, link_tag_element)

            link = link_tag.get("href")
            if link is None:
                continue

            content_element = row.find("p", class_="b_algoSlug")
            content_text = ""
            if content_element is not None and isinstance(
                content_element, Tag
            ):
                content_text = content_element.get_text()

            row_data = {
                "result_id": i + 1,
                "snippet": content_text,
                "title": title,
                "link": link,
            }
            results.append(row_data)

        if not results:
            return {
                "warning": "No results found. Check if "
                "Bing HTML structure has changed."
            }

        return {"results": results}

    except Exception as e:
        return {"error": f"Bing scraping error: {e!s}"}