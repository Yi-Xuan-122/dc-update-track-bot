import os
import discord
from discord import app_commands, ui
from discord.ext import commands
import mysql.connector
from mysql.connector import errorcode, pooling
import asyncio
import datetime
import psutil
import math

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

# --- DM:管理关系 ---
class ManagementPaginatorView(ui.View):
    def __init__(self, bot, user_id, items, item_type):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.item_type = item_type
        
        # --- 组件创建部分保持动态 ---
        self.select_menu = ui.Select(placeholder="选择要操作的项目...", min_values=1, row=0)
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

        self.delete_button = ui.Button(label="取消订阅/关注", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
        self.delete_button.callback = self.delete_callback
        self.add_item(self.delete_button)

        self.first_page_button = ui.Button(label="«", style=discord.ButtonStyle.secondary, row=2)
        self.first_page_button.callback = self.first_page_callback
        self.add_item(self.first_page_button)
        # ... (其他分页按钮的创建) ...
        self.prev_page_button = ui.Button(label="‹", style=discord.ButtonStyle.primary, row=2); self.prev_page_button.callback = self.prev_page_callback; self.add_item(self.prev_page_button)
        self.next_page_button = ui.Button(label="›", style=discord.ButtonStyle.primary, row=2); self.next_page_button.callback = self.next_page_callback; self.add_item(self.next_page_button)
        self.last_page_button = ui.Button(label="»", style=discord.ButtonStyle.secondary, row=2); self.last_page_button.callback = self.last_page_callback; self.add_item(self.last_page_button)

        self.set_items(items)

    def set_items(self, items):
        self.all_items = items
        self.current_page = 0
        self.total_pages = max(0, (len(items) - 1) // self.bot.UPDATES_PER_PAGE)
        self.update_components()

    def create_embed(self):
        start_index = self.current_page * self.bot.UPDATES_PER_PAGE
        page_items = self.all_items[start_index : start_index + self.bot.UPDATES_PER_PAGE]

        if not self.all_items:
            return discord.Embed(title="空空如也", description="你还没有任何订阅/关注。", color=discord.Color.orange())

        title = self.bot.MANAGE_SUBS_TITLE if self.item_type == 'thread' else self.bot.MANAGE_AUTHORS_TITLE
        lines = []
        for i, item in enumerate(page_items):
            uid = start_index + i + 1
            if self.item_type == 'thread':
                # item: (subscription_id, thread_id, subscribe_release, subscribe_test)
                statuses = []
                if item[2]: statuses.append("Release")
                if item[3]: statuses.append("Test")
                status_str = f" ({', '.join(statuses)})" if statuses else " (无订阅)"
                lines.append(f"**{uid}.** <#{item[1]}>{status_str}")
            else: # author
                # item: (follow_id, author_id)
                lines.append(f"**{uid}.** <@{item[1]}>")
        
        embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.blue())
        embed.set_footer(text=f"第 {self.current_page + 1} / {self.total_pages + 1} 页 | 使用下方菜单选择并操作")
        return embed

    def update_components(self):
        # ... (这部分逻辑与上一版几乎相同，只是更新label和description) ...
        start_index = self.current_page * self.bot.UPDATES_PER_PAGE
        page_items = self.all_items[start_index : start_index + self.bot.UPDATES_PER_PAGE]
        if not page_items:
            self.select_menu.disabled = True
            self.select_menu.options = [discord.SelectOption(label="本页无项目", value="disabled")]
            self.delete_button.disabled = True
        else:
            self.select_menu.disabled = False
            options = []
            for i, item in enumerate(page_items):
                uid = start_index + i + 1
                db_id = item[0]
                if self.item_type == 'thread':
                    statuses = []
                    if item[2]: statuses.append("Release")
                    if item[3]: statuses.append("Test")
                    status_str = f" ({', '.join(statuses)})" if statuses else ""
                    description_text=f"<#{item[1]}>{status_str}"
                else:
                    description_text=f"作者 <@{item[1]}>"
                options.append(discord.SelectOption(label=f"UID: {uid}", value=str(db_id), description=description_text))
            self.select_menu.options = options
            self.select_menu.max_values = len(options)
            self.delete_button.disabled = False
        is_first_page = self.current_page == 0; is_last_page = self.current_page >= self.total_pages
        self.first_page_button.disabled = is_first_page; self.prev_page_button.disabled = is_first_page
        self.next_page_button.disabled = is_last_page; self.last_page_button.disabled = is_last_page
    
    # --- 回调函数定义 ---
    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

    async def delete_callback(self, interaction: discord.Interaction):
        if not self.select_menu.values:
            await interaction.response.send_message("⚠️ 你没有选择任何项目。", ephemeral=True)
            return

        ids_to_process = [int(val) for val in self.select_menu.values]
        
        conn = None; cursor = None
        try:
            conn = self.bot.db_pool.get_connection()
            cursor = conn.cursor(buffered=True)

            if self.item_type == 'thread':
                sql = f"UPDATE thread_subscriptions SET subscribe_release = FALSE, subscribe_test = FALSE WHERE subscription_id IN ({', '.join(['%s'] * len(ids_to_process))}) AND user_id = %s"
            else: # author
                sql = f"DELETE FROM author_follows WHERE follow_id IN ({', '.join(['%s'] * len(ids_to_process))}) AND follower_id = %s"
            
            params = tuple(ids_to_process) + (self.user_id,)
            cursor.execute(sql, params)
            conn.commit()

            # 重新从数据库获取最新列表
            if self.item_type == 'thread':
                sql = """
                    SELECT subscription_id, thread_id, subscribe_release, subscribe_test
                    FROM thread_subscriptions
                    WHERE user_id = %s AND (subscribe_release = TRUE OR subscribe_test = TRUE)
                    ORDER BY subscription_id
                """
                cursor.execute(sql, (self.user_id,))
            else:
                cursor.execute("SELECT follow_id, author_id FROM author_follows WHERE follower_id = %s ORDER BY follow_id", (self.user_id,))
            
            self.set_items(cursor.fetchall())
        except mysql.connector.Error as err:
            await interaction.response.send_message(f"❌ 操作失败: {err}", ephemeral=True)
            return
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()
        
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    # ... (所有分页按钮的回调函数保持不变) ...
    async def first_page_callback(self, interaction: discord.Interaction): self.current_page = 0; self.update_components(); await interaction.response.edit_message(embed=self.create_embed(), view=self)
    async def prev_page_callback(self, interaction: discord.Interaction): self.current_page -= 1; self.update_components(); await interaction.response.edit_message(embed=self.create_embed(), view=self)
    async def next_page_callback(self, interaction: discord.Interaction): self.current_page += 1; self.update_components(); await interaction.response.edit_message(embed=self.create_embed(), view=self)
    async def last_page_callback(self, interaction: discord.Interaction): self.current_page = self.total_pages; self.update_components(); await interaction.response.edit_message(embed=self.create_embed(), view=self)

# --- DM:查看更新UI ---
class UpdatesPaginatorView(ui.View):
    def __init__(self, bot_instance, user_id, all_updates):
        super().__init__(timeout=300)
        self.bot = bot_instance
        self.user_id = user_id
        self.all_updates = all_updates
        self.current_page = 0
        self.total_pages = (len(all_updates) - 1) // self.bot.UPDATES_PER_PAGE

    async def create_embed_for_page(self):
        start_index = self.current_page * self.bot.UPDATES_PER_PAGE
        end_index = start_index + self.bot.UPDATES_PER_PAGE
        page_updates = self.all_updates[start_index:end_index]

        data_lines = []
        for update in page_updates:
            update_type, thread_id, author_id, last_update_url = update
            if update_type == 'thread_update':
                link = f"[▶更新]({last_update_url})" if last_update_url else f"首楼"
                data_lines.append(f"✅ <#{thread_id}> 更新了！\n└-> 来自您的订阅 ({link})")
            elif update_type == 'author_new_thread':
                data_lines.append(f"❤️ <#{thread_id}>\n└-> 来自您关注的作者 <@{author_id}>")
        
        data_text = "\n\n".join(data_lines)
        description = self.bot.VIEW_UPDATES_TEXT.replace("{{data}}", data_text)
        
        embed = discord.Embed(
            title=self.bot.VIEW_UPDATES_TITLE,
            description=description,
            color=discord.Color.teal()
        )
        embed.set_footer(text=f"第 {self.current_page + 1} / {self.total_pages + 1} 页")
        return embed

    async def update_interaction(self, interaction: discord.Interaction):
        """更新交互消息"""
        embed = await self.create_embed_for_page()
        for item in self.children: # 动态启用/禁用按钮
            item.disabled = self.total_pages == 0
        # 禁用上一页/首页按钮
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = self.current_page == 0
        # 禁用下一页/尾页按钮
        self.children[2].disabled = self.current_page == self.total_pages
        self.children[3].disabled = self.current_page == self.total_pages
        
        await interaction.response.edit_message(embed=embed, view=self)

    # --- 按钮定义 ---
    @ui.button(label="«", style=discord.ButtonStyle.secondary, row=1)
    async def first_page(self, interaction: discord.Interaction, button: ui.Button):
        self.current_page = 0
        await self.update_interaction(interaction)

    @ui.button(label="‹", style=discord.ButtonStyle.primary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: ui.Button):
        self.current_page -= 1
        await self.update_interaction(interaction)

    @ui.button(label="›", style=discord.ButtonStyle.primary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: ui.Button):
        self.current_page += 1
        await self.update_interaction(interaction)

    @ui.button(label="»", style=discord.ButtonStyle.secondary, row=1)
    async def last_page(self, interaction: discord.Interaction, button: ui.Button):
        self.current_page = self.total_pages
        await self.update_interaction(interaction)

# --- DM:用户控制面板UI ---
class UserPanel(ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    async def _show_management_panel(self, interaction: discord.Interaction, item_type: str):
        bot: "MyBot" = interaction.client
        user = interaction.user
        items = []
        conn = None; cursor = None
        try:
            conn = bot.db_pool.get_connection()
            cursor = conn.cursor()
            if item_type == 'thread':
                sql ="""
                    SELECT subscription_id, thread_id, subscribe_release, subscribe_test
                    FROM thread_subscriptions
                    WHERE user_id = %s AND (subscribe_release = TRUE OR subscribe_test = TRUE)
                    ORDER BY subscription_id
                """
                cursor.execute("SELECT subscription_id, thread_id, subscribe_release, subscribe_test FROM thread_subscriptions WHERE user_id = %s ORDER BY subscription_id", (user.id,))
            else: # author
                cursor.execute("SELECT follow_id, author_id FROM author_follows WHERE follower_id = %s ORDER BY follow_id", (user.id,))
            items = cursor.fetchall()
        except mysql.connector.Error as err:
            await interaction.response.send_message(f"❌ 数据获取失败: {err}", ephemeral=True)
            return
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

        if not items:
            title = bot.MANAGE_SUBS_TITLE if item_type == 'thread' else bot.MANAGE_AUTHORS_TITLE
            embed = discord.Embed(title=title, description="你还没有任何订阅/关注。", color=discord.Color.orange())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        view = ManagementPaginatorView(bot, user.id, items, item_type)
        embed = view.create_embed()
        if interaction.response.is_done():
             # 如果因为某些原因（比如耗时过长）已经响应过，就用 followup 发送新消息
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

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
        all_updates = []
        conn = None
        cursor = None

        try:
            conn = bot.db_pool.get_connection()
            cursor = conn.cursor()
            #先获取用户订阅的有新更新的帖子
            thread_updates_sql = """
                SELECT 'thread_update' as type, t.thread_id, t.author_id, t.last_update_url
                FROM thread_subscriptions ts
                JOIN managed_threads t ON ts.thread_id = t.thread_id
                WHERE ts.user_id = %s AND ts.has_new_update = TRUE
            """
            cursor.execute(thread_updates_sql, (user.id,))
            all_updates.extend(cursor.fetchall())

            #获取用户关注的作者发布的新帖子
            author_updates_sql = """
                SELECT 'author_new_thread' as type, t.thread_id, t.author_id, t.last_update_url
                FROM author_follows af
                JOIN managed_threads t ON af.author_id = t.author_id
                WHERE af.follower_id = %s AND af.has_new_update = TRUE
            """
            cursor.execute(author_updates_sql, (user.id,))
            all_updates.extend(cursor.fetchall())
            
            # 去重，防止一个帖子既是订阅的又是关注的作者发的
            all_updates = list(dict.fromkeys(all_updates))

        except mysql.connector.Error as err:
            await interaction.response.send_message(f"❌ 数据库查询失败：{err}", ephemeral=True)
            return
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

        # 如果没有任何更新
        if not all_updates:
            await interaction.response.send_message("🎉 你已经看完了所有最新动态！", ephemeral=True)
            return

        # 创建并发送分页视图
        paginator = UpdatesPaginatorView(bot, user.id, all_updates)
        initial_embed = await paginator.create_embed_for_page()
        # 禁用不必要的按钮
        for item in paginator.children:
            item.disabled = paginator.total_pages == 0
        paginator.children[0].disabled = True
        paginator.children[1].disabled = True
        
        await interaction.response.send_message(embed=initial_embed, view=paginator, ephemeral=True)
        
        try:
            conn = bot.db_pool.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE thread_subscriptions SET has_new_update = FALSE WHERE user_id = %s", (user.id,))
            cursor.execute("UPDATE author_follows SET has_new_update = FALSE WHERE follower_id = %s", (user.id,))
            conn.commit()
        except mysql.connector.Error as err:
            print(f"重置更新状态时出错: {err}")
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @ui.button(label="刷新", style=discord.ButtonStyle.success, emoji="🔄", row=1)
    async def refresh_panel(self, interaction: discord.Interaction, button: ui.Button):
        bot: "MyBot" = interaction.client
        user = interaction.user
        
        thread_update_count = 0
        author_update_count = 0
        conn = None
        cursor = None

        try:
            conn = bot.db_pool.get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT COUNT(*) FROM thread_subscriptions WHERE user_id = %s AND has_new_update = TRUE",
                (user.id,)
            )
            thread_update_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM author_follows WHERE follower_id = %s AND has_new_update = TRUE",
                (user.id,)
            )
            author_update_count = cursor.fetchone()[0]

        except mysql.connector.Error as err:
            # 如果刷新时出错，可以发送一个短暂的错误提示
            await interaction.response.send_message("❌ 刷新失败，无法连接到数据库。", ephemeral=True)
            return
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

        # --- 重新构建嵌入式消息 ---
        description_text = bot.DM_PANEL_TEXT.replace(
            "{{user}}", user.mention
        ).replace(
            "{{thread_update_number}}", str(thread_update_count)
        ).replace(
            "{{author_update_number}}", str(author_update_count)
        )

        refreshed_embed = discord.Embed(
            title=bot.DM_PANEL_TITLE,
            description=description_text,
            color=discord.Color.blurple()
        ).set_footer(text=f"将在12小时后消失... |At {get_utc8_now_str()}")
        
        # --- 使用 interaction.response.edit_message 更新原始面板 ---
        await interaction.response.edit_message(embed=refreshed_embed, view=self)

