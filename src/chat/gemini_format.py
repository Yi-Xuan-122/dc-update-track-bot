import base64
import httpx
import hashlib
from collections import OrderedDict
from typing import Optional, List, Dict, Any
import logging
log = logging.getLogger(__name__)

class GeminiImageManager:
    def __init__(self, max_items: int = 100, ram_cache_limit_mb: int = 2):
        # 使用 OrderedDict 实现 LRU 缓存
        # Key: URL的MD5哈希
        # Value: Base64 字符串
        self.cache: OrderedDict[str, str] = OrderedDict()
        self.max_items = max_items
        self.ram_cache_limit = ram_cache_limit_mb * 1024 * 1024  # 转换为字节

    def _get_url_hash(self, url: str) -> str:
        """生成URL的唯一索引"""
        return hashlib.md5(url.encode('utf-8')).hexdigest()

    async def get_image_base64(self, url: str) -> Optional[str]:
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
                
                img_content = resp.content
                img_size = len(img_content)
                
                # 执行转码
                b64_data = base64.b64encode(img_content).decode('utf-8')

                # 3. 缓存决策逻辑
                # 只有小于设定阈值 (2MB) 的图片才进入 RAM 缓存
                if img_size <= self.ram_cache_limit:
                    if len(self.cache) >= self.max_items:
                        # 弹出最旧的一个 (先进先出)
                        old_key, _ = self.cache.popitem(last=False)
                        log.debug(f"Cache Evicted: {old_key}")
                    
                    self.cache[url_hash] = b64_data
                    log.debug(f"Cache Stored: {url_hash} ({img_size/1024:.1f} KB)")
                else:
                    log.debug(f"Image too large for RAM cache: {img_size/1024/1024:.1f} MB")

                return b64_data

        except Exception as e:
            log.error(f"Gemini Image Fetch Error: {e}")
            return None

    def format_gemini_contents(self, text: str, b64_list: List[str], history_contents: List[Dict] = None) -> List[Dict]:
        """构造 Gemini 原生的 contents 结构"""
        new_parts = []
        
        if text:
            new_parts.append({"text": text})
        
        if b64_list:
            for b64 in b64_list:
                new_parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": b64
                    }
                })
        
        # 如果没有历史记录，初始化它
        if history_contents is None:
            history_contents = [{"role": "user", "parts": new_parts}]
        else:
            # 在已有的 contents 中追加
            history_contents[0]["parts"].extend(new_parts)
            
        return history_contents

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
            b64_data = await gemini_manager.get_image_base64(url)
            if b64_data:
                new_parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg", # 或者是根据url判断
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