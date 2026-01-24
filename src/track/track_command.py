from __future__ import annotations
import discord
from discord import app_commands ,Interaction
import datetime
import os
import psutil
import aiomysql
import asyncio 
from src.config import get_utc8_now_str , ADMIN_IDS
from src.track.track_ui import SubscriptionView, UserPanel , PermissionManageView
from src.track.db import check_and_create_user
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from main import MyBot
import logging
log = logging.getLogger(__name__)
# --- 指令注册函数 ---
def setup_commands(tree: app_commands.CommandTree, guild: discord.Object):
    """将所有指令添加到指令树中"""
    tree.add_command(CommandGroup_bot(name="bot", description="机器人指令"), guild=guild)
    tree.add_command(create_update_feed, guild=guild)
    tree.add_command(update_feed, guild=guild)
    tree.add_command(review_subscription,guild=guild)
    tree.add_command(manage_permission,guild=guild)
    tree.add_command(manage_subscription_panel)

async def is_admin_user(Interaction: Interaction) -> bool:
    return Interaction.user.id in ADMIN_IDS

@app_commands.guild_only()
class CommandGroup_bot(app_commands.Group): #查询BOT运行状态command    
    @app_commands.command(name="运行状态", description="查询目前Bot的运行状态，仅Bot主人可用")
    @app_commands.check(is_admin_user)
    async def hello(self, interaction: discord.Interaction):
        bot: "MyBot" = interaction.client
        mem = psutil.virtual_memory()   
        mem_total = mem.total / (1024*1024)
        mem_available = mem.available / (1024*1024)
        mem_available_usage = (mem_available/ mem_total)*100   # 整个物理内存使用率
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
        embed.add_field(name="内存总量/可使用:",value=f"{mem_total:.2f}/{mem_available:.2f}MB（可用:    {mem_available_usage:.2f}%）", inline=True)
        embed.add_field(name="PING延迟", value=f"{latency:.2f} ms", inline=True)
        embed.add_field(name="服务中服务器", value=f"`{guild_count} 个`", inline=True)
        days = uptime_delta.days
        hours, remainder = divmod(uptime_delta.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
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

    await interaction.response.defer(ephemeral=False) 
    thread_channel: discord.Thread = interaction.channel
    if not thread_channel.owner_id:
        await interaction.followup.send("❌ 严重错误：无法获取帖子的 `owner_id`。", ephemeral=True)
        return

    #check
    if interaction.user.id != thread_channel.owner_id:
        await interaction.followup.send("❌ 只有帖子作者可以创建更新推流。", ephemeral=True)
        return
    
    thread_owner = interaction.user
    try:
        await check_and_create_user(bot.db_pool, thread_owner.id)
        thread_id = thread_channel.id
        guild_id = interaction.guild.id
        async with bot.db_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute("SELECT EXISTS(SELECT 1 FROM managed_threads WHERE thread_id = %s) AS select_result;", (thread_id,))
                result = await cursor.fetchone()
                if result and result['select_result'] == 0:#如果是第一次创建
                    await cursor.execute(
                        """INSERT INTO managed_threads (thread_id, guild_id, author_id)
                        VALUES (%s, %s, %s) AS new_values
                        ON DUPLICATE KEY UPDATE
                        guild_id = new_values.guild_id,
                        author_id = new_values.author_id""",
                        (thread_id, guild_id, thread_owner.id)
                )
                    insert_notification_sql = """
                        INSERT INTO follower_thread_notifications (follower_id, thread_id)
                        SELECT follower_id, %s FROM author_follows WHERE author_id = %s
                    """
                    await cursor.execute(insert_notification_sql, (thread_id, thread_owner.id))
                else: #如果不是第一次创建
                    embed = discord.Embed(title=bot.EMBED_TITLE, description=f"ℹ️ 这个帖子已经开启了更新推流功能。\n\n{bot.EMBED_TEXT}", color=discord.Color.blue())
                    await interaction.followup.send(embed=embed, view=SubscriptionView(), ephemeral=True)
                    return
            await conn.commit()
        try: #手动执行指令的时候，取消作者的新帖子不再自动提醒创建更新推流状态
            async with bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    sql = """
                        INSERT INTO users (user_id, track_new_thread) 
                        VALUES (%s, FALSE)
                        ON DUPLICATE KEY UPDATE track_new_thread = TRUE;
                    """
                    await cursor.execute(sql, (thread_owner.id,))
                await conn.commit()
        except Exception as e:
            logging.error(f"数据库错误于[track_thread_choice_never]: {e}")
            await interaction.followup.send(f"❌ SQL_Error: {e}\n数据库操作失败，但已成功创建更新推流。请联系开发者", ephemeral=True)

        embed = discord.Embed(title=bot.EMBED_TITLE, description=bot.EMBED_TEXT, color=discord.Color.blue())
        await interaction.followup.send(embed=embed, view=SubscriptionView())

    except Exception as e:
        logging.error(f"未知错误于 /创建更新推流: {e}")
        await interaction.followup.send(f"❌ Unkown_Error:{e}，请联系开发者...", ephemeral=True)

@app_commands.command(name="查看订阅入口",description="查看当前帖子的订阅入口")
async def review_subscription(interaction: discord.Interaction):
    bot: "MyBot" = interaction.client
    if not isinstance(interaction.channel, discord.Thread) or interaction.channel.parent_id not in bot.ALLOWED_CHANNELS:
        await interaction.response.send_message("❌ 此指令只能在指定的论坛频道的帖子中使用。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True) 
    if not interaction.channel.id:
        await interaction.followup.send("❌ 严重错误：无法获取帖子的id。", ephemeral=True)
        return
    try:
        await check_and_create_user(bot.db_pool,interaction.user.id)
        async with bot.db_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute("SELECT EXISTS(SELECT 1 FROM managed_threads WHERE thread_id = %s) AS select_result;", (interaction.channel.id,))
                result = await cursor.fetchone()
                if result and result['select_result'] == 0:
                    await interaction.followup.send(f"❌ Error : 该帖子未启用更新推流，请联系帖主",ephemeral=True)
                else :
                    url_sql = """
                        SELECT
                            COALESCE(last_update_url,'NULL') AS last_update_url
                        FROM
                            managed_threads
                        WHERE
                            thread_id = %s
                    """
                    await cursor.execute(url_sql, (interaction.channel.id,))
                    result_1 = await cursor.fetchone()
                    if result_1['last_update_url'] == "NULL":
                        url = f"https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id}/0"
                        text = f"目前帖子还没有最新的订阅\n你可以在这里看到首楼->{url}"
                        embed = discord.Embed(title=bot.EMBED_TITLE,description=text,color=discord.Color.blue())
                        await interaction.followup.send(embed=embed,view=SubscriptionView(),ephemeral=True)
                    else:
                        last_sql = """
                            SELECT
                                COALESCE(last_update_message, 'NULL') AS last_update_message,
                                COALESCE(last_update_at, 'NULL') AS last_update_at,
                                COALESCE(last_update_type, 'NULL') AS last_update_type
                            FROM
                                managed_threads
                            WHERE
                                thread_id = %s;
                        """
                        await cursor.execute(last_sql, (interaction.channel.id,))
                        result_2 = await cursor.fetchone()
                        last_update_url = result_1['last_update_url']
                        last_update_message = result_2['last_update_message']
                        last_update_at = result_2['last_update_at']
                        last_update_type = result_2['last_update_type']
                        
                        text = f"最近一次的更新->{last_update_url}\n更新类型->{last_update_type}\n\n作者的话:{last_update_message}"
                        embed = discord.Embed(title=bot.EMBED_TITLE,description=text,color=discord.Color.blue())
                        embed.set_footer(text=f"最后一次更新在:{last_update_at} |在下方更改你的订阅状态")
                        await interaction.followup.send(embed=embed,view=SubscriptionView(),ephemeral=True)
    except Exception as e:
        logging.error(f"未知错误于 /查看订阅入口:{e}")
        await interaction.followup.send(f"❌ Unkown_Error:{e},请联系开发者...",ephemeral=True)


@app_commands.command(name="控制面板",description="在Bot的私信以及公屏均可调用~")
async def manage_subscription_panel(interaction: discord.Interaction):
    bot: "MyBot" = interaction.client
    user = interaction.user
    
    await check_and_create_user(bot.db_pool, user.id)

    try:
        thread_update_count = 0
        author_update_count = 0
        async with bot.db_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # 查询精确的订阅更新数
                thread_update_sql = """
                    SELECT COUNT(DISTINCT t.thread_id)
                    FROM thread_subscriptions ts JOIN managed_threads t ON ts.thread_id = t.thread_id
                    WHERE ts.user_id = %s AND ts.has_new_update = TRUE AND (
                        (ts.subscribe_release = TRUE AND t.last_update_type = 'release') OR
                        (ts.subscribe_test = TRUE AND t.last_update_type = 'test')
                    )
                """
                await cursor.execute(thread_update_sql, (user.id,))
                thread_update_count = (await cursor.fetchone())[0]

                # 查询精确的、去重后的作者动态数
                author_update_sql = """
                    SELECT COUNT(*)
                    FROM follower_thread_notifications
                    WHERE follower_id = %s AND thread_id NOT IN (
                        SELECT ts.thread_id FROM thread_subscriptions ts
                        JOIN managed_threads t ON ts.thread_id = t.thread_id
                        WHERE ts.user_id = %s AND ts.has_new_update = TRUE AND (
                            (ts.subscribe_release = TRUE AND t.last_update_type = 'release') OR
                            (ts.subscribe_test = TRUE AND t.last_update_type = 'test')
                        )
                    )
                """
                await cursor.execute(author_update_sql, (user.id, user.id))
                author_update_count = (await cursor.fetchone())[0]

    except Exception as err:
        logging.error(f"数据库错误于 控制面板 : {err}")
        await interaction.response.send_message("❌ 无法获取您的订阅状态，请稍后重试。", ephemeral=True)
        return
    
    description_text = bot.DM_PANEL_TEXT.replace(
        "{{user}}", user.mention
    ).replace(
        "{{thread_update_number}}", str(thread_update_count)
    ).replace(
        "{{author_update_number}}", str(author_update_count)
    )

    embed = discord.Embed(
        title=bot.DM_PANEL_TITLE,
        description=description_text,
        color=discord.Color.blurple()
    ).set_footer(text=f"将在5分钟后消失... |At {get_utc8_now_str()}")
    view = UserPanel()

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@app_commands.command(name="更新推流", description="为一个帖子发送更新推流") #更新推流command
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
    bot: "MyBot" = interaction.client
    guild_id = bot.TARGET_GUILD_ID
    valid_url = f"https://discord.com/channels/{guild_id}/"
    user = interaction.user
    thread = interaction.channel

    if not isinstance(thread, discord.Thread) or thread.parent_id not in bot.ALLOWED_CHANNELS:
        await interaction.response.send_message("❌ 此指令只能在指定的论坛频道的帖子中使用。", ephemeral=True)
        return
    
    if not url.startswith(valid_url):
        await interaction.response.send_message(
            f"❌ 提供的URL无效。链接必须是本服务器内的消息链接。\n"
            f"它应该以 `{valid_url}` 开头。"
            f"而您提供的URL为`{url}`",
            ephemeral=True
        )
        return

    users_to_notify = set()
    thread_owner_id = None
    
    try:
        #开始初步响应
        await interaction.response.defer()
        status_embed = discord.Embed(
        title=bot.UPDATE_TITLE,
        description="正在收集必要的数据中...",
        color=discord.Color.blue()
    ).set_footer(text=f"运行状态：准备中...|{discord.utils.format_dt(datetime.datetime.now(), 'T')}")
        await interaction.followup.send(embed=status_embed)
        response_message = await interaction.original_response()
        #响应完成，开始查表
        async with bot.db_pool.acquire() as conn:
            async with conn.cursor() as cursor:
        #验证权!
                permission_check_sql = """
                                    SELECT 1 
                                    FROM 
                                        managed_threads 
                                    WHERE 
                                        thread_id = %s 
                                        AND %s IN (author_id, thread_permission_group_1,thread_permission_group_2, thread_permission_group_3, thread_permission_group_4);
                                """
                await cursor.execute(permission_check_sql, (thread.id, user.id))
                permission_granted = await cursor.fetchone()
                if not permission_granted:
                    await cursor.execute("SELECT 1 FROM managed_threads WHERE thread_id = %s", (thread.id,))
                    thread_exists = await cursor.fetchone()
                    if not thread_exists:
                        await response_message.edit(content="❌ 这个帖子没有开启更新推流功能，请先使用 /创建更新推流 指令。")
                    else:
                        await response_message.edit(content="❌ 你不是帖子作者，也不在权限组内，无法发送更新推流。")
                    return

        # 先更新状态
                thread_owner_id = thread.owner_id #fix:修复thread_onwer_id未赋值导致的bug
                update_thread_sql = """
            UPDATE managed_threads
            SET last_update_url = %s, last_update_message = %s, last_update_at = CURRENT_TIMESTAMP, last_update_type = %s
            WHERE thread_id = %s
        """
                await cursor.execute(update_thread_sql, (url, message, update_type.value, thread.id))

        #标记订阅的该帖子的用户对应的更新状态
                subscription_update_sql = f"""
            UPDATE thread_subscriptions 
            SET has_new_update = TRUE
            WHERE thread_id = %s AND subscribe_{update_type.value} = TRUE
        """
                await cursor.execute(subscription_update_sql, (thread.id,))
        #顺便查询需要通知的用户数组
                if update_type.value == "release": # 查询订阅发行版的用户
                    await cursor.execute("SELECT user_id FROM thread_subscriptions WHERE thread_id = %s AND subscribe_release = TRUE", (thread.id,))
                else: # 查询订阅测试版的用户
                    await cursor.execute("SELECT user_id FROM thread_subscriptions WHERE thread_id = %s AND subscribe_test = TRUE", (thread.id,))
                for row in await cursor.fetchall():
                    users_to_notify.add(row[0])

                await cursor.execute("SELECT follower_id FROM author_follows WHERE author_id = %s", (thread_owner_id,)) # 查询关注作者的用户
                for row in await cursor.fetchall():
                    users_to_notify.add(row[0])

                insert_notification_sql = """
                        INSERT IGNORE INTO follower_thread_notifications (follower_id, thread_id)
                        SELECT follower_id, %s FROM author_follows WHERE author_id = %s
                    """
                await cursor.execute(insert_notification_sql,(thread.id,thread_owner_id))
            
            await conn.commit()

    except Exception as err:
        # 如果任何一步数据库操作失败，发送错误消息并返回
        await response_message.edit(content=f"❌ SQL_Error：{err}\n请联系开发者")
        return
    
    # --- 数据库操作已全部完成，开始发送响应和幽灵提及 ---

    # 开始预处理提及名单
    user_list = list(users_to_notify)
    total_users = len(user_list)
    processed_users = 0 # 开始处理

    if total_users == 0:
        final_text = bot.UPDATE_TEXT.replace("{{text}}", message).replace("{{url}}", url).replace("{{author}}", user.mention)
        final_embed = discord.Embed(title=bot.UPDATE_TITLE, description=final_text, color=discord.Color.blue())
        final_embed.set_footer(text=f"运行状态：✅没有需要通知的用户|{get_utc8_now_str()}")
        await response_message.edit(embed=final_embed)
        return
    
    mention_delay = bot.UPDATE_MENTION_DELAY / 1000.0
    update_text_template = bot.UPDATE_TEXT.replace("{{author}}", user.mention).replace("{{text}}", message).replace("{{url}}", url)
    update_embed = discord.Embed(title=bot.UPDATE_TITLE, description=update_text_template, color=discord.Color.green())

    for i in range(0, total_users, bot.UPDATE_MENTION_MAX_NUMBER):
        batch = user_list[i:i + bot.UPDATE_MENTION_MAX_NUMBER]
        if not batch: continue

        mention_string = " ".join(f"<@{uid}>" for uid in batch)
        try:
            ghosted_message = await thread.send(mention_string)
            await ghosted_message.delete()
        except discord.Forbidden:
            logging.error(f"无法发送幽灵提及消息，可能是权限不足。")
            update_embed.color = discord.Color.red()
            update_embed.description = "无法发送提及通知，请检查机器人权限。"
            await response_message.edit(embed=update_embed)
            return # 无法提及，直接中止

        processed_users += len(batch)

        progress_text = f"运行状态：正在通知 {processed_users}/{total_users}"
        update_embed.set_footer(text=progress_text + f"|{get_utc8_now_str()}")
        await response_message.edit(embed=update_embed)

        if mention_delay > 0:
            await asyncio.sleep(mention_delay) # 等待

    # 完成
    final_embed = discord.Embed(title=bot.UPDATE_TITLE, description=update_text_template, color=discord.Color.green())
    final_embed.set_footer(text=f"运行状态：✅已通知 {processed_users}/{total_users} | {get_utc8_now_str()}")
    await response_message.edit(embed=final_embed)

@app_commands.command(name="管理当前帖子权限组",description="管理当前帖子可用更新推流的权限组")
async def manage_permission(interaction: discord.Interaction):
    bot: "MyBot" = interaction.client
    if not isinstance(interaction.channel, discord.Thread) or interaction.channel.parent_id not in bot.ALLOWED_CHANNELS:
        await interaction.response.send_message("❌ 此指令只能在指定的论坛频道的帖子中使用。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True) 
    thread_channel: discord.Thread = interaction.channel
    if not thread_channel.owner_id:
        await interaction.followup.send("❌ 严重错误：无法获取帖子的 `owner_id`。", ephemeral=True)
        return
    if interaction.user.id != thread_channel.owner_id:
        await interaction.followup.send("❌ 只有帖子作者可以管理权限组。", ephemeral=True)
        return
    
    try:

        async with bot.db_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                sql = """
                SELECT author_id, thread_permission_group_1, thread_permission_group_2,thread_permission_group_3, thread_permission_group_4
                       FROM managed_threads WHERE thread_id = %s"""
                await cursor.execute(sql,(interaction.channel.id,))
                data = await cursor.fetchone()
                if not data:
                    await interaction.followup.send("❌ 错误：此帖子尚未开启推流更新，该功能不可用", ephemeral=True)
                    return
                initial_permissions = [
                    data['thread_permission_group_1'],
                    data['thread_permission_group_2'],
                    data['thread_permission_group_3'],
                    data['thread_permission_group_4']
                ]
                author_id = data['author_id']
            await conn.commit()

        view = PermissionManageView(
            bot=bot,
            thread_id=interaction.channel.id,
            author_id=author_id,
            initial_permissions=initial_permissions
        )
        embed = view.create_embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    except Exception as e:
        logging.error(f"在 /管理当前帖子权限组 出现错误: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message(f"❌ 未知错误: {e}\n请联系开发者...", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ 未知错误: {e}\n请联系开发者...", ephemeral=True)