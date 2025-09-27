import discord
from discord.ext import commands
from asyncio import Queue
import datetime
from src import config , database 
from src.command import SubscriptionView , setup_commands
from src.ui import TrackNewThreadView
from src.config import get_utc8_now_str
import asyncio
from src.database import check_and_create_user
from discord.errors import Forbidden

# --- 机器人核心类 ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)
        self.thread_creation_queue = Queue()
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        self.db_pool = None

        # 从 config 模块加载配置
        self.TARGET_GUILD_ID = config.TARGET_GUILD_ID
        self.ADMIN_IDS = config.ADMIN_IDS
        self.ALLOWED_CHANNELS = config.ALLOWED_CHANNELS
        self.EMBED_TITLE = config.EMBED_TITLE
        self.EMBED_TEXT = config.EMBED_TEXT
        self.UPDATE_MENTION_MAX_NUMBER = config.UPDATE_MENTION_MAX_NUMBER
        self.UPDATE_MENTION_DELAY = config.UPDATE_MENTION_DELAY
        self.UPDATE_TITLE = config.UPDATE_TITLE
        self.UPDATE_TEXT = config.UPDATE_TEXT
        self.DM_PANEL_TITLE = config.DM_PANEL_TITLE
        self.DM_PANEL_TEXT = config.DM_PANEL_TEXT
        self.UPDATES_PER_PAGE = config.UPDATES_PER_PAGE
        self.MANAGE_SUBS_TITLE = config.MANAGE_SUBS_TITLE
        self.MANAGE_AUTHORS_TITLE = config.MANAGE_AUTHORS_TITLE
        self.TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS = config.TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS
        self.TRACK_NEW_THREAD_EMBED_TITLE = config.TRACK_NEW_THREAD_EMBED_TITLE
        self.TRACK_NEW_THREAD_EMBED_TEXT = config.TRACK_NEW_THREAD_EMBED_TEXT

    async def setup_hook(self):
        # 1. 初始化数据库连接池
        self.db_pool = await database.create_db_pool()
        if not self.db_pool:
            print("数据库连接失败，机器人无法启动。")
            await self.close()
            return
        
        # 2. 自动创建表
        await database.setup_database(self.db_pool)

        # 3. 注册指令
        target_guild = discord.Object(id=self.TARGET_GUILD_ID)
        setup_commands(self.tree, target_guild)

        # 4. 注册持久化视图
        self.add_view(SubscriptionView())
        self.add_view(TrackNewThreadView())

        # 5. 同步指令
        try:
            print(f"正在尝试将指令同步到服务器 ID: {target_guild.id}...")
            synced_commands = await self.tree.sync(guild=target_guild)
            synced_global = await self.tree.sync(guild=None)
            print(f"成功同步了 {len(synced_commands) + len(synced_global)} 个指令。")
        except Exception as e:
            print(f"指令同步时发生严重错误: {e}")

        # 6. 启动后台任务
        self.loop.create_task(self.thread_processor_task())
        if self.TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS == 1:
            print("启用追踪新帖子功能")
        else:
            print("关闭追踪新帖子功能")

    async def on_ready(self):
        print(f'机器人 {self.user} 已成功登录并准备就绪！')

    async def on_thread_create(self, thread: discord.Thread):
        if self.TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS != 1:
            return
        if thread.parent_id not in self.ALLOWED_CHANNELS:
            return
        
        await database.check_and_create_user(self.db_pool, thread.owner_id)
        
        async with self.db_pool.acquire() as conn, conn.cursor() as cursor:
            sql = "SELECT track_new_thread FROM users WHERE user_id = %s"
            await cursor.execute(sql, (thread.owner_id,))
            result = await cursor.fetchone()
            if not result or result[0] == 1:
                await self.thread_creation_queue.put(thread)

    async def thread_processor_task(self):# 队列中的thread将在这里发送
            await self.wait_until_ready()
            while not self.is_closed():
                try:
                    thread = await self.thread_creation_queue.get()
                
                    max_attempts = 3
                    attempt_delay = 10
                    url_buffer = f"https://discord.com/channels/{thread.guild.id}/{thread.id}"
                    title_buffer = self.TRACK_NEW_THREAD_EMBED_TITLE
                    text_buffer = self.TRACK_NEW_THREAD_EMBED_TEXT.replace("{{author}}", f"<@{thread.owner_id}>").replace("{{thread_url}}", url_buffer)
                    embed = discord.Embed(title=title_buffer, description=text_buffer, color=discord.Color.blue()).set_footer(text=f"✅已发送 | At {get_utc8_now_str()}")

                    for attempt in range(max_attempts):
                        try:
                            # 尝试发送
                            await thread.send(embed=embed, view=TrackNewThreadView())
                            
                            # 如果上面这行代码没有报错，说明发送成功了
                            print(f"{get_utc8_now_str()}|✅ 成功向帖子 '{thread.name}' (ID: {thread.id}) 发送消息。")
                            break  # 成功后必须 break，跳出重试循环

                        except Forbidden as e:
                            if e.code == 40058: # 只处理 40058 错误
                                if attempt < max_attempts - 1: # 如果不是最后一次尝试
                                    print(f"{get_utc8_now_str()}|⏳ 帖子 {thread.id} 未就绪 (Error 40058)，将在 {attempt_delay} 秒后重试... (尝试 {attempt + 2}/{max_attempts})")
                                    await asyncio.sleep(attempt_delay)
                                else: # 如果是最后一次尝试
                                    print(f"{get_utc8_now_str()}|❌ 在 {max_attempts} 次尝试后，向帖子 {thread.id} 发送消息仍失败 (Error 40058)。")
                            else:
                                # 如果是其他 Forbidden 错误，直接报错并跳出循环
                                print(f"{get_utc8_now_str()}|处理帖子 {thread.id} 时遇到未处理的权限错误: {e}")
                                break
                        except Exception as e:
                            # 捕获其他任何异常，直接报错并跳出循环
                            print(f"{get_utc8_now_str()}|处理帖子 {thread.id} 时发送消息失败: {e}")
                            break
                    
                    # --- 重试逻辑结束 ---
                    
                    self.thread_creation_queue.task_done()
                    await asyncio.sleep(1)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"Thread processor task 发生严重错误: {e}")

    async def on_thread_create(self,thread:discord.Thread):
        if self.TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS != 1 :
            return
        if thread.parent_id not in self.ALLOWED_CHANNELS:
            return
        await check_and_create_user(self.db_pool, thread.owner_id) #假如是新人第一次发帖
        async with self.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    sql = "SELECT track_new_thread FROM users WHERE user_id = %s"
                    await cursor.execute(sql,(thread.owner_id,))
                    result = await cursor.fetchone()
                    if not result or result[0] == 1: #若启用了新帖子启动更新
                        try:
                            await self.thread_creation_queue.put(thread) #压入队列
                        except Exception as e:
                            print(f"Error:{e}")
                await conn.commit()

if __name__ == "__main__":
    bot = MyBot()
    bot.run(config.BOT_TOKEN)