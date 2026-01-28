import discord
import logging
import time
import json
from typing import Dict, Any, Optional
from src.chat.tool_src.tool_base import BaseTool
from src.config import TARGET_GUILD_ID, ADMIN_IDS
from src.chat.gemini_format import gemini_manager

log = logging.getLogger(__name__)

class DiscordTool(BaseTool):
    def __init__(self):
        self.bot: Optional[discord.Client] = None
        self.blacklist_storage: Dict[int, float] = {}

    def set_bot(self, bot: discord.Client):
        self.bot = bot

    @property
    def name(self) -> str:
        return "Discord_Tool"

    @property
    def description(self) -> str:
        return (
            """
            Discord 综合操作工具，包含以下指令：
            1. 'get_profile': 获取用户的详细资料（强制获取头像和Banner的Base64数据）。
            2. 'block_user': 将指定用户拉黑一段时间，拉黑最长时间1440分钟。
            <Tool_Think>
            - 本工具会自动处理 URL 拼接和图片下载，返回 Base64 给模型。
            - 即使 get_profile 的目标不在当前服务器，也能获取其全局资料。
            </Tool_Think>
            """
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "instruction": {
                "type": "STRING",
                "description": "指令类型，可选值: 'get_profile', 'block_user'",
                "enum": ["get_profile", "block_user"]
            },
            "user_id": {
                "type": "STRING",
                "description": "目标用户的 Discord ID (纯数字字符串)"
            },
            "duration": {
                "type": "INTEGER",
                "description": "拉黑时长（分钟），仅在 instruction 为 block_user 时有效"
            }
        }

    async def execute(self, instruction: str, user_id: str, duration: int = 0, **kwargs) -> Any:
        if not self.bot:
            return "[DiscordTool]Error: Bot instance not initialized."

        try:
            target_uid = int(user_id)
        except ValueError:
            return "[DiscordTool]Error: user_id 必须是数字格式的字符串。"

        context_guild_id = kwargs.get('_context_guild_id')

        if instruction == "get_profile":
            return await self._handle_get_profile(target_uid, context_guild_id)
        elif instruction == "block_user":
            return await self._handle_block_user(target_uid, duration)
        else:
            return f"[DiscordTool]Error: Unknown instruction '{instruction}'"

    async def _fetch_b64(self, url: str) -> str:
        """辅助函数：下载并转码"""
        if not url: 
            return "None"
        try:
            b64_data = await gemini_manager.get_image_base64(url)
            return b64_data if b64_data else "Failed to fetch image data"
        except Exception as e:
            log.error(f"Failed to fetch b64 for {url}: {e}")
            return f"Error: {e}"

    async def _get_raw_user_data_and_urls(self, user_id: int) -> Dict[str, Any]:
        """
        核心方法：绕过 member/user 对象，直接调用 API 获取 Hash 并手动拼接 URL
        """
        try:
            # 1. 使用 bot.http 直接请求 Discord API /users/{id}
            # 返回的是最原始的 JSON 字典，不依赖本地缓存
            raw_data = await self.bot.http.get_user(user_id)
        except discord.NotFound:
            return None
        except Exception as e:
            log.error(f"Raw API Fetch Error: {e}")
            return None

        # 2. 手动拼接 Avatar URL
        avatar_hash = raw_data.get('avatar')
        if avatar_hash:
            # 判断是否是 GIF
            ext = 'gif' if avatar_hash.startswith('a_') else 'png'
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size=1024"
        else:
            # 默认头像逻辑
            discriminator = int(raw_data.get('discriminator', 0))
            if discriminator == 0: # 新版 Pomelo 用户名
                # 根据 ID 计算默认头像
                default_idx = (user_id >> 22) % 6
                avatar_url = f"https://cdn.discordapp.com/embed/avatars/{default_idx}.png"
            else: # 旧版 Discriminator
                default_idx = discriminator % 5
                avatar_url = f"https://cdn.discordapp.com/embed/avatars/{default_idx}.png"

        # 3. 手动拼接 Banner URL
        banner_hash = raw_data.get('banner')
        banner_url = None
        if banner_hash:
            ext = 'gif' if banner_hash.startswith('a_') else 'png'
            banner_url = f"https://cdn.discordapp.com/banners/{user_id}/{banner_hash}.{ext}?size=1024"

        # 4. 获取 Accent Color
        accent_color = raw_data.get('accent_color')

        return {
            "username": raw_data.get('username'),
            "global_name": raw_data.get('global_name'),
            "id": raw_data.get('id'),
            "avatar_url": avatar_url,
            "banner_url": banner_url,
            "accent_color": accent_color,
            "bot": raw_data.get('bot', False)
        }

    async def _handle_get_profile(self, user_id: int, context_guild_id: Optional[int]) -> str:
        # 1. 获取最底层的原始数据 (Global)
        raw_user = await self._get_raw_user_data_and_urls(user_id)
        if not raw_user:
            return f"[DiscordTool]Error: User {user_id} not found via Raw API."

        # 2. 获取上下文 Guild 信息 (Member)
        target_guild_id = context_guild_id if context_guild_id else TARGET_GUILD_ID
        guild = self.bot.get_guild(target_guild_id)
        
        member = None
        server_avatar_url = None
        
        if guild:
            # 尝试获取 Member
            member = guild.get_member(user_id)
            if not member:
                try:
                    member = await guild.fetch_member(user_id)
                except:
                    member = None
            
            # 手动提取 Server Avatar
            if member and member.guild_avatar:
                server_avatar_url = str(member.guild_avatar.url)

        # 3. 统一通过 gemini_manager 下载图片
        global_avatar_b64 = await self._fetch_b64(raw_user['avatar_url'])
        banner_b64 = await self._fetch_b64(raw_user['banner_url'])
        server_avatar_b64 = await self._fetch_b64(server_avatar_url)

        # 4. 构造返回数据
        info = {
            "Global Info (Raw API)": {
                "Username": raw_user['username'],
                "Global Name": raw_user['global_name'],
                "ID": raw_user['id'],
                "Is Bot": raw_user['bot'],
                "Avatar URL": raw_user['avatar_url'],
                "Banner URL": raw_user['banner_url'] if raw_user['banner_url'] else "None",
                "Accent Color": str(raw_user['accent_color']),
                "Avatar_Base64_Data": global_avatar_b64,
                "Banner_Base64_Data": banner_b64
            }
        }

        if member:
            roles = [r.name for r in member.roles if r.name != "@everyone"]
            key_permissions = []
            for perm, value in member.guild_permissions:
                if value and perm in ['administrator', 'manage_guild', 'ban_members', 'kick_members', 'manage_messages', 'mention_everyone']:
                    key_permissions.append(perm)

            info[f"Server Profile ({guild.name})"] = {
                "Nickname": member.display_name,
                "Joined At": str(member.joined_at),
                "Server Avatar URL": server_avatar_url if server_avatar_url else "Same as Global",
                "Server_Avatar_Base64_Data": server_avatar_b64 if server_avatar_url else "Same as Global",
                "Roles": roles,
                "Key Permissions": key_permissions,
                "Status": str(member.status)
            }
        else:
            guild_name = guild.name if guild else f"ID:{target_guild_id}"
            info["Server Profile"] = f"User is NOT in the guild: {guild_name}"

        return json.dumps(info, ensure_ascii=False)

    async def _handle_block_user(self, user_id: int, duration_minutes: int) -> str:
        if duration_minutes <= 0:
            return "[DiscordTool]Error: duration 必须大于 0 分钟。"
        if duration_minutes > 1440:
            duration_minutes = 1440
        if user_id in ADMIN_IDS:
            return "[DiscordTool]Error: 不可对Admin/Master使用"

        expiry_time = time.time() + (duration_minutes * 60)
        self.blacklist_storage[user_id] = expiry_time
        
        current_time = time.time()
        expired_keys = [k for k, v in self.blacklist_storage.items() if v < current_time]
        for k in expired_keys:
            del self.blacklist_storage[k]

        return f"[DiscordTool]Success: 用户 {user_id} 已被加入静默黑名单，时长 {duration_minutes} 分钟。"

    def is_user_blocked(self, user_id: int) -> bool:
        if user_id not in self.blacklist_storage:
            return False
        
        expiry = self.blacklist_storage[user_id]
        if time.time() > expiry:
            del self.blacklist_storage[user_id]
            return False
        
        return True