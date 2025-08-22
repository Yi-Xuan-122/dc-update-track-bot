import os
import discord
from discord import app_commands, ui
from discord.ext import commands
import aiomysql
from aiomysql import Error,pool
import asyncio
from asyncio import Queue
import datetime
import psutil
import math
from discord.errors import Forbidden

# --- 时区 ---
UTC_PLUS_8 = datetime.timezone(datetime.timedelta(hours=8))

def get_utc8_now_str():
    return datetime.datetime.now(UTC_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")

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
        new_status = None
        await interaction.response.defer()

        await check_and_create_user(bot.db_pool,user_id)

        try:
            async with bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    toggle_sql = """
                INSERT INTO thread_subscriptions (user_id, thread_id, subscribe_release)
                VALUES (%s, %s, TRUE)
                ON DUPLICATE KEY UPDATE subscribe_release = NOT subscribe_release;
                """
                    await cursor.execute(toggle_sql, (user_id, thread_id))
                    select_sql = "SELECT subscribe_release FROM thread_subscriptions WHERE user_id = %s AND thread_id = %s"
                    await cursor.execute(select_sql, (user_id, thread_id))    
                    result = await cursor.fetchone()
                    if result:
                        new_status = bool(result[0])
                await conn.commit()

        except Exception as err:
            print(f"数据库错误于[subscribe_release_button]: {err}")
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
        new_status = None
        await interaction.response.defer()
        
        await check_and_create_user(bot.db_pool,user_id)

        try:
            async with bot.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    toggle_sql = """
                INSERT INTO thread_subscriptions (user_id, thread_id, subscribe_test)
                VALUES (%s, %s, TRUE)
                ON DUPLICATE KEY UPDATE subscribe_test = NOT subscribe_test;  
            """
                    await cursor.execute(toggle_sql, (user_id, thread_id))
                    select_sql = "SELECT subscribe_test FROM thread_subscriptions WHERE user_id = %s AND thread_id = %s"
                    await cursor.execute(select_sql, (user_id, thread_id))
                    result = await cursor.fetchone()
                    if result:
                        new_status = bool(result[0])
                await conn.commit()

        except Exception as err:
            print(f"数据库错误于[subscribe_test_button]: {err}")
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
            print(f"数据库错误于[follow_author_button]: {err}")
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
        
        # 修复 BUG 2: 绑定到正确的 page_callback
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
            print(f"Error fetching management page data: {e}")
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
                status_str = f" ({', '.join(statuses)})" if statuses else "" #(无订阅)不应该出现
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
            self.select_menu.options = [discord.SelectOption(label="本页无项目", value="disabled")]
            self.delete_button.disabled = True
        else:
            self.select_menu.disabled = False
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
            
            await self._fetch_page_data()
            self.update_view()
            embed = self.create_embed()
            await interaction.edit_original_response(embed=embed, view=self)

        except Exception as err:
            print(f"数据库错误于 delete_callback: {err}")
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
            print(f"Error fetching page data: {e}")
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
            self.select_menu.options = [discord.SelectOption(label="本页无项目", value="disabled")]
            self.select_menu.max_values = 1
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
        await interaction.response.edit_message(embed=embed, view=self)

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

            await self._update_and_respond(interaction)

        except Exception as err:
            print(f"数据库错误于 mark_selected: {err}")
            await interaction.followup.send(f"❌ 数据库操作失败: {err}", ephemeral=True)

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
            print(f"数据库错误于 mark_all_as_read_callback: {err}")
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
            print(f"数据库错误于 view_updates: {err}")
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
                        await cursor.execute("INSERT IGNORE INTO managed_threads (thread_id, guild_id, author_id) VALUES (%s, %s, %s)",(thread_id, guild_id, thread_owner_id))
                        #开始遍历所有关注了该作者的用户，并且在新follower_thread_notifications表中插入，表示这是需要通知的新帖子
                        insert_notification_sql = """
                        INSERT INTO follower_thread_notifications (follower_id, thread_id)
                        SELECT follower_id, %s FROM author_follows WHERE author_id = %s
                    """
                        await cursor.execute(insert_notification_sql,(thread_id,thread_owner_id))
                await conn.commit()
        except Exception as e:
            print(f"数据库错误在track_thread_choice_yes :{e}")

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
            print(f"数据库错误于[track_thread_choice_never]: {e}")
            await interaction.response.send_message(f"❌ SQL_Error: {e}\n数据库操作失败，请联系开发者。", ephemeral=True)
            return

        # 编辑原始消息，告知用户已选择，并移除按钮
        original_embed = interaction.message.embeds[0]
        original_embed.description = "🔴 操作成功:\n\n好的，以后在您发布的新帖子中将不再自动提醒您。但您仍然可以随时使用 `/创建更新推流` 指令来手动为您的帖子启用创建更新推流\n-# (使用指令后将取消您的不再提醒状态)"
        original_embed.color = discord.Color.dark_grey()
        original_embed.set_footer(text=f"操作者: {interaction.user.mention} | {get_utc8_now_str()}")
        await interaction.response.edit_message(embed=original_embed, view=None)

# --- 复用函数 ---
# 检查并创建用户
async def check_and_create_user(db_pool, user_id:int):
    if not user_id:
        return
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("INSERT INTO users (user_id) VALUES (%s) ON DUPLICATE KEY UPDATE user_id = user_id", (user_id,))  #直接插入!
            await conn.commit()
    except Exception as err:
        print(f"数据库错误于 check_and_create_user: {err}")

# --- 指令组定义 ---

@app_commands.guild_only()
class CommandGroup_bot(app_commands.Group): #查询BOT运行状态command
    
    @app_commands.command(name="运行状态", description="查询目前Bot的运行状态")
    async def hello(self, interaction: discord.Interaction):
        bot: "MyBot" = interaction.client
        if interaction.user.id not in bot.ADMIN_IDS:
            await interaction.response.send_message("❌ 你没有权限使用这个指令。", ephemeral=True)
            print(f"用户 {interaction.user.id} 尝试访问 /bot 运行状态，但没有权限。")
            return
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
            print(f"数据库错误于[track_thread_choice_never]: {e}")
            await interaction.followup.send(f"❌ SQL_Error: {e}\n数据库操作失败，但已成功创建更新推流。请联系开发者", ephemeral=True)

        embed = discord.Embed(title=bot.EMBED_TITLE, description=bot.EMBED_TEXT, color=discord.Color.blue())
        await interaction.followup.send(embed=embed, view=SubscriptionView())

    except Exception as e:
        print(f"未知错误于 /创建更新推流: {e}")
        await interaction.followup.send(f"❌ Unkown_Error:{e}，请联系开发者...", ephemeral=True)
     
@app_commands.command(name="控制面板",description="请在和Bot的私信中使用哦~")
@app_commands.dm_only()
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
        print(f"数据库错误于 控制面板 : {err}")
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
    author = interaction.user
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
                await cursor.execute("SELECT * FROM managed_threads WHERE thread_id = %s", (thread.id,))
                result = await cursor.fetchone()
                if not result:
                    await response_message.edit(content="❌ 这个帖子没有开启更新推流功能，请先使用 /创建更新推流 指令。")
                    return
        
                thread_owner_id = result[2] #managed_threads的[2]才是author_id
                if author.id != thread_owner_id:
                    await response_message.edit(content="❌ 只有帖子作者可以发送更新推流。")
                    return

        # 先更新状态
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
        await response_message.edit(content=f"❌ SQL_Error：{err}， 请联系开发者")
        return
    
    # --- 数据库操作已全部完成，开始发送响应和幽灵提及 ---

    # 开始预处理提及名单
    user_list = list(users_to_notify)
    total_users = len(user_list)
    processed_users = 0 # 开始处理

    if total_users == 0:
        final_text = bot.UPDATE_TEXT.replace("{{text}}", message).replace("{{url}}", url).replace("{{author}}", author.mention)
        final_embed = discord.Embed(title=bot.UPDATE_TITLE, description=final_text, color=discord.Color.blue())
        final_embed.set_footer(text=f"运行状态：✅没有需要通知的用户|{get_utc8_now_str()}")
        await response_message.edit(embed=final_embed)
        return
    
    mention_delay = bot.UPDATE_MENTION_DELAY / 1000.0
    update_text_template = bot.UPDATE_TEXT.replace("{{author}}", author.mention).replace("{{text}}", message).replace("{{url}}", url)
    update_embed = discord.Embed(title=bot.UPDATE_TITLE, description=update_text_template, color=discord.Color.green())

    for i in range(0, total_users, bot.UPDATE_MENTION_MAX_NUMBER):
        batch = user_list[i:i + bot.UPDATE_MENTION_MAX_NUMBER]
        if not batch: continue

        mention_string = " ".join(f"<@{uid}>" for uid in batch)
        try:
            ghosted_message = await thread.send(mention_string)
            await ghosted_message.delete()
        except discord.Forbidden:
            print(f"无法发送幽灵提及消息，可能是权限不足。")
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

# --- 机器人核心类 ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)
        self.thread_creation_queue = Queue()

        # 载入.env配置
        self.TARGET_GUILD_ID = int(os.getenv("TARGET_GUILD_ID"))
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        self.ADMIN_IDS = [int(uid.strip()) for uid in admin_ids_str.split(',') if uid.strip().isdigit()]
        self.ALLOWED_CHANNELS = [int(c.strip()) for c in os.getenv("ALLOWED_CHANNELS").split(",")]
        self.EMBED_TITLE = os.getenv("EMBED_TITLE")
        self.EMBED_TEXT = os.getenv("EMBED_TEXT")
        self.EMBED_ERROR = os.getenv("EMBED_ERROR")
        self.UPDATE_MENTION_MAX_NUMBER = int(os.getenv("UPDATE_MENTION_MAX_NUMBER"))
        self.UPDATE_MENTION_DELAY = int(os.getenv("UPDATE_MENTION_DELAY"))
        self.UPDATE_TITLE = os.getenv("UPDATE_TITLE")
        self.UPDATE_TEXT = os.getenv("UPDATE_TEXT")
        self.UPDATE_ERROR = os.getenv("UPDATE_ERROR")
        self.DM_PANEL_TITLE = os.getenv("DM_PANEL_TITLE")
        self.DM_PANEL_TEXT = os.getenv("DM_PANEL_TEXT")
        self.VIEW_UPDATES_TITLE = os.getenv("VIEW_UPDATES_TITLE")
        self.VIEW_UPDATES_TEXT = os.getenv("VIEW_UPDATES_TEXT")
        self.UPDATES_PER_PAGE = int(os.getenv("UPDATES_PER_PAGE", 10))
        self.MANAGE_SUBS_TITLE = os.getenv("MANAGE_SUBS_TITLE")
        self.MANAGE_AUTHORS_TITLE = os.getenv("MANAGE_AUTHORS_TITLE")
        self.TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS = int(os.getenv("TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS"))
        self.TRACK_NEW_THREAD_EMBED_TITLE = os.getenv("TRACK_NEW_THREAD_EMBED_TITLE")
        self.TRACK_NEW_THREAD_EMBED_TEXT = os.getenv("TRACK_NEW_THREAD_EMBED_TEXT")
        self.start_time = datetime.datetime.now(datetime.timezone.utc)#我操，修了我半天，移除无关参数的时候我还以为不用了
        self.db_pool = None

    async def setup_hook(self):
        #初始化开始
        # --- 1. 初始化数据库连接池 ---
        print("正在初始化数据库连接池...")
        max_retries = 10
        retry_delay = 5 
        for i in range(max_retries):
            try:
                pool_size_str = os.getenv("POOL_SIZE")
                pool_size_int = int(pool_size_str)

                self.db_pool = await aiomysql.create_pool(
                host='db',  # Docker Compose中定义的MySQL服务名
                user=os.getenv('MYSQL_USER'),
                password=os.getenv('MYSQL_PASSWORD'),
                db=os.getenv('MYSQL_DATABASE'),
                port=3306,
                minsize=1,
                maxsize=pool_size_int,
                pool_recycle=600,
                autocommit=False
            )
                async with self.db_pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT 1")
                        result = await cursor.fetchone()
                        print("数据库连接测试成功。")
                print("数据库连接池创建成功。")
                break

            except Exception as err:
                print(f"数据库连接失败 (尝试 {i+1}/{max_retries}): {err}")
                if i + 1 == max_retries:
                    print("已达到最大重试次数，机器人启动失败。")
                    await self.close() # 彻底关闭机器人
                    return
                print(f"将在 {retry_delay} 秒后重试...")
                await asyncio.sleep(retry_delay)

        # --- 2. 自动创建表 (如果不存在的话) ---
        print("正在检查并创建数据库表...")
        try:
            async with self.db_pool.acquire() as conn:
                async with conn.cursor() as cursor:

                # 表1: 用户信息与状态表
                    await cursor.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                        last_checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                    #为了兼容旧表
                    await cursor.execute("""
                        SELECT COUNT(*) 
                        FROM INFORMATION_SCHEMA.COLUMNS 
                        WHERE TABLE_SCHEMA = DATABASE()
                        AND TABLE_NAME = 'users' 
                        AND COLUMN_NAME = 'track_new_thread'
                    """)

                    result = await cursor.fetchone()
                    if result[0] == 0:  # 列不存在时才添加
                        await cursor.execute("""
                            ALTER TABLE users
                            ADD COLUMN track_new_thread BOOLEAN NOT NULL DEFAULT TRUE;
                        """)

                # 表2: 被管理的帖子表，包含最新更新的缓存
                    await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS managed_threads (
                        thread_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                        guild_id BIGINT UNSIGNED NOT NULL,
                        author_id BIGINT UNSIGNED NOT NULL,
                        last_update_url VARCHAR(255) DEFAULT NULL,
                        last_update_message TEXT DEFAULT NULL,
                        last_update_at TIMESTAMP NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
                        last_update_type ENUM('release', 'test') DEFAULT NULL,
                        FOREIGN KEY (author_id) REFERENCES users(user_id) ON DELETE CASCADE
                    );
                """)

                # 表3: 帖子订阅表，包含独立的ID和更新标志
                    await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS thread_subscriptions (
                        subscription_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id BIGINT UNSIGNED NOT NULL,
                        thread_id BIGINT UNSIGNED NOT NULL,
                        subscribe_release BOOLEAN NOT NULL DEFAULT FALSE,
                        subscribe_test BOOLEAN NOT NULL DEFAULT FALSE,
                        has_new_update BOOLEAN NOT NULL DEFAULT FALSE,
                        UNIQUE KEY unique_subscription (user_id, thread_id),
                        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                        FOREIGN KEY (thread_id) REFERENCES managed_threads(thread_id) ON DELETE CASCADE
                    );
                """)
                
                # 表4: 作者关注表，包含独立的ID和更新标志
                    await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS author_follows (
                        follow_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        follower_id BIGINT UNSIGNED NOT NULL,
                        author_id BIGINT UNSIGNED NOT NULL,
                        UNIQUE KEY unique_follow (follower_id, author_id),
                        FOREIGN KEY (follower_id) REFERENCES users(user_id) ON DELETE CASCADE,
                        FOREIGN KEY (author_id) REFERENCES users(user_id) ON DELETE CASCADE
                    );
                """)
                    
                # 表5：独立的用于记录关注作者逻辑实现通知的表，包含folloer_id以及他们所关注的作者发布新帖子需要更新的thread_id
                    await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS follower_thread_notifications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    follower_id BIGINT UNSIGNED NOT NULL,
                    thread_id BIGINT UNSIGNED NOT NULL,
                    UNIQUE KEY unique_notification (follower_id, thread_id),
                    FOREIGN KEY (follower_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (thread_id) REFERENCES managed_threads(thread_id) ON DELETE CASCADE
                    );
                """)
                    await conn.commit()
                    
            print("数据库表结构已确认。")

        except Exception as err:
            print(f"数据库建表失败: {err}")
                
        target_guild = discord.Object(id=self.TARGET_GUILD_ID)
        self.tree.add_command(CommandGroup_bot(name="bot", description="机器人指令"), guild=target_guild)
        self.tree.add_command(create_update_feed, guild=target_guild)
        self.tree.add_command(update_feed, guild=target_guild)
        self.tree.add_command(manage_subscription_panel)

        self.add_view(SubscriptionView())
        self.add_view(TrackNewThreadView())

        # --- 同步command ---
        try:
            print(f"正在尝试将指令同步到服务器 ID: {target_guild.id}...")
            synced_commands = await self.tree.sync(guild=target_guild) + await self.tree.sync(guild=None)
            print(f"成功同步了 {len(synced_commands)} 个指令。")
            for cmd in synced_commands:
                print(f"  - 指令名称: {cmd.name} (ID: {cmd.id})")
        except Exception as e:
            print(f"指令同步时发生严重错误: {e}")
        print("正在启动后台 thread 处理器任务...")
        self.loop.create_task(self.thread_processor_task())
        if self.TRACK_NEW_THREAD_FROM_ALLOWED_CHANNELS == 1:
            print("启用追踪新帖子功能")
        else:
            print("关闭追踪新帖字功能")

    async def thread_processor_task(self): # 队列中的thread将在这里发送
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                thread = await self.thread_creation_queue.get()
                
                max_attempts = 3
                attempt_delay = 10

                # 将 embed 的创建放在循环外，避免重复创建
                url_buffer = f"https://discord.com/channels/{thread.guild.id}/{thread.id}"
                title_buffer = self.TRACK_NEW_THREAD_EMBED_TITLE
                text_buffer = self.TRACK_NEW_THREAD_EMBED_TEXT.replace("{{author}}", f"<@{thread.owner_id}>").replace("{{thread_url}}", url_buffer)
                embed = discord.Embed(title=title_buffer, description=text_buffer, color=discord.Color.blue()).set_footer(text=f"✅已发送 | At {get_utc8_now_str()}")

                for attempt in range(max_attempts):
                    try:
                        # 尝试发送
                        await thread.send(embed=embed, view=TrackNewThreadView())
                        
                        # 如果上面这行代码没有报错，说明发送成功了
                        print(f"✅ 成功向帖子 '{thread.name}' (ID: {thread.id}) 发送消息。")
                        break  # 成功后必须 break，跳出重试循环

                    except Forbidden as e:
                        if e.code == 40058: # 只处理 40058 错误
                            if attempt < max_attempts - 1: # 如果不是最后一次尝试
                                print(f"⏳ 帖子 {thread.id} 未就绪 (Error 40058)，将在 {attempt_delay} 秒后重试... (尝试 {attempt + 2}/{max_attempts})")
                                await asyncio.sleep(attempt_delay)
                            else: # 如果是最后一次尝试
                                print(f"❌ 在 {max_attempts} 次尝试后，向帖子 {thread.id} 发送消息仍失败 (Error 40058)。")
                        else:
                            # 如果是其他 Forbidden 错误，直接报错并跳出循环
                            print(f"处理帖子 {thread.id} 时遇到未处理的权限错误: {e}")
                            break
                    except Exception as e:
                        # 捕获其他任何异常，直接报错并跳出循环
                        print(f"处理帖子 {thread.id} 时发送消息失败: {e}")
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

    async def on_ready(self):
        print(f'机器人 {self.user} 已成功登录并准备就绪！')