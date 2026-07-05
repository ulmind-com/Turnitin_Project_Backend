from tavily import AsyncTavilyClient
from app.config import settings
from typing import Optional
import traceback


def get_tavily_client() -> AsyncTavilyClient:
    """Get an AsyncTavilyClient instance."""
    return AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)


async def search_web_for_chunk(chunk_text: str, key_phrases: list[str]) -> list[dict]:
    """
    Search the web for content matching the given text chunk.
    
    Strategy:
      1. First try exact-match quoted search (best for detecting direct copy-paste)
      2. If no results, fall back to semantic search (catches paraphrasing)
      3. Log every step so failures are visible in server logs
    """
    if not settings.TAVILY_API_KEY:
        print("⚠️ TAVILY_API_KEY not set — skipping web search")
        return []

    client = get_tavily_client()
    all_results = []
    seen_urls = set()

    for phrase in key_phrases[:2]:
        # ── Attempt 1: Exact match with quotes ──
        try:
            exact_query = f'"{phrase}"'
            print(f"🔍 Tavily EXACT search: {exact_query[:60]}...")
            response = await client.search(
                query=exact_query,
                search_depth="basic",
                max_results=3,
                include_raw_content=False,
            )
            results = response.get("results", [])
            print(f"   → Got {len(results)} exact results")

            for result in results:
                url = result.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append({
                        "url": url,
                        "title": result.get("title", ""),
                        "content": result.get("content", "")[:500],
                        "score": result.get("score", 0),
                    })
        except Exception as e:
            print(f"⚠️ Tavily EXACT search error: {e}")

        # ── Attempt 2: Semantic search (broader net) ──
        if not all_results:
            try:
                print(f"🔍 Tavily SEMANTIC search: {phrase[:60]}...")
                response = await client.search(
                    query=phrase,
                    search_depth="basic",
                    max_results=5,
                    include_raw_content=False,
                )
                results = response.get("results", [])
                print(f"   → Got {len(results)} semantic results")

                for result in results:
                    url = result.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append({
                            "url": url,
                            "title": result.get("title", ""),
                            "content": result.get("content", "")[:500],
                            "score": result.get("score", 0),
                        })
            except Exception as e:
                print(f"⚠️ Tavily SEMANTIC search error: {e}")
                traceback.print_exc()

    print(f"📊 Tavily total results for chunk: {len(all_results)} sources")
    return all_results