# --- 复用函数 ---
# 检查并创建用户
async def check_and_create_user(db_pool, user:discord.User):
    if not user:
        return
    conn = None;cursor = None
    try:
        conn = db_pool.get_connection()
        cursor = conn.cursor()
        #直接插入!
        cursor.execute("INSERT IGNORE INTO users (user_id) VALUES (%s)", (user.id,))
        conn.commit()
    except mysql.connector.Error as err:
        print(f"数据库错误于 check_and_create_user: {err}")
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

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

    conn = None
    cursor = None
    try:
        await check_and_create_user(bot.db_pool, thread_owner)

        conn = bot.db_pool.get_connection()
        cursor = conn.cursor()
        
        thread_id = thread_channel.id
        guild_id = interaction.guild.id
        
        cursor.execute(
            "INSERT INTO managed_threads (thread_id, guild_id, author_id) VALUES (%s, %s, %s)",
            (thread_id, guild_id, thread_owner.id) # 使用ID进行存储
        )
        conn.commit()

        embed = discord.Embed(title=bot.EMBED_TITLE, description=bot.EMBED_TEXT, color=discord.Color.blue())
        await interaction.followup.send(embed=embed, view=SubscriptionView())

    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_DUP_ENTRY:
            embed = discord.Embed(title=bot.EMBED_TITLE, description=f"ℹ️ 这个帖子已经开启了更新推流功能。\n\n{bot.EMBED_TEXT}", color=discord.Color.blue())
            await interaction.followup.send(embed=embed, view=SubscriptionView(), ephemeral=True)
        else:
            print(f"数据库错误于 /创建更新推流: {err}")
            await interaction.followup.send(f"❌ {bot.EMBED_ERROR} (数据库错误)", ephemeral=True)
    except Exception as e:
        print(f"未知错误于 /创建更新推流: {e}")
        await interaction.followup.send(f"❌ 发生了一个未知错误，请联系开发者。", ephemeral=True)
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()
 
