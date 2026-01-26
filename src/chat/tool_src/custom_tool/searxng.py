import httpx
import logging
from src.chat.tool_src.tool_base import BaseTool
from src.chat.chat_env import SEARXNG_URL

log = logging.getLogger(__name__)

ALLOWED_ENGINES = {
    "duckduckgo",
    "wikipedia",
    "github",
    "stackoverflow",
    "reddit",
    "google",
}

class SearxngTool(BaseTool):
    @property
    def name(self):
        return "internet_search"

    @property
    def description(self):
        return (
            "在互联网上搜索信息。返回结果仅包含简短摘要和URL。"
        )

    @property
    def parameters(self):
        return {
            "query": {
                "type": "STRING",
                "description": "搜索关键词"
            },
            "engines": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "可选。搜索引擎列表。可用引擎: duckduckgo, wikipedia, github, stackoverflow, reddit, google"
            },
            "time_range": {
                "type": "STRING",
                "description": "可选。时间范围: day, week, month, year。若不限时间则留空。"
            },
            "max_results": {
                "type": "INTEGER",
                "description": "最多返回的总结果数（5-20，默认 10）"
            }
        }

    async def execute(
        self,
        query: str,
        engines: list[str] | None = None,
        time_range: str = "",
        max_results: int = 10
    ) -> dict:
        log.info(f"SearXNG search: {query}")

        # ---------- 参数兜底 ----------
        max_results = max(5, min(max_results, 20))

        if not engines:
            engines = ["duckduckgo", "wikipedia"]

        engines = [e.lower().strip() for e in engines if e.lower().strip() in ALLOWED_ENGINES]
        if not engines:
            engines = ["duckduckgo"]

        params = {
            "q": query,
            "format": "json",
            "language": "zh-CN",
            "engines": ",".join(engines),
            "safesearch": 1,
        }

        if time_range in {"day", "week", "month", "year"}:
            params["time_range"] = time_range

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{SEARXNG_URL}/search", params=params)
                resp.raise_for_status()
                data = resp.json()

        except Exception as e:
            log.error(f"SearXNG error: {e}")
            return {
                "error": str(e),
                "results": []
            }

        raw_results = data.get("results") or data.get("content", {}).get("results") or []
        if not raw_results:
            return {
                "query": query,
                "results": []
            }
        logging.debug(f"tool_result:\n{raw_results}")

        # ---------- 结果压缩 ----------
        compact_results = []
        for item in raw_results:
            if len(compact_results) >= max_results:
                break

            snippet = (item.get("content") or item.get("snippet") or "").strip()
            snippet = snippet.replace("\n", " ").replace("\r", " ")

            if len(snippet) > 500:
                snippet = snippet[:500] + "..."
            else:
                # 不够的话，用原长度全部保留
                snippet = snippet

            compact_results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "engine": item.get("engine", "unknown"),
                "snippet": snippet,
            })
        
        return {
            "query": query,
            "engines_used": engines,
            "time_range": time_range or "不限",
            "results": compact_results
        }
