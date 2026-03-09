from __future__ import annotations
import discord
from discord import ui
import aiomysql
from src.track.db import check_and_create_user
from src.config import get_utc8_now_str
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from main import MyBot
import logging
log = logging.getLogger(__name__)
# --- 创建更新推流UI ---
class SubscriptionView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="订阅发行版", style=discord.ButtonStyle.primary, custom_id="subscribe_release_button") # UI:订阅发行
    async def subscribe_release(self, interaction: discord.Interaction, button: ui.Button):
        bot:"MyBot" = interaction.client
        user = interaction.user
        user_id = interaction.user.id
        thread_id = interaction.channel.id
        guild_id = interaction.guild.id if interaction.guild else interaction.channel.guild.id

        new_status = None
        await interaction.response.defer()

        await check_and_create_user(bot.db_pool,user_id)

        try:
            async with bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    toggle_sql = """
                INSERT INTO thread_subscriptions (user_id, thread_id, guild_id, subscribe_release)
                VALUES (%s, %s, %s, TRUE)
                ON DUPLICATE KEY UPDATE subscribe_release = NOT subscribe_release, guild_id = VALUES(guild_id);
                """
                    await cursor.execute(toggle_sql, (user_id, thread_id, guild_id))
                    select_sql = "SELECT subscribe_release FROM thread_subscriptions WHERE user_id = %s AND thread_id = %s"
                    await cursor.execute(select_sql, (user_id, thread_id))    
                    result = await cursor.fetchone()
                    if result:
                        new_status = bool(result[0])
                await conn.commit()

        except Exception as err:
            logging.error(f"数据库错误于[subscribe_release_button]: {err}")
            if not interaction.response.is_done():
                await interaction.followup.send(f"❌ SQL_Error:{err}\n数据库操作失败，请稍后再试", ephemeral=True)
            return  

        if new_status is True:
            await interaction.followup.send("✅ 已订阅发行版更新通知！", ephemeral=True)
        elif new_status is False:
            await interaction.followup.send("❌ 已取消订阅发行版更新通知！", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ 无法确定订阅状态，请稍后再试。或联系开发者", ephemeral=True)


    @ui.button(label="订阅测试版", style=discord.ButtonStyle.secondary, custom_id="subscribe_test_button") # UI:订阅测试
    async def subscribe_test(self, interaction: discord.Interaction, button: ui.Button):
        bot: "MyBot" = interaction.client
        user = interaction.user
        user_id = interaction.user.id
        thread_id = interaction.channel.id
        guild_id = interaction.guild.id if interaction.guild else interaction.channel.guild.id
        new_status = None
        await interaction.response.defer()
        
        await check_and_create_user(bot.db_pool,user_id)

        try:
            async with bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    toggle_sql = """
                INSERT INTO thread_subscriptions (user_id, thread_id, guild_id, subscribe_test)
                VALUES (%s, %s, %s, TRUE)
                ON DUPLICATE KEY UPDATE subscribe_test = NOT subscribe_test, guild_id = VALUES(guild_id);  
            """
                    await cursor.execute(toggle_sql, (user_id, thread_id, guild_id))
                    select_sql = "SELECT subscribe_test FROM thread_subscriptions WHERE user_id = %s AND thread_id = %s"
                    await cursor.execute(select_sql, (user_id, thread_id))
                    result = await cursor.fetchone()
                    if result:
                        new_status = bool(result[0])
                await conn.commit()

        except Exception as err:
            logging.error(f"数据库错误于[subscribe_test_button]: {err}")
            if not interaction.response.is_done():
                await interaction.followup.send(f"❌ SQL_Error:{err}\n数据库操作失败，请稍后再试", ephemeral=True)
            return

        if new_status is True:
            await interaction.followup.send("✅ 已订阅测试版更新通知！", ephemeral=True)
        elif new_status is False:
            await interaction.followup.send("❌ 已取消订阅测试版更新通知！", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ 无法确定订阅状态，请稍后再试。或联系开发者", ephemeral=True)

    @ui.button(label="关注作者", style=discord.ButtonStyle.success, custom_id="follow_author_button") # UI:关注作者
    async def follow_author(self, interaction: discord.Interaction, button: ui.Button):
        bot: "MyBot" = interaction.client
        user = interaction.user
        user_id = interaction.user.id
        await interaction.response.defer()
        if not interaction.channel.owner_id:
            await interaction.followup.send("❌ 无法获取帖子作者信息，请确认帖子作者还在服务器中，或联系开发者", ephemeral=True)
            return
        author_id = interaction.channel.owner_id 
        new_status = None
        await check_and_create_user(bot.db_pool,user_id) #因为有交互作者肯定在DB中，只考虑交互者的情况

        try:
            async with bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    insert_sql = "INSERT INTO author_follows (follower_id, author_id) VALUES (%s, %s)"# 插入关注记录
                    try:
                        await cursor.execute(insert_sql, (user_id, author_id))
                        new_status = True  # 成功插入，表示关注成功
                    except aiomysql.IntegrityError as err:
                        if err.args[0] == 1062:
                    # 如果是重复键错误，表示已经关注了，开始删除
                            delete_sql = "DELETE FROM author_follows WHERE follower_id = %s AND author_id = %s"
                            await cursor.execute(delete_sql, (user_id, author_id))
                            new_status = False  # 成功删除，表示取消关注
                        else:
                            raise err
                await conn.commit()

        except Exception as err:
            logging.error(f"数据库错误于[follow_author_button]: {err}")
            if not interaction.response.is_done():
                await interaction.followup.send(f"❌ SQL_Error:{err}\n数据库操作失败，请稍后再试", ephemeral=True)
            return

        if new_status is True:
            await interaction.followup.send("✅ 已关注作者！", ephemeral=True)
        elif new_status is False:
            await interaction.followup.send("❌ 已取消关注作者！", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ 无法确定关注状态，请稍后再试。或联系开发者", ephemeral=True)

# --- DM:管理关系 ---
class ManagementPaginatorView(ui.View):
    def __init__(self, bot, user_id, item_type, total_item_count):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.item_type = item_type
        
        self.total_item_count = total_item_count
        self.current_page_items = []
        
        self.current_page = 0
        self.total_pages = max(0, (self.total_item_count - 1) // self.bot.UPDATES_PER_PAGE)
        
        self._create_components()

    def _create_components(self):
        """一次性创建所有组件"""
        self.select_menu = ui.Select(placeholder="选择要操作的项目...", min_values=1, row=0)
        self.select_menu.callback = self.select_callback
        
        self.delete_button = ui.Button(label="取消订阅/关注", style=discord.ButtonStyle.danger, emoji="🗑️", row=1, custom_id="delete_selected")
        self.delete_button.callback = self.delete_callback
        
        self.first_page_button = ui.Button(label="«", style=discord.ButtonStyle.secondary, row=2, custom_id="page_first")
        self.first_page_button.callback = self.page_callback
        self.prev_page_button = ui.Button(label="‹", style=discord.ButtonStyle.primary, row=2, custom_id="page_prev")
        self.prev_page_button.callback = self.page_callback
        self.next_page_button = ui.Button(label="›", style=discord.ButtonStyle.primary, row=2, custom_id="page_next")
        self.next_page_button.callback = self.page_callback
        self.last_page_button = ui.Button(label="»", style=discord.ButtonStyle.secondary, row=2, custom_id="page_last")
        self.last_page_button.callback = self.page_callback

    async def _fetch_page_data(self): #根据页码从数据库仅获取当前页的数据
        
        limit = self.bot.UPDATES_PER_PAGE
        offset = self.current_page * limit
        
        try:
            async with self.bot.db_pool.acquire() as conn, conn.cursor() as cursor:
                if self.item_type == 'thread':
                    sql = """
                        SELECT subscription_id, thread_id, subscribe_release, subscribe_test
                        FROM thread_subscriptions
                        WHERE user_id = %s AND (subscribe_release = TRUE OR subscribe_test = TRUE)
                        ORDER BY subscription_id DESC
                        LIMIT %s OFFSET %s
                    """
                    await cursor.execute(sql, (self.user_id, limit, offset))
                else: # author
                    sql = """
                        SELECT follow_id, author_id FROM author_follows
                        WHERE follower_id = %s ORDER BY follow_id DESC
                        LIMIT %s OFFSET %s
                    """
                    await cursor.execute(sql, (self.user_id, limit, offset))
                self.current_page_items = await cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching management page data: {e}")
            self.current_page_items = []

    def create_embed(self):
        if not self.total_item_count:
            return discord.Embed(title="空空如也", description="你还没有任何订阅/关注。", color=discord.Color.orange())

        title = self.bot.MANAGE_SUBS_TITLE if self.item_type == 'thread' else self.bot.MANAGE_AUTHORS_TITLE
        start_index = self.current_page * self.bot.UPDATES_PER_PAGE
        lines = []
        for i, item in enumerate(self.current_page_items):
            uid = start_index + i + 1
            if self.item_type == 'thread':
                statuses = []
                if item[2]: statuses.append("Release")
                if item[3]: statuses.append("Test")
                status_str = f" ({', '.join(statuses)})" if statuses else "" #(无订阅不应该出现
                lines.append(f"**{uid}.** <#{item[1]}>{status_str}")
            else:
                lines.append(f"**{uid}.** <@{item[1]}>")
        
        description = "\n".join(lines) if lines else "本页无项目。"
        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        embed.set_footer(text=f"第 {self.current_page + 1} / {self.total_pages + 1} 页 | 使用下方菜单选择并操作")
        return embed

    def update_view(self):
        self.clear_items()
        
        if not self.current_page_items:
            self.select_menu.disabled = True
            self.select_menu.options = [
                discord.SelectOption(
                    label="目前没有任何项目",
                    description="目前没有可以操作的项目",
                    value="placeholder_empty"
                )
            ]
            self.select_menu.max_values = 1
            self.select_menu.placeholder = "本页无项目"
            self.delete_button.disabled = True
        else:
            self.select_menu.disabled = False
            self.select_menu.placeholder = "请选择项目..."  
            start_index = self.current_page * self.bot.UPDATES_PER_PAGE
            options = []
            for i, item in enumerate(self.current_page_items):
                uid = start_index + i + 1
                db_id = item[0]
                if self.item_type == 'thread':
                    statuses = []
                    if item[2]: statuses.append("Release")
                    if item[3]: statuses.append("Test")
                    status_str = f" ({', '.join(statuses)})" if statuses else ""
                    description_text = f"<#{item[1]}>{status_str}"
                else:
                    description_text = f"作者 <@{item[1]}>"
                options.append(discord.SelectOption(label=f"UID: {uid}", value=str(db_id), description=description_text))
            self.select_menu.options = options
            self.select_menu.max_values = len(options)
            self.delete_button.disabled = False

        is_first_page = self.current_page == 0
        is_last_page = self.current_page >= self.total_pages
        self.first_page_button.disabled = is_first_page
        self.prev_page_button.disabled = is_first_page
        self.next_page_button.disabled = is_last_page
        self.last_page_button.disabled = is_last_page
        
        self.add_item(self.select_menu)
        self.add_item(self.delete_button)

        self.add_item(self.first_page_button)
        self.add_item(self.prev_page_button)
        self.add_item(self.next_page_button)
        self.add_item(self.last_page_button)

    async def create_initial_embed(self):
        await self._fetch_page_data()
        self.update_view()
        return self.create_embed()

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

    async def delete_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.select_menu.values:
            await interaction.followup.send("⚠️ 你没有选择任何项目。", ephemeral=True)
            return
        
        ids_to_process = [int(val) for val in self.select_menu.values]

        try:
            async with self.bot.db_pool.acquire() as conn, conn.cursor() as cursor:
                if self.item_type == 'thread':
                    sql = f"UPDATE thread_subscriptions SET subscribe_release = FALSE, subscribe_test = FALSE WHERE subscription_id IN ({','.join(['%s']*len(ids_to_process))}) AND user_id = %s"
                else:
                    sql = f"DELETE FROM author_follows WHERE follow_id IN ({','.join(['%s']*len(ids_to_process))}) AND follower_id = %s"
                
                params = tuple(ids_to_process) + (self.user_id,)
                await cursor.execute(sql, params)
                await conn.commit()

            self.total_item_count -= len(ids_to_process)
            self.total_pages = max(0, (self.total_item_count - 1) // self.bot.UPDATES_PER_PAGE)
            if self.current_page > self.total_pages:
                self.current_page = self.total_pages
            self.select_menu.values.clear()
            await self._fetch_page_data()
            self.update_view()
            embed = self.create_embed()
            await interaction.edit_original_response(embed=embed, view=self)

        except Exception as err:
            logging.error(f"数据库错误于 delete_callback: {err}")
            await interaction.followup.send(f"❌ SQL_Error: {err}\n数据库操作失败，请联系开发者", ephemeral=True)

    async def page_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data['custom_id']
        
        if custom_id == 'page_first':
            self.current_page = 0
        elif custom_id == 'page_prev':
            self.current_page = max(0, self.current_page - 1)
        elif custom_id == 'page_next':
            self.current_page = min(self.total_pages, self.current_page + 1)
        elif custom_id == 'page_last':
            self.current_page = self.total_pages
        
        await interaction.response.defer() # 翻页操作需要defer
        await self._fetch_page_data()
        self.update_view()
        embed = self.create_embed()
        await interaction.edit_original_response(embed=embed, view=self)

# --- DM:查看更新UI ---
class UpdatesPaginatorView(ui.View):
    def __init__(self, bot_instance, user_id, thread_updates_count, author_updates_count):
        super().__init__(timeout=300)
        self.bot = bot_instance
        self.user_id = user_id
        
        self.thread_updates_count = thread_updates_count
        self.author_updates_count = author_updates_count

        self.current_page_items = []
        
        self.current_view_state = "initial"
        self.current_page = 0
        self.total_pages = 0
        
        self._create_components()
        self.update_view()

    def _create_components(self):
        self.threads_button = ui.Button(style=discord.ButtonStyle.primary, custom_id="view_thread_updates")
        self.threads_button.callback = self.initial_choice_callback
        self.authors_button = ui.Button(style=discord.ButtonStyle.secondary, custom_id="view_author_updates")
        self.authors_button.callback = self.initial_choice_callback

        self.select_menu = ui.Select(placeholder="选择要操作的项目...", min_values=1, row=0)
        self.select_menu.callback = self.select_callback

        self.mark_selected_button = ui.Button(label="标记选中为已读", style=discord.ButtonStyle.success, emoji="✅", row=1, custom_id="mark_selected")
        self.mark_selected_button.callback = self.mark_selected_as_read_callback
        
        self.mark_all_button = ui.Button(label="标记全部为已读", style=discord.ButtonStyle.danger, emoji="🗑️", row=1, custom_id="mark_all_paginated")
        self.mark_all_button.callback = self.mark_all_as_read_callback

        self.first_page_button = ui.Button(label="«", style=discord.ButtonStyle.secondary, row=2, custom_id="page_first")
        self.first_page_button.callback = self.page_callback
        self.prev_page_button = ui.Button(label="‹", style=discord.ButtonStyle.primary, row=2, custom_id="page_prev")
        self.prev_page_button.callback = self.page_callback
        self.next_page_button = ui.Button(label="›", style=discord.ButtonStyle.primary, row=2, custom_id="page_next")
        self.next_page_button.callback = self.page_callback
        self.last_page_button = ui.Button(label="»", style=discord.ButtonStyle.secondary, row=2, custom_id="page_last")
        self.last_page_button.callback = self.page_callback
        
        self.back_button = ui.Button(label="返回", style=discord.ButtonStyle.grey, emoji="↩️", row=3, custom_id="go_back")
        self.back_button.callback = self.go_back_callback

    async def _fetch_page_data(self): #从数据库中只获取当前页所需的数据
        if self.current_view_state == "initial":
            self.current_page_items = []
            return

        limit = self.bot.UPDATES_PER_PAGE
        offset = self.current_page * limit
        try:
            async with self.bot.db_pool.acquire() as conn, conn.cursor() as cursor:
                if self.current_view_state == 'threads':
                    await cursor.execute("SELECT author_id FROM author_follows WHERE follower_id = %s", (self.user_id,))
                    followed_author_ids = {row[0] for row in await cursor.fetchall()}
                    
                    sql = """
                        SELECT t.thread_id, t.author_id, t.last_update_type, t.last_update_url, t.last_update_message, t.last_update_at
                        FROM thread_subscriptions ts JOIN managed_threads t ON ts.thread_id = t.thread_id
                        WHERE ts.user_id = %s AND ts.has_new_update = TRUE AND (
                            (ts.subscribe_release = TRUE AND t.last_update_type = 'release') OR
                            (ts.subscribe_test = TRUE AND t.last_update_type = 'test')
                        )
                        ORDER BY t.last_update_at DESC
                        LIMIT %s OFFSET %s
                    """
                    await cursor.execute(sql, (self.user_id, limit, offset))
                    raw_updates = await cursor.fetchall()
                    
                    processed_updates = []
                    for update in raw_updates:
                        is_followed = update[1] in followed_author_ids
                        processed_updates.append(update + (is_followed,))
                    self.current_page_items = processed_updates

                elif self.current_view_state == 'authors':
                    sql = """
                        SELECT t.thread_id, t.author_id, t.last_update_url, t.last_update_at
                        FROM follower_thread_notifications ftn JOIN managed_threads t ON ftn.thread_id = t.thread_id
                        WHERE ftn.follower_id = %s AND ftn.thread_id NOT IN (
                            SELECT ts.thread_id FROM thread_subscriptions ts JOIN managed_threads t ON ts.thread_id = t.thread_id
                            WHERE ts.user_id = %s AND ts.has_new_update = TRUE
                        )
                        ORDER BY t.last_update_at DESC
                        LIMIT %s OFFSET %s
                    """
                    await cursor.execute(sql, (self.user_id, self.user_id, limit, offset))
                    self.current_page_items = await cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching page data: {e}")
            self.current_page_items = []
 
    def update_view(self): #根据当前状态，更新组件属性并决定显示哪些组件 
        self.clear_items()
        
        if self.current_view_state == "initial":
            if self.thread_updates_count > 0:
                self.threads_button.label = f"查看订阅更新 ({self.thread_updates_count})"
                self.add_item(self.threads_button)
            if self.author_updates_count > 0:
                self.authors_button.label = f"查看作者动态 ({self.author_updates_count})"
                self.add_item(self.authors_button)
            return

        total_items_count = 0
        if self.current_view_state == 'threads':
            total_items_count = self.thread_updates_count
            self.mark_all_button.label = "标记所有订阅为已读" 
        elif self.current_view_state == 'authors':
            total_items_count = self.author_updates_count
            self.mark_all_button.label = "标记所有动态为已读"

        self.total_pages = max(0, (total_items_count - 1) // self.bot.UPDATES_PER_PAGE)
        page_items = self.current_page_items

        options = []
        if page_items:
            start_index = self.current_page * self.bot.UPDATES_PER_PAGE
            for i, item in enumerate(page_items):
                uid = start_index + i + 1
                thread_id = item[0]
                value = f"thread_{thread_id}" if self.current_view_state == 'threads' else f"author_{thread_id}"
                options.append(discord.SelectOption(label=f"UID: {uid}", value=value, description=f"帖子 <#{thread_id}>"))
            self.select_menu.options = options
            self.select_menu.max_values = len(options)
            self.select_menu.disabled = False
            self.mark_selected_button.disabled = False
        else:
            self.select_menu.options = [    
                discord.SelectOption(
                    label="目前没有任何项目",
                    description="目前没有可以操作的项目",
                    value="placeholder_empty"
                )
            ]
            self.select_menu.max_values = 1
            self.select_menu.placeholder = "本页无项目"
            self.select_menu.disabled = True
            self.mark_selected_button.disabled = True
        
        self.mark_all_button.disabled = (total_items_count == 0)
        is_first, is_last = (self.current_page == 0), (self.current_page >= self.total_pages)
        self.first_page_button.disabled = is_first
        self.prev_page_button.disabled = is_first
        self.next_page_button.disabled = is_last
        self.last_page_button.disabled = is_last

        self.add_item(self.select_menu)
        self.add_item(self.mark_selected_button)
        self.add_item(self.mark_all_button)
        self.add_item(self.first_page_button)
        self.add_item(self.prev_page_button)
        self.add_item(self.next_page_button)
        self.add_item(self.last_page_button)
        self.add_item(self.back_button)

    async def create_embed(self):
        """异步创建Embed，在需要时会自动获取分页数据。"""
        if self.current_view_state == "initial":
            return self.create_initial_embed()
        
        await self._fetch_page_data()
        
        if self.current_view_state == "threads":
            return self.create_threads_embed()
        elif self.current_view_state == "authors":
            return self.create_authors_embed()

    def create_initial_embed(self): 
        description = "你有新的动态未查看，请选择要查看的类别："
        if self.thread_updates_count == 0 and self.author_updates_count == 0:
            description = "🎉 你已经看完了所有最新动态！"
        embed = discord.Embed(title="📬 查看更新", description=description, color=discord.Color.blurple())
        embed.set_footer(text="选择一个选项以继续")
        return embed

    def _create_paginated_embed(self, title, line_formatter):
        """统一处理分页Embed的创建，并正确计算UID。"""
        start_index = self.current_page * self.bot.UPDATES_PER_PAGE
        lines = [line_formatter(start_index + i + 1, item) for i, item in enumerate(self.current_page_items)]
        description = "\n\n".join(lines) if lines else "此类更新已全部看完。"
        embed = discord.Embed(title=title, description=description, color=discord.Color.teal())
        embed.set_footer(text=f"第 {self.current_page + 1} / {self.total_pages + 1} 页 | 使用下拉菜单选择项目")
        return embed

    def create_threads_embed(self):
        def formatter(uid, item): # <-- 接收计算好的 uid
            thread_id, _, update_type, url, message, _, is_followed = item
            if len(message) > 50:
                display_message  = message[:50] + "..."
            else:
                display_message = message
            link = f"[▶跳转至最新的楼层]({url})" if url else "帖子"
            update_type_text = "发行版" if update_type == 'release' else '测试版'
            followed_notice = " (❤️-来自您关注的作者)" if is_followed else ""
            return f"**{uid}.** <#{thread_id}> {followed_notice} 发布了新的 **{update_type_text}** 更新！{link}\n└─ 简述: {display_message}"
        return self._create_paginated_embed("📄 订阅的帖子更新", formatter)

    def create_authors_embed(self):
        def formatter(uid, item): # 同理进行操作
            thread_id, author_id, url, _ = item
            if url is None:
                return f"**{uid}.** ❤️ 您关注的作者 <@{author_id}> 发布了新帖子: <#{thread_id}>"
            else:
                return f"**{uid}.** ❤️ 您关注的作者 <@{author_id}> 更新了帖子 <#{thread_id}>! \n└─[▶跳转至最新的楼层]({url})"
        return self._create_paginated_embed("👤 关注的作者动态", formatter)

    async def _update_and_respond(self, interaction: discord.Interaction):
        embed = await self.create_embed()
        self.update_view()
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed,view=self)
        except Exception as e:
            logging.error(f"error in _update_and_respond:{e}")

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
    
    async def initial_choice_callback(self, interaction: discord.Interaction):
        self.current_view_state = 'threads' if interaction.data['custom_id'] == 'view_thread_updates' else 'authors'
        self.current_page = 0
        await self._update_and_respond(interaction)
        
    async def go_back_callback(self, interaction: discord.Interaction):
        self.current_view_state = 'initial'
        self.current_page = 0
        await self._update_and_respond(interaction)

    async def page_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data['custom_id']
        if custom_id == 'page_first': self.current_page = 0
        elif custom_id == 'page_prev': self.current_page = max(0, self.current_page - 1)
        elif custom_id == 'page_next': self.current_page = min(self.total_pages, self.current_page + 1)
        elif custom_id == 'page_last': self.current_page = self.total_pages
        await self._update_and_respond(interaction)
        
    async def mark_selected_as_read_callback(self, interaction: discord.Interaction):
        if not self.select_menu.values:
            await interaction.response.send_message("⚠️ 你没有选择任何项目。", ephemeral=True, delete_after=5)
            return
        
        await interaction.response.defer()
        thread_ids_to_update, thread_ids_to_delete = set(), set()
        for value in self.select_menu.values:
            update_type, thread_id_str = value.split("_", 1)
            thread_id = int(thread_id_str)
            if update_type == "thread": thread_ids_to_update.add(thread_id)
            else: thread_ids_to_delete.add(thread_id)

        try:
            async with self.bot.db_pool.acquire() as conn, conn.cursor() as cursor:
                if thread_ids_to_update:
                    sql = f"UPDATE thread_subscriptions SET has_new_update = FALSE WHERE user_id = %s AND thread_id IN ({','.join(['%s']*len(thread_ids_to_update))})"
                    await cursor.execute(sql, (self.user_id, *thread_ids_to_update))
                if thread_ids_to_delete:
                    sql = f"DELETE FROM follower_thread_notifications WHERE follower_id = %s AND thread_id IN ({','.join(['%s']*len(thread_ids_to_delete))})"
                    await cursor.execute(sql, (self.user_id, *thread_ids_to_delete))
                await conn.commit()
            
            if self.current_view_state == 'threads' and thread_ids_to_update:
                self.thread_updates_count -= len(thread_ids_to_update)
            elif self.current_view_state == 'authors' and thread_ids_to_delete:
                self.author_updates_count -= len(thread_ids_to_delete)

            new_total_items = self.thread_updates_count if self.current_view_state == 'threads' else self.author_updates_count
            new_total_pages = max(0, (new_total_items - 1) // self.bot.UPDATES_PER_PAGE)
            if self.current_page > new_total_pages:
                self.current_page = new_total_pages
            self.select_menu.values.clear()
            await self._update_and_respond(interaction)

        except Exception as err:
            logging.error(f"数据库错误于 mark_selected: {err}")
            await interaction.followup.send(f"❌ Error: {err}", ephemeral=True)

    async def mark_all_as_read_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            async with self.bot.db_pool.acquire() as conn, conn.cursor() as cursor:
                if self.current_view_state == 'threads':
                    await cursor.execute("UPDATE thread_subscriptions SET has_new_update = FALSE WHERE user_id = %s", (self.user_id,))
                    self.thread_updates_count = 0
                elif self.current_view_state == 'authors':
                    await cursor.execute("DELETE FROM follower_thread_notifications WHERE follower_id = %s", (self.user_id,))
                    self.author_updates_count = 0
                else:
                    await interaction.followup.send("未知视图状态，操作已取消。", ephemeral=True)
                    return
                await conn.commit()

            self.current_view_state = 'initial'
            self.current_page = 0
            
            embed = await self.create_embed()
            self.update_view()
            await interaction.edit_original_response(embed=embed, view=self)

        except Exception as err:
            logging.error(f"数据库错误于 mark_all_as_read_callback: {err}")
            await interaction.edit_original_response(content=f"❌ 数据库操作失败: {err}", view=None, embed=None)

# --- DM:用户控制面板UI ---
class UserPanel(ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    async def _show_management_panel(self, interaction: discord.Interaction, item_type: str):
        bot: "MyBot" = interaction.client
        user = interaction.user
        await interaction.response.defer(ephemeral=True)
        total_items_count = 0
        try:
            async with bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    if item_type == 'thread':
                        sql ="""
                        SELECT COUNT(subscription_id)
                        FROM thread_subscriptions
                        WHERE user_id = %s AND (subscribe_release = TRUE OR subscribe_test = TRUE)
                    """
                        await cursor.execute(sql, (user.id,))
                        total_items_count = (await cursor.fetchone())[0]
                    else: # author
                        await cursor.execute("SELECT COUNT(follow_id) FROM author_follows WHERE follower_id = %s", (user.id,))
                        total_items_count = (await cursor.fetchone())[0]
        except Exception as err:
            await interaction.followup.send(f"❌ 数据获取失败: {err}", ephemeral=True)
            return
        if not total_items_count:
            title = bot.MANAGE_SUBS_TITLE if item_type == 'thread' else bot.MANAGE_AUTHORS_TITLE
            embed = discord.Embed(title=title, description="你还没有任何订阅/关注。", color=discord.Color.orange())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        view = ManagementPaginatorView(bot, user.id, item_type,total_items_count)
        embed = await view.create_initial_embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @ui.button(label="查看订阅的帖子", style=discord.ButtonStyle.secondary, emoji="📄")
    async def view_subscribed_threads(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_management_panel(interaction, 'thread')

    @ui.button(label="查看关注的作者", style=discord.ButtonStyle.secondary, emoji="👤")
    async def view_followed_authors(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_management_panel(interaction, 'author')

    @ui.button(label="查看更新", style=discord.ButtonStyle.primary, emoji="📬")
    async def view_updates(self, interaction: discord.Interaction, button: ui.Button):
        bot: "MyBot" = interaction.client
        user = interaction.user
        await interaction.response.defer(ephemeral=True)

        try:
            async with bot.db_pool.acquire() as conn, conn.cursor() as cursor:
                thread_count_sql = """
                SELECT COUNT(t.thread_id)
                FROM thread_subscriptions ts JOIN managed_threads t ON ts.thread_id = t.thread_id
                WHERE ts.user_id = %s AND ts.has_new_update = TRUE AND (
                    (ts.subscribe_release = TRUE AND t.last_update_type = 'release') OR
                    (ts.subscribe_test = TRUE AND t.last_update_type = 'test')
                )
            """
                await cursor.execute(thread_count_sql, (user.id,))
                thread_updates_count = (await cursor.fetchone())[0]
            # 查询作者动态的总数 (排除已在订阅更新中显示的部分)
                author_count_sql = """
                SELECT COUNT(ftn.thread_id)
                FROM follower_thread_notifications ftn
                WHERE ftn.follower_id = %s AND ftn.thread_id NOT IN (
                    SELECT ts.thread_id
                    FROM thread_subscriptions ts JOIN managed_threads t ON ts.thread_id = t.thread_id
                    WHERE ts.user_id = %s AND ts.has_new_update = TRUE AND (
                        (ts.subscribe_release = TRUE AND t.last_update_type = 'release') OR
                        (ts.subscribe_test = TRUE AND t.last_update_type = 'test')
                    )
                )
            """
                await cursor.execute(author_count_sql, (user.id, user.id))
                author_updates_count = (await cursor.fetchone())[0]

        except Exception as err:
            logging.error(f"数据库错误于 view_updates: {err}")
            await interaction.followup.send(f"❌ 数据库查询失败：{err}", ephemeral=True)
            return

        #传递计数给Paginator
        paginator = UpdatesPaginatorView(bot, user.id, thread_updates_count, author_updates_count)
    
        embed = await paginator.create_embed()
        await interaction.followup.send(embed=embed, view=paginator, ephemeral=True)

    async def _get_update_counts(self, bot, user_id):
        async with bot.db_pool.acquire() as conn, conn.cursor() as cursor:
            thread_sql = """
                SELECT COUNT(DISTINCT t.thread_id) FROM thread_subscriptions ts JOIN managed_threads t ON ts.thread_id = t.thread_id
                WHERE ts.user_id = %s AND ts.has_new_update = TRUE AND (
                    (ts.subscribe_release = TRUE AND t.last_update_type = 'release') OR
                    (ts.subscribe_test = TRUE AND t.last_update_type = 'test')
                )
            """
            await cursor.execute(thread_sql, (user_id,))
            thread_count = (await cursor.fetchone())[0]

            author_sql = """
                SELECT COUNT(*) FROM follower_thread_notifications
                WHERE follower_id = %s AND thread_id NOT IN (
                    SELECT ts.thread_id FROM thread_subscriptions ts JOIN managed_threads t ON ts.thread_id = t.thread_id
                    WHERE ts.user_id = %s AND ts.has_new_update = TRUE AND (
                        (ts.subscribe_release = TRUE AND t.last_update_type = 'release') OR
                        (ts.subscribe_test = TRUE AND t.last_update_type = 'test')
                    )
                )
            """
            await cursor.execute(author_sql, (user_id, user_id))
            author_count = (await cursor.fetchone())[0]
            return thread_count, author_count

    @ui.button(label="刷新", style=discord.ButtonStyle.success, emoji="🔄", row=1)
    async def refresh_panel(self, interaction: discord.Interaction, button: ui.Button):
        bot: "MyBot" = interaction.client
        try:
            thread_count, author_count = await self._get_update_counts(bot, interaction.user.id)
            desc = bot.DM_PANEL_TEXT.replace("{{user}}", interaction.user.mention)\
                                    .replace("{{thread_update_number}}", str(thread_count))\
                                    .replace("{{author_update_number}}", str(author_count))
            embed = discord.Embed(title=bot.DM_PANEL_TITLE, description=desc, color=discord.Color.blurple())\
                           .set_footer(text=f"将在5分钟后消失... |At {get_utc8_now_str()}")
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as err:
            await interaction.response.send_message(f"❌ SQL_Error:{err}\n刷新失败，无法连接到数据库。", ephemeral=True, delete_after=10)

# --- 追踪新帖子的UI ---
class TrackNewThreadView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction:discord.Interaction)->bool: #交互check
        if isinstance(interaction.channel, discord.Thread) and interaction.channel.owner_id:
            if interaction.user.id == interaction.channel.owner_id:
                return True
            else:
                await interaction.response.send_message("❌ 只有帖子作者才能进行此操作。", ephemeral=True)
                return False
        await interaction.response.send_message("❌ 交互错误：无法获取帖子及作者ID。",ephemeral=True)
        return False
    
    @ui.button(label="是", style=discord.ButtonStyle.success, custom_id="track_thread_choice_yes") #选择是的话
    async def yes_button(self, interaction: discord.Interaction, button: ui.Button):
        bot: "MyBot" = interaction.client
        thread_channel: discord.Thread = interaction.channel
        thread_owner_id = thread_channel.owner_id
        thread_id = thread_channel.id
        guild_id = thread_channel.guild.id

        original_embed = interaction.message.embeds[0]
        original_embed.description = "⚙️ 正在为您处理，请稍候..."
        original_embed.color = discord.Color.green()
        await interaction.response.edit_message(embed=original_embed, view=None)

        try:
            async with bot.db_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    #先进行查询managed_thread表中是否含有，有的话则表示该帖子不是第一次创建更新推流
                    await cursor.execute("SELECT EXISTS(SELECT 1 FROM managed_threads WHERE thread_id = %s) AS select_result;",(thread_id))
                    result = await cursor.fetchone()
                    select_result = result['select_result']
                    if select_result == 0: #第一次创建更新推流
                        await cursor.execute(
                            """INSERT INTO managed_threads (thread_id, guild_id, author_id)
                            VALUES (%s, %s, %s) AS new_values
                            ON DUPLICATE KEY UPDATE
                            guild_id = new_values.guild_id,
                            author_id = new_values.author_id""",
                            (thread_id, guild_id, thread_owner_id)
                        )
                        #开始遍历所有关注了该作者的用户，并且在新follower_thread_notifications表中插入，表示这是需要通知的新帖子
                        insert_notification_sql = """
                        INSERT INTO follower_thread_notifications (follower_id, thread_id, guild_id)
                        SELECT follower_id, %s, %s FROM author_follows WHERE author_id = %s
                    """
                        await cursor.execute(insert_notification_sql, (thread_id, guild_id, thread_owner_id))
                await conn.commit()
        except Exception as e:
            logging.error(f"数据库错误在track_thread_choice_yes :{e}")

        original_embed = interaction.message.embeds[0]
        original_embed.description = "✅ 操作成功:\n\n已经在下方为您创建订阅入口"
        original_embed.color = discord.Color.green()
        original_embed.set_footer(text=f"操作者: {interaction.user.mention} | At:{get_utc8_now_str()}")
        await interaction.edit_original_response(embed=original_embed, view=None)
        #开始创建订阅入口
        followup_embed = discord.Embed(title=bot.EMBED_TITLE, description=bot.EMBED_TEXT, color=discord.Color.blue())
        await interaction.followup.send(embed=followup_embed, view=SubscriptionView())

    @ui.button(label="否",style=discord.ButtonStyle.danger, custom_id="track_thread_choice_no")
    async def no_button(self, interaction: discord.Interaction, button: ui.Button):
        original_embed = interaction.message.embeds[0]
        original_embed.description = "✅ 操作成功：否\n\n本次将不为您创建更新推流。但您之后仍可使用 `/创建更新推流` 手动为您的帖子手动启用更新推流。"
        original_embed.color = discord.Color.greyple()
        original_embed.set_footer(text=f"操作者: {interaction.user.mention} | {get_utc8_now_str()}")
        await interaction.response.edit_message(embed=original_embed, view=None)

    @ui.button(label="不再打扰我", style=discord.ButtonStyle.secondary, custom_id="track_thread_choice_never")
    async def never_button(self, interaction: discord.Interaction, button: ui.Button):
        bot: "MyBot" = interaction.client
        user_id = interaction.user.id
        try:
            async with bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    sql = """
                        INSERT INTO users (user_id, track_new_thread) 
                        VALUES (%s, FALSE)
                        ON DUPLICATE KEY UPDATE track_new_thread = FALSE;
                    """
                    await cursor.execute(sql, (user_id,))
                await conn.commit()
        except Exception as e:
            logging.error(f"数据库错误于[track_thread_choice_never]: {e}")
            await interaction.response.send_message(f"❌ SQL_Error: {e}\n数据库操作失败，请联系开发者。", ephemeral=True)
            return

        # 编辑原始消息，告知用户已选择，并移除按钮
        original_embed = interaction.message.embeds[0]
        original_embed.description = "🔴 操作成功:\n\n好的，以后在您发布的新帖子中将不再自动提醒您。但您仍然可以随时使用 `/创建更新推流` 指令来手动为您的帖子启用创建更新推流\n-# (使用指令后将取消您的不再提醒状态)"
        original_embed.color = discord.Color.dark_grey()
        original_embed.set_footer(text=f"操作者: {interaction.user.mention} | {get_utc8_now_str()}")
        await interaction.response.edit_message(embed=original_embed, view=None)

# --- 添加权限组UI ---
class AddPermissionModal(discord.ui.Modal, title="添加权限用户"):
    def __init__(self, parent_view: 'PermissionManageView'):
        super().__init__(timeout=300)
        self.parent_view = parent_view

    user_id_input = discord.ui.TextInput(
        label="用户的雪花ID",
        placeholder="请在此处输入一个有效的Discord雪花ID",
        required=True,
        style=discord.TextStyle.short,
        min_length=17,
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_id = int(self.user_id_input.value)
        except ValueError:
            await interaction.response.send_message("❌ 输入的不是有效的雪花ID，请确保只包含数字。", ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)

        current_permissions = await self.parent_view.fetch_permissions()
        
        if target_id == self.parent_view.author_id or target_id in current_permissions:
            await interaction.followup.send("❌ 该用户已经是帖主或已在权限组中。", ephemeral=True)
            return

        try:
            first_empty_slot = current_permissions.index(None)
            slot_to_update = f"thread_permission_group_{first_empty_slot + 1}"

            async with self.parent_view.bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        f"UPDATE managed_threads SET {slot_to_update} = %s WHERE thread_id = %s",
                        (target_id, self.parent_view.thread_id)
                    )
                await conn.commit()

            await interaction.followup.send(f"✅ 成功将 `{target_id}` 添加到权限组。", ephemeral=True)
            await self.parent_view.update_view(interaction, use_followup=True)

        except ValueError:
            await interaction.followup.send("❌ 权限组已满(4/4),无法添加新成员。", ephemeral=True)
        except Exception as e:
            logging.error(f"在 AddPermissionModal 中发生数据库错误: {e}")
            await interaction.followup.send("❌ 添加失败，发生内部错误。", ephemeral=True)

# --- 帖子的权限组管理UI ---
class PermissionManageView(discord.ui.View):
    def __init__ (self,bot,thread_id:int,author_id:int,initial_permissions:list[int|None]):
        super().__init__(timeout=None)

        self.bot = bot
        self.thread_id = thread_id
        self.author_id = author_id  
        self.current_permissions = initial_permissions
        self.selected_slot_to_remove = None
        self.remove_button = self.create_remove_button()
        self.select_menu = self.create_select_menu()
        self.add_item(self.create_add_button())
        self.add_item(self.select_menu)
        self.add_item(self.remove_button)

    async def update_view(self,interaction:discord.Interaction,use_followup:bool = False):
        self.current_permissions = await self.fetch_permissions()
        self.select_menu.options = self._create_select_options()
        self.select_menu.disabled = not any(self.current_permissions)
        self.remove_button.disabled = self.selected_slot_to_remove is None
        if self.selected_slot_to_remove is None:
            self.select_menu.placeholder = "选择一个要移除的目标..."
        else:
            display_uid = int(self.selected_slot_to_remove) + 1
            self.select_menu.placeholder = f"已选中【UID：{display_uid}】"
        embed = self.create_embed()
        if use_followup:
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

        # 用于获取最新的权限组成员，并且转换为列表
    async def fetch_permissions(self) -> list[int | None]:
            async with self.bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """SELECT thread_permission_group_1, thread_permission_group_2, 
                                thread_permission_group_3, thread_permission_group_4 
                        FROM managed_threads WHERE thread_id = %s""",
                        (self.thread_id,)
                    )
                    result = await cursor.fetchone()
                    return list(result) if result else [None, None, None, None]
        
    def create_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title=getattr(self.bot, 'EMBED_TITLE', '权限管理'),
                description="管理当前帖子的权限用户组。",
                color=discord.Color.blurple()
            )
            #添加帖主以及其他的权限组成员
            embed.add_field(name="UID 1 (帖主)", value=f"<@{self.author_id}>", inline=False)
            slot_index = 2
            for user_id in self.current_permissions:
                if user_id:
                    embed.add_field(name=f"UID {slot_index}", value=f"<@{user_id}>", inline=False)
                    slot_index += 1
            empty_slots = self.current_permissions.count(None)
            if empty_slots > 0:
                embed.add_field(name=f"UID {slot_index} - {slot_index + empty_slots - 1}", value="*空闲*", inline=False)
                
            embed.set_footer(text=f"帖子ID: {self.thread_id}")
            return embed
    
    def _create_select_options(self) -> list[discord.SelectOption]:
            options = []
            for i, user_id in enumerate(self.current_permissions):
                if user_id:
                    options.append(
                        discord.SelectOption(
                            label=f"UID {i + 2}: {user_id}",
                            description="选择以移除此用户",
                            value=str(i + 1)  # value 存储的是 group_1, group_2... 的数字
                        )
                    )
            if not options:
                options.append(discord.SelectOption(
                label="目前权限组为空",
                description="没有可以移除的目标",
                value="placeholder_empty",
                emoji="🤷"
            ))
            return options
        
    def create_add_button(self) -> discord.ui.Button:
            async def add_callback(interaction: discord.Interaction):
                modal = AddPermissionModal(parent_view=self)
                await interaction.response.send_modal(modal)
            
            button = discord.ui.Button(label="移入", style=discord.ButtonStyle.green, emoji="📥")
            button.callback = add_callback
            return button

    def create_select_menu(self) -> discord.ui.Select:
            async def select_callback(interaction: discord.Interaction):
                selected_value = self.select_menu.values[0]
                if selected_value == "placeholder_empty":
                    await interaction.response.defer()
                    return
                self.selected_slot_to_remove = selected_value
                await self.update_view(interaction, use_followup=False)
            select = discord.ui.Select(
                placeholder="选择一个要移除的目标...",
                options=self._create_select_options(),
                disabled= not any(self.current_permissions)
            )
            select.callback = select_callback
            return select

    def create_remove_button(self) -> discord.ui.Button:
            async def remove_callback(interaction: discord.Interaction):
                if not self.selected_slot_to_remove:
                    await interaction.response.send_message("❌ 你没有选择任何目标。", ephemeral=True)
                    return

                await interaction.response.defer()

                slot_to_remove_index = int(self.selected_slot_to_remove) - 1
                new_permissions = [p for i, p in enumerate(self.current_permissions) if i != slot_to_remove_index and p is not None]
                while len(new_permissions) < 4:
                    new_permissions.append(None)
                try:
                    async with self.bot.db_pool.acquire() as conn:
                        async with conn.cursor() as cursor:
                            await cursor.execute(
                                """UPDATE managed_threads SET 
                                thread_permission_group_1 = %s,
                                thread_permission_group_2 = %s,
                                thread_permission_group_3 = %s,
                                thread_permission_group_4 = %s
                                WHERE thread_id = %s""",
                                (*new_permissions, self.thread_id)
                            )
                        await conn.commit()
                    
                    # 重置选择状态
                    self.selected_slot_to_remove = None
                    await self.update_view(interaction,use_followup=True)

                except Exception as e:
                    logging.error(f"在 remove_callback 中发生数据库错误: {e}")
                    await interaction.followup.send("❌ 移除失败，发生内部错误。", ephemeral=True)

            button = discord.ui.Button(label="移除", style=discord.ButtonStyle.red, emoji="📤", disabled=True)
            button.callback = remove_callback
            return button