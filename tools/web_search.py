import re
import html as html_lib
import requests

def search_web_contents(query: str) -> dict:
    results = []

    # 1. Try DuckDuckGo Lite Custom Scraping (highly reliable, no API key or JS needed)
    try:
        url = "https://lite.duckduckgo.com/lite/"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        res = requests.post(url, data={"q": query}, headers=headers, timeout=6)
        if res.status_code == 200:
            # H03: Bulletproof regex matching either href first or class first, with flexible quote types
            raw_links = re.findall(
                r'<a[^>]*class=[\x27"]result-link[\x27"][^>]*href=[\x27"]([^\x27"]+)[\x27"][^>]*>(.*?)</a>|'
                r'<a[^>]*href=[\x27"]([^\x27"]+)[\x27"][^>]*class=[\x27"]result-link[\x27"][^>]*>(.*?)</a>',
                res.text,
                re.DOTALL
            )
            parsed_links = []
            for m in raw_links:
                href = m[0] or m[2]
                title = m[1] or m[3]
                if href and title:
                    parsed_links.append((href, title))

            snippets = re.findall(
                r'<td[^>]*class=[\x27"]result-snippet[\x27"][^>]*>(.*?)</td>',
                res.text,
                re.DOTALL,
            )
            for i in range(min(len(parsed_links), len(snippets), 5)):
                href, title = parsed_links[i]
                title = re.sub(r"<[^>]+>", "", title)
                title = html_lib.unescape(title).strip()
                snippet = re.sub(r"<[^>]+>", "", snippets[i])
                snippet = html_lib.unescape(snippet).strip()
                snippet = re.sub(r"\s+", " ", snippet)

                # Exclude internal/ad links if possible
                if "duckduckgo.com" in href and ("y.js" in href or "company" in href):
                    continue
                results.append({"title": title, "snippet": snippet, "url": href})
    except Exception:
        pass

    # 2. Try Wikipedia API Search (with proper User-Agent header to avoid 403 Forbidden)
    if len(results) < 5:
        try:
            url = "https://en.wikipedia.org/w/api.php"
            params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "utf8": 1,
            }
            headers = {
                "User-Agent": "LivePythonGemini/1.0 (https://github.com/vinoth/livepythongemini; vinoth.live2d@gmail.com)"
            }
            res = requests.get(url, params=params, headers=headers, timeout=6)
            if res.status_code == 200:
                data = res.json()
                for r in data.get("query", {}).get("search", [])[:5]:
                    title = r.get("title")
                    snippet = re.sub(r"<[^>]+>", "", r.get("snippet"))
                    snippet = html_lib.unescape(snippet).strip()
                    snippet = re.sub(r"\s+", " ", snippet)
                    page_id = r.get("pageid")

                    results.append(
                        {
                            "title": title,
                            "snippet": snippet,
                            "url": f"https://en.wikipedia.org/?curid={page_id}",
                        }
                    )
        except Exception:
            pass

    if not results:
        return {
            "query": query,
            "results": [],
            "status": "No text results found. You can suggest opening the browser for the user.",
        }

    # Deduplicate by url
    seen_urls = set()
    unique_results = []
    for r in results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique_results.append(r)

    return {"query": query, "results": unique_results[:5]}
