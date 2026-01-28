import base64
import httpx
import hashlib
from collections import OrderedDict
from typing import Optional, List, Dict, Any, Tuple
import logging
log = logging.getLogger(__name__)

class GeminiImageManager:
    # 严格白名单：允许的 MIME 类型映射
    ALLOWED_MIMES = {
        "image/png": "image/png",
        "image/jpeg": "image/jpeg",
        "image/jpg": "image/jpeg",  # 兼容 jpg 写法
        "image/webp": "image/webp",
        "image/heic": "image/heic",
        "image/heif": "image/heif"
    }

    def __init__(self, max_items: int = 100, ram_cache_limit_mb: int = 2):
        # Value: (Base64字符串, MimeType字符串)
        self.cache: OrderedDict[str, Tuple[str, str]] = OrderedDict()
        self.max_items = max_items
        self.ram_cache_limit = ram_cache_limit_mb * 1024 * 1024  # 转换为字节

    def _get_url_hash(self, url: str) -> str:
        """生成URL的唯一索引"""
        return hashlib.md5(url.encode('utf-8')).hexdigest()

    def _guess_mime_from_url(self, url: str) -> Optional[str]:
        """尝试从URL后缀推断MIME"""
        clean_url = url.split('?')[0].lower()
        if clean_url.endswith(".png"): return "image/png"
        if clean_url.endswith((".jpg", ".jpeg")): return "image/jpeg"
        if clean_url.endswith(".webp"): return "image/webp"
        if clean_url.endswith(".heic"): return "image/heic"
        if clean_url.endswith(".heif"): return "image/heif"
        return None

    async def get_image_base64(self, url: str) -> Optional[Tuple[str, str]]:
        url_hash = self._get_url_hash(url)

        # 1. 命中缓存逻辑
        if url_hash in self.cache:
            log.debug(f"Cache Hit: {url_hash}")
            # 移动到末尾，表示最近使用过
            self.cache.move_to_end(url_hash)
            return self.cache[url_hash]

        # 2. 未命中，执行下载
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                
                # --- MIME 校验逻辑 ---
                header_mime = resp.headers.get("Content-Type", "").lower()
                if ";" in header_mime:
                    header_mime = header_mime.split(";")[0].strip()
                
                final_mime = None
                
                # A. 优先信任 Header 且 Header 在白名单中
                if header_mime in self.ALLOWED_MIMES:
                    final_mime = self.ALLOWED_MIMES[header_mime]
                else:
                    # B. Header 不明确或不在白名单，尝试根据 URL 后缀补救
                    url_mime = self._guess_mime_from_url(url)
                    if url_mime and url_mime in self.ALLOWED_MIMES:
                        final_mime = self.ALLOWED_MIMES[url_mime]
                
                # C. 如果最终未识别出有效格式，则丢弃
                if not final_mime:
                    log.warning(f"Skipped unsupported or unknown image format: {url} (Header: {header_mime})")
                    return None
                
                # ---------------------
                
                img_content = resp.content
                img_size = len(img_content)
                
                # 执行转码
                b64_data = base64.b64encode(img_content).decode('utf-8')
                result = (b64_data, final_mime)

                # 3. 缓存决策逻辑
                if img_size <= self.ram_cache_limit:
                    if len(self.cache) >= self.max_items:
                        # 弹出最旧的一个 (先进先出)
                        old_key, _ = self.cache.popitem(last=False)
                        log.debug(f"Cache Evicted: {old_key}")
                    
                    self.cache[url_hash] = result
                    log.debug(f"Cache Stored: {url_hash} ({img_size/1024:.1f} KB) as {final_mime}")
                else:
                    log.debug(f"Image too large for RAM cache: {img_size/1024/1024:.1f} MB")

                return result

        except Exception as e:
            log.error(f"Gemini Image Fetch Error: {e}")
            return None

# 全局单例，保证整个程序生命周期内缓存有效
gemini_manager = GeminiImageManager()

async def gemini_format_callback(text: str, img_urls: Optional[List[str]] = None, main_prompt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    针对 Gemini 原生格式优化的回调
    返回格式: {"contents": [{"role": "user", "parts": [...]}]}
    """
    from src.config import IMG_VIEW
    import copy

    # 初始化或深拷贝
    if main_prompt is None:
        prompt = {"contents": []}
    else:
        prompt = copy.deepcopy(main_prompt)

    new_parts = []
    
    # 1. 处理文本部分
    if text:
        new_parts.append({"text": text})

    # 2. 处理图片部分 (转为 inline_data)
    if img_urls and IMG_VIEW:
        for url in img_urls:
            # 获取数据和类型
            result = await gemini_manager.get_image_base64(url)
            if result:
                b64_data, mime_type = result
                new_parts.append({
                    "inline_data": {
                        "mime_type": mime_type, 
                        "data": b64_data
                    }
                })

    if not new_parts:
        return prompt

    # 3. 构造或追加到 contents
    # 如果最后一条消息是 user，则追加到该消息的 parts 中（合并上下文）
    if (prompt["contents"] and prompt["contents"][-1].get("role") == "user"):
        prompt["contents"][-1]["parts"].extend(new_parts)
    else:
        prompt["contents"].append({
            "role": "user",
            "parts": new_parts
        })

    return prompt