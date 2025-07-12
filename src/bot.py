import os
import discord
from discord import app_commands, ui
from discord.ext import commands
import mysql.connector
from mysql.connector import errorcode, pooling
import asyncio
import datetime
import psutil

# --- 订阅UI ---
class SubscriptionView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="订阅发行版", style=discord.ButtonStyle.primary, custom_id="subscribe_release_button") # UI:订阅发行
    async def subscribe_release(self, interaction: discord.Interaction, button: ui.Button):
        bot:"MyBot" = interaction.client
        user_id = interaction.user.id
        thread_id = interaction.channel.id
        new_status = None

        try:
            conn = bot.db_pool.get_connection()
            cursor = conn.cursor()

            toggle_sql = """
                INSERT INTO thread_subscriptions (user_id, thread_id, subscribe_release)
                VALUES (%s, %s, TRUE)
                ON DUPLICATE KEY UPDATE subscribe_release = NOT subscribe_release;
                """
            cursor.execute(toggle_sql, (user_id, thread_id))

            select_sql = "SELECT subscribe_release FROM thread_subscriptions WHERE user_id = %s AND thread_id = %s"
            cursor.execute(select_sql, (user_id, thread_id))    
            result = cursor.fetchone()
            if result:
                new_status = bool(result[0])

            conn.commit()

        except mysql.connector.Error as err:
            print(f"数据库错误于[subscribe_release_button]: {err}")
            await interaction.response.send_message(f"❌ 数据库操作失败，请稍后再试", ephemeral=True)
            return  
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

        if new_status is True:
            await interaction.response.send_message("✅ 已订阅发行版更新通知！", ephemeral=True)
        elif new_status is False:
            await interaction.response.send_message("❌ 已取消订阅发行版更新通知！", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 无法确定订阅状态，请稍后再试。或联系开发者", ephemeral=True)


    @ui.button(label="订阅测试版", style=discord.ButtonStyle.secondary, custom_id="subscribe_test_button") # UI:订阅测试
    async def subscribe_test(self, interaction: discord.Interaction, button: ui.Button):
        bot: "MyBot" = interaction.client
        user_id = interaction.user.id
        thread_id = interaction.channel.id
        new_status = None

        try:
            conn = bot.db_pool.get_connection()
            cursor = conn.cursor()

            toggle_sql = """
                INSERT INTO thread_subscriptions (user_id, thread_id, subscribe_test)
                VALUES (%s, %s, TRUE)
                ON DUPLICATE KEY UPDATE subscribe_test = NOT subscribe_test;  
            """
            cursor.execute(toggle_sql, (user_id, thread_id))

            select_sql = "SELECT subscribe_test FROM thread_subscriptions WHERE user_id = %s AND thread_id = %s"
            cursor.execute(select_sql, (user_id, thread_id))
            result = cursor.fetchone()
            if result:
                new_status = bool(result[0])

            conn.commit()

        except mysql.connector.Error as err:
            print(f"数据库错误于[subscribe_test_button]: {err}")
            await interaction.response.send_message(f"❌ 数据库操作失败，请稍后再试", ephemeral=True)
            return
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

        if new_status is True:
            await interaction.response.send_message("✅ 已订阅测试版更新通知！", ephemeral=True)
        elif new_status is False:
            await interaction.response.send_message("❌ 已取消订阅测试版更新通知！", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 无法确定订阅状态，请稍后再试。或联系开发者", ephemeral=True)

    @ui.button(label="关注作者", style=discord.ButtonStyle.success, custom_id="follow_author_button") # UI:关注作者
    async def follow_author(self, interaction: discord.Interaction, button: ui.Button):
        bot: "MyBot" = interaction.client
        user_id = interaction.user.id
        if not interaction.channel.owner_id:
            await interaction.response.send_message("❌ 无法获取帖子作者信息，请确认帖子作者还在服务器中，或联系开发者", ephemeral=True)
            return
        author_id = interaction.channel.owner_id 
        new_status = None

        try:
            conn = bot.db_pool.get_connection()
            cursor = conn.cursor(buffered=True)
            insert_sql = "INSERT INTO author_follows (follower_id, author_id) VALUES (%s, %s)"# 插入关注记录
            try:
                cursor.execute(insert_sql, (user_id, author_id))
                new_status = True  # 成功插入，表示关注成功
            except mysql.connector.Error as err:
                if err.errno == errorcode.ER_DUP_ENTRY:
                    # 如果是重复键错误，表示已经关注了，开始删除
                    delete_sql = "DELETE FROM author_follows WHERE follower_id = %s AND author_id = %s"
                    cursor.execute(delete_sql, (user_id, author_id))
                    new_status = False  # 成功删除，表示取消关注
                else:
                    raise err

            conn.commit()

        except mysql.connector.Error as err:
            print(f"数据库错误于[follow_author_button]: {err}")
            await interaction.response.send_message(f"❌ 数据库操作失败，请稍后再试", ephemeral=True)
            return
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()
        if new_status is True:
            await interaction.response.send_message("✅ 已关注作者！", ephemeral=True)
        elif new_status is False:
            await interaction.response.send_message("❌ 已取消关注作者！", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 无法确定关注状态，请稍后再试。或联系开发者", ephemeral=True)

# --- 指令组定义 ---

@app_commands.guild_only()
class CommandGroup_bot(app_commands.Group):
    
    @app_commands.command(name="运行状态", description="查询目前Bot的运行状态")
    async def hello(self, interaction: discord.Interaction):
        bot: "MyBot" = interaction.client

        if interaction.user.id not in bot.ADMIN_IDS:
            await interaction.response.send_message("❌ 你没有权限使用这个指令。", ephemeral=True)
            print(f"用户 {interaction.user.id} 尝试访问 /bot 运行状态，但没有权限。")
            return
        process = psutil.Process(os.getpid())
        cpu_usage = process.cpu_percent(interval=1)

        memory_info = process.memory_info()
        memory_mb = memory_info.rss / (1024 * 1024)  # 转换为MB

        latency = bot.latency * 1000

        uptime_delta = datetime.datetime.now(datetime.timezone.utc) - bot.start_time
        guild_count = len(bot.guilds)

        embed = discord.Embed(title="Bot 运行状态", color=discord.Color.blue())
        embed.add_field(name="CPU 使用率", value=f"{cpu_usage:.2f}%", inline=True)
        embed.add_field(name="内存使用量", value=f"{memory_mb:.2f} MB", inline=True)
        embed.add_field(name="PING延迟", value=f"{latency:.2f} ms", inline=True)
        embed.add_field(name="服务中服务器", value=f"`{guild_count} 个`", inline=True)
        days = uptime_delta.days
        hours, remainder = divmod(uptime_delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{days}天 {hours}小时 {minutes}分钟"
        embed.add_field(name="运行时长", value=uptime_str, inline=False)
        utc_plus_8 = datetime.timezone(datetime.timedelta(hours=8))
        start_time_utc8 = bot.start_time.astimezone(utc_plus_8)
        start_time_str = start_time_utc8.strftime("%Y-%m-%d %H:%M:%S")
        embed.set_footer(text=f"机器人启动于: {start_time_str}")    
        await interaction.response.send_message(embed=embed, ephemeral=True)

@app_commands.command(name="创建更新推流", description="为一个帖子开启更新订阅功能")
async def create_update_feed(interaction: discord.Interaction):
    
    bot: "MyBot" = interaction.client


    if not isinstance(interaction.channel, discord.Thread) or interaction.channel.parent_id not in bot.ALLOWED_CHANNELS:
            await interaction.response.send_message("❌ 此指令只能在指定的论坛频道的帖子中使用。", ephemeral=True)
            return
    thread_channel: discord.Thread = interaction.channel
    thread_owner_id = thread_channel.owner_id
    commands_caller_id = interaction.user.id
    if commands_caller_id != thread_owner_id:
        await interaction.response.send_message("❌ 只有帖子作者可以创建更新推流。", ephemeral=True)
        return

    thread_id = interaction.channel.id
    author_id = interaction.user.id

    try:
        conn = bot.db_pool.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO managed_threads (thread_id, author_id) VALUES (%s, %s)", (thread_id, author_id))
        conn.commit()
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_DUP_ENTRY:
            await interaction.response.send_message("⚠️ 这个帖子已经创建过更新推流了。", ephemeral=False,embed=discord.Embed(title=bot.EMBED_TITLE,description=bot.EMBED_TEXT,color=discord.Color.blue()),view=SubscriptionView())
            pass
        else:
            print(f"数据库错误于 /创建更新推流: {err}")
            await interaction.response.send_message(f"❌ {bot.EMBED_ERROR}", ephemeral=True)
        return
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

    embed = discord.Embed(title=bot.EMBED_TITLE,description=bot.EMBED_TEXT,color=discord.Color.blue())
    await interaction.response.send_message(embed=embed, view=SubscriptionView())

@app_commands.command(name="更新推流", description="为一个帖子发送更新推流")
@app_commands.describe(
    update_type="此次更新的类型",
    url="这次更新的链接URL",
    message="这次更新的简要描述(400字以内)"
)
@app_commands.choices(
    update_type=[
        app_commands.Choice(name="发行版(Release)", value="release"),
        app_commands.Choice(name="测试版(Test)", value="test"),
    ]
)
async def update_feed(interaction: discord.Interaction, update_type: app_commands.Choice[str], url: str, message: str="无"):
    bot :"MyBot" = interaction.client
    author = interaction.user
    thread = interaction.channel

    if not isinstance(thread,discord.Thread) or thread.parent_id not in bot.ALLOWED_CHANNELS:
        await interaction.response.send_message("❌ 此指令只能在指定的论坛频道的帖子中使用。", ephemeral=True)
        return
    
    conn = None;cursor = None;thread_owner_id = None
    try:
        conn = bot.db_pool.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM managed_threads WHERE thread_id = %s", (thread.id,))
        result = cursor.fetchone()
        if not result:
            await interaction.response.send_message("❌ 这个帖子没有开启更新推流功能，请先使用 /创建更新推流 指令。", ephemeral=True)
            return
        thread_owner_id = result[1]
        if author.id != thread_owner_id:
            await interaction.response.send_message("❌ 只有帖子作者可以发送更新推流。", ephemeral=True)
            return
    except mysql.connector.Error as err:
        await interaction.response.send_message(f"❌ 数据库查询失败：{err}", ephemeral=True)
        return
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

    #开始创建嵌入式消息
    status_embed = discord.Embed(
        title=bot.UPDATE_TITLE,
        description="正在收集必要的数据中...",
        color=discord.Color.blue()
    ).set_footer(text=f"运行状态：准备中...|{discord.utils.format_dt(datetime.datetime.now(), 'T')}")
    await interaction.response.send_message(embed=status_embed)
    response_message = await interaction.original_response()

    #开始查表并且开始更新嵌入式消息

    users_to_notify = set()
    try:
        conn = bot.db_pool.get_connection()
        cursor = conn.cursor(buffered=True)

        if update_type.value == "release": #查询订阅发行版的用户
            cursor.execute("""
                SELECT user_id FROM thread_subscriptions 
                WHERE thread_id = %s AND subscribe_release = TRUE
            """, (thread.id,))
        else :                              #查询订阅测试版的用户
            cursor.execute("""
                SELECT user_id FROM thread_subscriptions 
                WHERE thread_id = %s AND subscribe_test = TRUE
            """, (thread.id,))
        for row in cursor.fetchall():
            users_to_notify.add(row[0])

        cursor.execute("SELECT follower_id FROM author_follows WHERE author_id = %s", (thread_owner_id,))# 查询关注作者的用户
        for row in cursor.fetchall():
            users_to_notify.add(row[0])

    except mysql.connector.Error as err:
        await response_message.edit(embed=discord.Embed(title=bot.UPDATE_ERROR, description=f"获取用户列表时出错", color=discord.Color.red()))
        return
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()
    
    #开始预处理提及名单
    user_list = list(users_to_notify)
    total_users = len(user_list)
    Processed_users = 0 #开始处理

    if total_users == 0:
        final_text = bot.UPDATE_TEXT.replace("{{text}}", message).replace("{{url}}", url).replace("{{author}}", author.name)
        final_embed = discord.Embed(title=bot.UPDATE_TITLE, description=final_text, color=discord.Color.blue())
        final_embed.set_footer(text=f"运行状态：发布完成：没有需要通知的用户|{discord.utils.format_dt(datetime.datetime.now(), 'F')}")
        await response_message.edit(embed=final_embed)
        return 

    update_embed = discord.Embed(title=bot.UPDATE_TITLE, description=bot.UPDATE_TEXT.format(author=author.name, text=message, url=url), color=discord.Color.green())

    for i in range(0, total_users, bot.UPDATE_MENTION_MAX_NUMBER):
        batch = user_list[i:i + bot.UPDATE_MENTION_MAX_NUMBER]
        if not batch:continue

        mention_string = " ".join(f"<@{uid}>" for uid in batch)
        try:
            ghosted_message = await thread.send(mention_string)
            await ghosted_message.delete() # 删除幽灵提及消息
        except discord.Forbidden:
            print(f"无法发送幽灵提及消息，可能是权限不足。请检查机器人是否有发送消息的权限。")
            await response_message.edit(embed=discord.Embed(title=bot.UPDATE_ERROR, description="无法进行通知，具体原因不明...请通知开发者", color=discord.Color.red()))

        Processed_users += len(batch)


        progress_text = f"运行状态：正在通知{Processed_users}/{total_users}"
        update_embed.set_footer(text=progress_text + f"|{discord.utils.format_dt(datetime.datetime.now(), 'T')}")
        await response_message.edit(embed=update_embed)

    #完成
    final_text = bot.UPDATE_TEXT.replace("{{text}}", message).replace("{{url}}", url).replace("{{author}}", author.name)        
    final_embed = discord.Embed(title=bot.UPDATE_TITLE, description=final_text, color=discord.Color.blue())

    utc_plus_8 = datetime.timezone(datetime.timedelta(hours=8))
    completion_time_utc8 = datetime.datetime.now(utc_plus_8)
    completion_time_str = completion_time_utc8.strftime("%Y-%m-%d %H:%M:%S")

    final_embed.set_footer(text=f"运行状态：已通知{Processed_users}/{total_users}|{completion_time_str}")    

    await response_message.edit(embed=final_embed)

# --- 机器人核心类 ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)

        # 载入.env配置
        self.TARGET_GUILD_ID = int(os.getenv("TARGET_GUILD_ID"))
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        self.ADMIN_IDS = [int(uid.strip()) for uid in admin_ids_str.split(',') if uid.strip().isdigit()]
        self.ALLOWED_CHANNELS = [int(c.strip()) for c in os.getenv("ALLOWED_CHANNELS").split(",")]
        self.EMBED_TITLE = os.getenv("EMBED_TITLE")
        self.EMBED_TEXT = os.getenv("EMBED_TEXT")
        self.EMBED_ERROR = os.getenv("EMBED_ERROR")
        self.UPDATE_MENTION_MAX_NUMBER = 50
        self.UPDATE_TITLE = os.getenv("UPDATE_TITLE")
        self.UPDATE_TEXT = os.getenv("UPDATE_TEXT")
        self.UPDATE_ERROR = os.getenv("UPDATE_ERROR")
        self.db_pool = None
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        

    async def setup_hook(self):
        #初始化开始
        # --- 1. 初始化数据库连接池 ---
        print("正在初始化数据库连接池...")
        try:
            pool_size_str = os.getenv("POOL_SIZE")
            pool_size_int = int(pool_size_str)
            self.db_pool = pooling.MySQLConnectionPool(
                pool_name="bot_pool",
                pool_size=pool_size_int,
                host='db',  # Docker Compose中定义的MySQL服务名
                user=os.getenv('MYSQL_USER'),
                password=os.getenv('MYSQL_PASSWORD'),
                database=os.getenv('MYSQL_DATABASE')
            )
            print("数据库连接池创建成功。")
        except mysql.connector.Error as err:
            print(f"致命错误：数据库连接失败: {err}")
            await self.close() #失败直接退出
            return

        # --- 2. 自动创建表 (如果不存在的话) ---
        print("正在检查并创建数据库表...")
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()
            # 使用 IF NOT EXISTS 
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS managed_threads (
                    thread_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                    author_id BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS thread_subscriptions (
                    user_id BIGINT UNSIGNED NOT NULL,
                    thread_id BIGINT UNSIGNED NOT NULL,
                    subscribe_release BOOLEAN NOT NULL DEFAULT FALSE,
                    subscribe_test BOOLEAN NOT NULL DEFAULT FALSE,
                    PRIMARY KEY (user_id, thread_id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS author_follows (
                    follower_id BIGINT UNSIGNED NOT NULL,
                    author_id BIGINT UNSIGNED NOT NULL,
                    PRIMARY KEY (follower_id, author_id)
                );
            """)
            conn.commit()
            print("数据库表结构已确认。")
        except mysql.connector.Error as err:
            print(f"数据库建表失败: {err}")
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()
                
        target_guild = discord.Object(id=self.TARGET_GUILD_ID)
        self.tree.add_command(CommandGroup_bot(name="bot", description="机器人指令"), guild=target_guild)
        self.tree.add_command(create_update_feed, guild=target_guild)
        self.tree.add_command(update_feed, guild=target_guild)

        self.add_view(SubscriptionView())

        # --- 同步command ---
        try:
            print(f"正在尝试将指令同步到服务器 ID: {target_guild.id}...")
            synced_commands = await self.tree.sync(guild=target_guild)
            print(f"成功同步了 {len(synced_commands)} 个指令。")
            for cmd in synced_commands:
                print(f"  - 指令名称: {cmd.name} (ID: {cmd.id})")
        except Exception as e:
            print(f"指令同步时发生严重错误: {e}")

    async def on_ready(self):
        print(f'机器人 {self.user} 已成功登录并准备就绪！') 