from tavily import AsyncTavilyClient
from app.config import settings
from typing import Optional


def get_tavily_client() -> AsyncTavilyClient:
    """Get an AsyncTavilyClient instance."""
    return AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)


async def search_web_for_chunk(chunk_text: str, key_phrases: list[str]) -> list[dict]:
    """
    Search the web for content matching the given text chunk.

    Uses key phrases extracted from the chunk to find potential source matches.

    Args:
        chunk_text: The full text of the chunk.
        key_phrases: List of key phrases extracted from the chunk.

    Returns:
        List of matching sources with url, title, and content snippet.
    """
    if not settings.TAVILY_API_KEY:
        return []

    client = get_tavily_client()
    all_results = []
    seen_urls = set()

    for phrase in key_phrases[:2]:  # limit to 2 searches per chunk for speed
        try:
            # Do NOT use exact match quotes, let Tavily's semantic search do the work.
            # Exact matching is too brittle and often returns 0 results for slightly modified text.
            response = await client.search(
                query=phrase,
                search_depth="basic",
                max_results=3,
                include_raw_content=False,
            )

            for result in response.get("results", []):
                url = result.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(
                        {
                            "url": url,
                            "title": result.get("title", ""),
                            "content": result.get("content", "")[:500],
                            "score": result.get("score", 0),
                        }
                    )
        except Exception as e:
            # Log but don't fail — web search is best-effort
            print(f"Tavily search error for phrase '{phrase[:30]}...': {e}")
            continue

    return all_results
