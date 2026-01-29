import httpx
import logging
import trafilatura
from src.chat.tool_src.tool_base import BaseTool

# 设置 logger
log = logging.getLogger(__name__)

class WebPageContextTool(BaseTool):
    @property
    def name(self):
        return "webpage_context"

    @property
    def description(self):
        return (
            """
            当出现https、http链接需要查看时，提供url，返回网页内的内容。
            回复时记得引用对应的URL。
            **特别的，当你需要浏览github上某个文件，请转换为:`https://raw.githubusercontent.com/.../file.example`格式**
            <Tool_Think>
            1.禁止使用捏造或自己构造的URL，必须是上下文中包含的或网络查找后的合理URL
            2.调用后必须优先解析http状态码，若非200则根据任务要求判断是否中止当前任务并承认任务失败。例如:
             - 429:尝试抓取其他镜像站(若有)
             - 403/404/500/503/其他错误码:承认任务失败，并显式的报告具体错误。
            3.若抓取成功的网页内容包含‘Enable JavaScript’、‘CAPTCHA’或仅有大量CSS/JS代码而无实质文本，需判定为抓取失败，并尝试寻找镜像站或缓存。
            </Tool_Think>
            """
        )

    @property
    def parameters(self):
        return {
            "url": {
                "type": "STRING",
                "description": "需要抓取的网页 URL"
            },
            "max_length": {
                "type": "INTEGER",
                "description": "返回的最大字符数（默认 5000）最大20000"
            }
        }

    async def execute(self, url: str, max_length: int = 5000,**kwargs) -> dict:
        log.info(f">>> [WebPageContextTool] Start fetching: {url}")
        max_length = max(1, min(max_length, 20000))

        try:
            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                
                print(f"\n[DEBUG] URL: {url} | Status Code: {resp.status_code}")
                
        except Exception as e:
            log.error(f"Fetch failed: {e}")
            return {
                "results": [],
                "error": str(e),
                "notice": f"抓取失败: {str(e)}"
            }

        # 获取原始 HTML
        raw_html = resp.text
        
        log.debug(f"Raw HTML Length: {len(raw_html)}")
        log.debug(f"Raw HTML Preview (First 500 chars):\n{'-'*20}\n{raw_html[:500]}\n{'-'*20}\n")

        cleaned = trafilatura.extract(
            raw_html,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
            deduplicate=True
        )

        cleaned = cleaned.strip() if cleaned else ""

        log.debug(
            "Web clean stats url=%s raw_len=%d trafilatura_len=%d",
            url,
            len(raw_html),
            len(cleaned),
        )


        final_content = cleaned
        fallback_flag = None

        if not final_content:
            if len(raw_html) > 0:
                log.warning("Trafilatura extract failed (empty), fallback to raw HTML snippet")
                final_content = raw_html[:max_length] 
                fallback_flag = "raw_html"
            else:
                final_content = "页面内容为空"

        if len(final_content) > max_length:
            final_content = final_content[:max_length] + "..."

        return {
            "results": [
                {
                    "url": url,
                    "content": final_content,
                    "length": len(final_content),
                    "fallback": fallback_flag
                }
            ]
        }