@app_commands.command(name="控制面板",description="请在和Bot的私信中使用哦~")
@app_commands.dm_only()
async def manage_subscription_panel(interaction: discord.Interaction):
    bot: "MyBot" = interaction.client
    user = interaction.user
    
    # 确保用户存在于数据库中
    await check_and_create_user(bot.db_pool, user)

    thread_update_count = 0
    author_update_count = 0
    conn = None
    cursor = None

    # 从数据库获取更新数量
    try:
        conn = bot.db_pool.get_connection()
        cursor = conn.cursor()

        # 查询有多少个已订阅的帖子有新更新
        cursor.execute(
            "SELECT COUNT(*) FROM thread_subscriptions WHERE user_id = %s AND has_new_update = TRUE",
            (user.id,)
        )
        thread_update_count = cursor.fetchone()[0]

        # 查询有多少个已关注的作者有新动态
        cursor.execute(
            "SELECT COUNT(*) FROM author_follows WHERE follower_id = %s AND has_new_update = TRUE",
            (user.id,)
        )
        author_update_count = cursor.fetchone()[0]

    except mysql.connector.Error as err:
        print(f"数据库错误于 /管理订阅消息: {err}")
        await interaction.response.send_message("❌ 无法获取您的订阅状态，请稍后重试。", ephemeral=True)
        return
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

    # 格式化描述文本
    description_text = bot.DM_PANEL_TEXT.replace(
        "{{user}}", user.mention
    ).replace(
        "{{thread_update_number}}", str(thread_update_count)
    ).replace(
        "{{author_update_number}}", str(author_update_count)
    )

    # 创建嵌入式消息和视图
    embed = discord.Embed(
        title=bot.DM_PANEL_TITLE,
        description=description_text,
        color=discord.Color.blurple()
    ).set_footer(text=f"将在12小时后消失... |At {get_utc8_now_str()}")
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
    author = interaction.user
    thread = interaction.channel

    if not isinstance(thread, discord.Thread) or thread.parent_id not in bot.ALLOWED_CHANNELS:
        await interaction.response.send_message("❌ 此指令只能在指定的论坛频道的帖子中使用。", ephemeral=True)
        return

    conn = None
    cursor = None
    users_to_notify = set()
    thread_owner_id = None
    
    try:
        await check_and_create_user(bot.db_pool, author)
        conn = bot.db_pool.get_connection()
        cursor = conn.cursor(buffered=True)

        #验证权!
        cursor.execute("SELECT * FROM managed_threads WHERE thread_id = %s", (thread.id,))
        result = cursor.fetchone()
        if not result:
            await interaction.response.send_message("❌ 这个帖子没有开启更新推流功能，请先使用 /创建更新推流 指令。", ephemeral=True)
            return
        
        thread_owner_id = result[2] #managed_threads的[2]才是author_id
        if author.id != thread_owner_id:
            await interaction.response.send_message("❌ 只有帖子作者可以发送更新推流。", ephemeral=True)
            return

        # 先更新状态
        update_thread_sql = """
            UPDATE managed_threads
            SET last_update_url = %s, last_update_message = %s, last_update_at = CURRENT_TIMESTAMP, last_update_type = %s
            WHERE thread_id = %s
        """
        cursor.execute(update_thread_sql, (url, message, update_type.value, thread.id))

        #标记订阅的该帖子的用户对应的更新状态
        subscription_update_sql = f"""
            UPDATE thread_subscriptions 
            SET has_new_update = TRUE
            WHERE thread_id = %s AND subscribe_{update_type.value} = TRUE
        """
        cursor.execute(subscription_update_sql, (thread.id,))

        #标记关注了作者的用户的 has_new_update 状态
        author_follow_update_sql = "UPDATE author_follows SET has_new_update = TRUE WHERE author_id = %s"
        cursor.execute(author_follow_update_sql, (author.id,))
        
        #顺便查询需要通知的用户数组
        if update_type.value == "release": # 查询订阅发行版的用户
            cursor.execute("SELECT user_id FROM thread_subscriptions WHERE thread_id = %s AND subscribe_release = TRUE", (thread.id,))
        else: # 查询订阅测试版的用户
            cursor.execute("SELECT user_id FROM thread_subscriptions WHERE thread_id = %s AND subscribe_test = TRUE", (thread.id,))
        for row in cursor.fetchall():
            users_to_notify.add(row[0])

        cursor.execute("SELECT follower_id FROM author_follows WHERE author_id = %s", (thread_owner_id,)) # 查询关注作者的用户
        for row in cursor.fetchall():
            users_to_notify.add(row[0])
            
        #所有数据库操作成功，提交事务
        conn.commit()

    except mysql.connector.Error as err:
        # 如果任何一步数据库操作失败，发送错误消息并返回
        await interaction.response.send_message(f"❌ 数据库操作失败：{err}", ephemeral=True)
        return
    finally:
        # 无论成功或失败，最后都关闭
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()
    
    # --- 数据库操作已全部完成，开始发送响应和幽灵提及 ---

    # 处理完成：开始发送嵌入式消息
    status_embed = discord.Embed(
        title=bot.UPDATE_TITLE,
        description="正在收集必要的数据中...",
        color=discord.Color.blue()
    ).set_footer(text=f"运行状态：准备中...|{discord.utils.format_dt(datetime.datetime.now(), 'T')}")
    # 这里用 try-except 包裹，防止 interaction 过期
    try:
        await interaction.response.send_message(embed=status_embed)
    except discord.InteractionResponded:
        # 如果已经响应过（例如数据库操作时间过长），就直接修改
        await interaction.edit_original_response(embed=status_embed)
    response_message = await interaction.original_response()

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
                self.db_pool = pooling.MySQLConnectionPool(
                pool_name="bot_pool",
                pool_size=pool_size_int,
                host='db',  # Docker Compose中定义的MySQL服务名
                user=os.getenv('MYSQL_USER'),
                password=os.getenv('MYSQL_PASSWORD'),
                database=os.getenv('MYSQL_DATABASE'),
                pool_reset_session=True,
                connect_timeout=10
            )
                conn = self.db_pool.get_connection()
                print("数据库连接测试成功。")
                conn.close()
                print("数据库连接池创建成功。")
                break

            except mysql.connector.Error as err:
                print(f"数据库连接失败 (尝试 {i+1}/{max_retries}): {err}")
                if i + 1 == max_retries:
                    print("已达到最大重试次数，机器人启动失败。")
                    await self.close() # 彻底关闭机器人
                    return
                print(f"将在 {retry_delay} 秒后重试...")
                await asyncio.sleep(retry_delay)

        # --- 2. 自动创建表 (如果不存在的话) ---
        print("正在检查并创建数据库表...")
        conn = None
        cursor = None
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()

                # 表1: 用户信息与状态表
            cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                        last_checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                # 表2: 被管理的帖子表，包含最新更新的缓存
            cursor.execute("""
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
            cursor.execute("""
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
            cursor.execute("""
                    CREATE TABLE IF NOT EXISTS author_follows (
                        follow_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        follower_id BIGINT UNSIGNED NOT NULL,
                        author_id BIGINT UNSIGNED NOT NULL,
                        has_new_update BOOLEAN NOT NULL DEFAULT FALSE,
                        UNIQUE KEY unique_follow (follower_id, author_id),
                        FOREIGN KEY (follower_id) REFERENCES users(user_id) ON DELETE CASCADE,
                        FOREIGN KEY (author_id) REFERENCES users(user_id) ON DELETE CASCADE
                    );
                """)

            conn.commit()
            print("数据库表结构已确认。")

        except mysql.connector.Error as err:
            print(f"数据库建表失败: {err}")
        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()
                
        target_guild = discord.Object(id=self.TARGET_GUILD_ID)
        self.tree.add_command(CommandGroup_bot(name="bot", description="机器人指令"), guild=target_guild)
        self.tree.add_command(create_update_feed, guild=target_guild)
        self.tree.add_command(update_feed, guild=target_guild)
        self.tree.add_command(manage_subscription_panel)

        self.add_view(SubscriptionView())

        # --- 同步command ---
        try:
            print(f"正在尝试将指令同步到服务器 ID: {target_guild.id}...")
            synced_commands = await self.tree.sync(guild=target_guild) + await self.tree.sync(guild=None)
            print(f"成功同步了 {len(synced_commands)} 个指令。")
            for cmd in synced_commands:
                print(f"  - 指令名称: {cmd.name} (ID: {cmd.id})")
        except Exception as e:
            print(f"指令同步时发生严重错误: {e}")

    async def on_ready(self):
        print(f'机器人 {self.user} 已成功登录并准备就绪！') 
