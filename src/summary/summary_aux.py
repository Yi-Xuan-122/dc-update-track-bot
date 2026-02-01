from typing import Set
import re
import asyncio
import time
import datetime
from collections import deque
import math
import logging
import discord
from src.chat.gemini_format import gemini_format_callback
import copy
from discord import TextChannel
from dataclasses import field ,dataclass
from typing import Optional, Dict, Any, List, Callable
from src.config import TARGET_GUILD_ID, IMG_VIEW ,UTC_ZONE
import base64
import random
log = logging.getLogger(__name__)

@dataclass
class Summary_fetch_task:
    timestamp: float
    target_channel: int
    aggregated_messages: List[discord.Message] = field(default_factory=list, init=False) #主要用于链式任务结果的持久化
    fetch_total: int
    single_limit: int
    start_before_message_id: Optional[str] = None
    future: asyncio.Future = field(default_factory=asyncio.Future)
    is_blocked: bool = field(default=False, init=False)
    retry_count: int = field(default=0, init=False)
    def __post_init__(self):
        guild_id = int(TARGET_GUILD_ID)
        url = f"https://discord.com/channels/{guild_id}/{self.target_channel}"
        self.base64_id: str = base64.b64encode(f'{url}:{self.fetch_total}'.encode('utf-8'))

class queue_cache: #用来缓存的队列
    def __init__(self):
        self.items = []
        self._new_item_event = asyncio.Event()
    def add(self,item):
        self.items.append(item)
        self._new_item_event.set()
    async def wait(self):
        while not self.items:
            await self._new_item_event.wait()
            self._new_item_event.clear()
# --- summary message 缓存结束 ---

DEFAULT_AVG_FETCH_TIME_SECONDS = 0.5 
RateLimitCounter = Dict[int, Dict[str, Any]]
class RateLimitingScheduler:
    RATE_LIMIT_PER_CHANNEL = 50
    RATE_LIMIT_WINDOW_SECONDS = 60
    MAX_RETRIES = 3
    CACHE_EXPIRATION_SECONDS = 300
    
    def __init__(self,bot : discord.Client):
        self.main_queue: asyncio.Queue[Summary_fetch_task] = asyncio.Queue()
        
        self.channel_counts : RateLimitCounter = {}

        self.rate_limit_event = asyncio.Event()

        self.historical_fetch_times = deque(maxlen=50)

        self.queue_cache = queue_cache()

        self.lock = asyncio.Lock()

        self.bot = bot
        logging.debug("RateLimitingScheduler loaded")

    def _is_rate_limit(self, channel_id: int)-> bool:
        if channel_id not in self.channel_counts:
            return False
        
        counter = self.channel_counts[channel_id]
        if time.time() > counter['first_request_time'] + self.RATE_LIMIT_WINDOW_SECONDS:
            self.channel_counts.pop(channel_id)
            self.rate_limit_event.set()
            return False
        
        return counter['count'] >= self.RATE_LIMIT_PER_CHANNEL
    
    def _update_counter(self,channel_id: int):
        if channel_id not in self.channel_counts:
            self.channel_counts[channel_id] = {
                'count': 1,
                'first_request_time': time.time()
            }
        else:
            self.channel_counts[channel_id]['count']+=1

    def _calculate_retry_after(self, channel_id: int) -> float:
       # 计算限时后等待秒数
        counter = self.channel_counts.get(channel_id)
        if not counter:
            return 0.0
            
        reset_time = counter['first_request_time'] + self.RATE_LIMIT_WINDOW_SECONDS
        return max(0.0, reset_time - time.time())
    
    async def run(self):
        loop = asyncio.get_event_loop()
        while True:
            if self.main_queue.empty():
                task: Summary_fetch_task = await self.main_queue.get()

            channel_id = task.target_channel #这里是缓存判断的开始
            
            is_cache = False
            cache_task = await self.find_and_get_cache(task.base64_id)#尝试从cache中获取，此时缓存中的被取出
            if cache_task:
                is_cache = True
                task = cache_task

            if is_cache == False: #如果缓存命中则跳过，直接重新计算分片任务
                if self._is_rate_limit(channel_id): #判断是否需要等待
                    retry_after_s = self._calculate_retry_after(channel_id)
                
                    def re_queue_task():
                        task.is_blocked = False
                        self.main_queue.put_nowait(task)
                        self.rate_limit_event.set()

                    loop.call_later(retry_after_s,re_queue_task)
                    task.is_blocked = True
                    self.main_queue.task_done()
                    continue
                
                self._update_counter(channel_id)
                fetch_success = False
                message : List[discord.Message] = []
                try:
                    channel: Optional[TextChannel] = self.bot.get_channel(channel_id)     
                    if not channel:
                        raise ValueError(f"Channel ID {channel_id} not found")
                    while task.retry_count < self.MAX_RETRIES:
                        try:
                            fetch_start_time = time.monotonic()
                            message = [msg async for msg in channel.history(
                                limit=task.single_limit,
                                before=discord.Object(id=int(task.start_before_message_id)) if task.start_before_message_id else None
                            )]
                            fetch_success = True
                            task.retry_count = 0
                            break
                        except discord.HTTPException as e:
                            if e.status in (429,500,503):
                                task.retry_count += 1
                                wait_time = 2**task.retry_count
                                logging.warning(f"API error on channel {channel_id}. Status: {e.status}. Retrying ({task.retry_count}/{self.MAX_RETRIES}) in {wait_time}s...")
                                await asyncio.sleep(wait_time)
                            else:
                                raise e
                            
                    if not fetch_success:
                        logging.error(f"Failed to fetch for channel {channel_id} after retries. Caching task.")
                        task.future.set_exception(RuntimeError(f"Failed to fetch for channel {channel_id} after retries."))
                        task.future = asyncio.Future()
                        self.queue_cache.add(task)#进入缓存
                        self.main_queue.task_done()
                        continue

                except Exception as e:
                    logging.error(f"Cannot get message : {e}")
                    task.future.set_exception(e)
                    self.main_queue.task_done()
                    continue

                fetch_duration = time.monotonic() - fetch_start_time
                self.historical_fetch_times.append(fetch_duration)

                task.aggregated_messages.extend(message)
                is_channel_end = not message
                is_limit_reached = len(task.aggregated_messages) >= task.fetch_total

            is_channel_end = (not is_cache) and (not message)

            if is_channel_end or is_limit_reached:
                logging.debug(f"Success: Channel {channel_id}。Reason: {'Reached the end of the channel' if is_channel_end else 'Reached the quantity limit'}。")

                final_messages = task.aggregated_messages[:task.fetch_total]
                final_messages.reverse() #现在的message对象是从旧到新的
                task.future.set_result(final_messages)
            else:
                logging.debug(f"Fetched {len(message)} items, totaling {len(task.aggregated_messages)}/{task.fetch_total}. Continuing to the next slice...")
                remaining = task.fetch_total - len(task.aggregated_messages)
                next_limit = min(remaining,100)

                next_task = Summary_fetch_task(
                timestamp=time.time(),
                target_channel=task.target_channel,
                fetch_total=task.fetch_total, # 传递总目标
                single_limit=next_limit, # 下一个切片的 limit
                start_before_message_id=message[-1].id, # 从最旧的消息开始
                future=task.future # 传递同一个 future
            )
                next_task.aggregated_messages = task.aggregated_messages
                await self.main_queue.put(next_task)
                self.rate_limit_event.set()

    async def estimate_completion_time(self, task_to_estimate: Summary_fetch_task) -> float:
        current_time = time.time()
        if not self.historical_fetch_times:
            avg_fetch_time_per_slice = DEFAULT_AVG_FETCH_TIME_SECONDS
        else:
            avg_fetch_time_per_slice = sum(self.historical_fetch_times) / len(self.historical_fetch_times)

        # 复制当前计数器状态
        simulated_counts = {k: v.copy() for k, v in self.channel_counts.items()}
        
        # 建立一个包含所有待处理切片的列表（队列中的任务+新任务的切片）
        tasks_to_simulate_slices = []
        # 从队列中获取任务 (每个任务代表一个待处理的切片)
        tasks_to_simulate_slices.extend(list(self.main_queue._queue))
        num_slices_for_new_task = math.ceil(task_to_estimate.fetch_total / 100)
        for _ in range(num_slices_for_new_task):
            tasks_to_simulate_slices.append(task_to_estimate)

        simulated_current_time = current_time
        for task_slice in tasks_to_simulate_slices:
            channel_id = task_slice.target_channel
            counter = simulated_counts.get(channel_id)

            if counter and simulated_current_time > counter['first_request_time'] + self.RATE_LIMIT_WINDOW_SECONDS:
                simulated_counts.pop(channel_id)
                counter = None

            if counter and counter['count'] >= self.RATE_LIMIT_PER_CHANNEL:
                reset_time = counter['first_request_time'] + self.RATE_LIMIT_WINDOW_SECONDS
                simulated_current_time = max(simulated_current_time, reset_time)
                simulated_counts.pop(channel_id)
                counter = None

            simulated_current_time += avg_fetch_time_per_slice

            # 更新模拟计数器
            if channel_id not in simulated_counts:
                simulated_counts[channel_id] = {'count': 1, 'first_request_time': simulated_current_time}
            else:
                simulated_counts[channel_id]['count'] += 1

        total_estimated_time = simulated_current_time - current_time
        return max(0.0, total_estimated_time)
    
    async def find_and_get_cache(self,target_base64):
        async with self.lock:
            for item in self.queue_cache.items: #队列中的item是Summary_fetch_task类
                if item.base64_id == target_base64:
                    if item:
                        try:
                            self.queue_cache.items.remove(item)
                            return item
                        except Exception as e:
                            logging.error(f"find_and_get_cache Error:{e}")
            return None

    async def queue_cache_event(self):
        logging.debug(f"queue_cacha loaded")
        minutes_to_keep = 5 #5min
        expiry_duration = datetime.timedelta(minutes=minutes_to_keep)
        while True:
            head_item = None
            sleep_duration = 1

            async with self.lock:
                if self.queue_cache.items:
                    head_item = self.queue_cache.items[0]
                    expiry_time = head_item.timestamp + expiry_duration
                    now = time.time()
                    sleep_duration = max(0, (expiry_time - now).total_seconds())
            
            await asyncio.sleep(sleep_duration)
            if head_item:
                async with self.lock:
                    if self.queue_cache.items and self.queue_cache.items[0] == head_item:
                        now = time.time()
                        if (head_item.timestamp + expiry_duration) <= now:
                            self.queue_cache.items.pop(0)


def parse_user_ids(members:str)-> Set[int]:
    #我们期望获得的str为:<@id1><@id2>然后把它们解析为字典
    if not members:
        return set()
    ids_str = re.findall(r'<@!?(\d+)>',members)
    user_ids = {int(uid) for uid in ids_str}
    return user_ids

async def openai_format(text: str,img_urls: Optional[List[str]] = None, main_prompt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
#期望获得一个字符串、一个可选的imgurl List对象以及一个可选的main_prompt来转化为一个openai格式的JSON对象
    if main_prompt is None:
        openai_prompt = {"messages": []}
    else:
        openai_prompt = copy.deepcopy(main_prompt)

    if "messages" not in openai_prompt or not isinstance(openai_prompt["messages"], list):
        openai_prompt["messages"] = []

    content_parts = []
    if text:
        content_parts.append({
            "type": "text",
            "text": text
        })

    if img_urls and IMG_VIEW:
        for url in img_urls:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": url}
            })
    if not content_parts:
        return openai_prompt
    #if IMG_VIEW==False ，content 只能是纯文本
    if not IMG_VIEW:
        final_content = text
    elif len(content_parts) == 1 and content_parts[0]["type"] == "text":
        final_content = text
    else:
        final_content = content_parts

    if (openai_prompt["messages"] and 
        openai_prompt["messages"][-1].get("role") == "user"):
        
        last_message = openai_prompt["messages"][-1]
        current_content = last_message.get("content", "")

        if isinstance(current_content, str):
            current_content = [{"type": "text", "text": current_content}]

        if isinstance(final_content, list):
            current_content.extend(final_content)
        else:
            if current_content and current_content[-1].get("type") == "text":
                current_content[-1]["text"] += f"\n{final_content}"
            else:
                current_content.append({"type": "text", "text": final_content})
        
        last_message["content"] = current_content

    else:
        new_message = {
            "role": "user",
            "content": final_content
        }
        openai_prompt["messages"].append(new_message)

    return openai_prompt    

def convert_to_local_timezone(utc_dt: datetime.datetime) -> datetime.datetime:
    offset_hours = UTC_ZONE
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=datetime.timezone.utc)
    target_timezone = datetime.timezone(datetime.timedelta(hours=offset_hours))
    local_dt = utc_dt.astimezone(target_timezone)
    return local_dt

post_processing = Callable[[str, Optional[List[str]], Optional[Dict[str, Any]]], Any]

async def parse_message_history_to_prompt(message:List[discord.Message],post_processing_callback:post_processing,members_ids:Set[int] = None):
    #期望获得一个discord message对象，把它们一层一层转化为LLM总结的prompt
    #我应该是接受一个discord.Message对象，还有一个回调函数作为内部处理的函数以转化为openai对象，最后返回一个prompt的JSON对象
    #我们的目标是:
    #1.根据时间戳判断，消息间隔大于5min则插入一个 提示时间过去多少 的prompt
    #2.reply逻辑，解析到reference属性，则留下一个<reply:[自增id]>，然后在一个字典中记录，从以前的消息中遍历id，若没有则在API处获取
    #3.img逻辑，若IMG_VIEW!=False，解析到attachments属性，则传递img的url参数
    previous_time:datetime = None
    current_time:datetime = None
    main_prompt:Dict[str, Any] = None
    message_id_hash:Dict[int,str] = {} #建立一个message.id -> reply_str的hash表{"message_id":"{str}"}
    message_index = -1
    seen_members = {}
    name_usage_count = {}
    Seed: int = random.randint(100000,999999)
    for items in message:
        if members_ids and (items.author.id not in members_ids):
            continue
        
        minutes_passed = None
        pass_time_str = ""
        start_str = None 

        if not current_time:
            #说明是第一条消息，尚未初始化
            current_time = items.created_at
            start_str:str = f"[本次全部的System Seed为: {Seed}，请注意核对]\n\n[System Seed:{Seed}]: ---聊天记录开始---\n\n"
            first_start_time_str = f" \n[时间:{convert_to_local_timezone(current_time)}]\n"
            pass
        else:
            previous_time = current_time
            current_time = items.created_at

        #开始字符串解析，准备
        author_id = items.author.id
        if author_id not in seen_members:
            raw_name = items.author.display_name
            if raw_name in name_usage_count:
                name_usage_count[raw_name] += 1
                # 如果重名，格式化为 "名字 (2)"
                unique_name = f"{raw_name} ({name_usage_count[raw_name]})"
            else:
                name_usage_count[raw_name] = 1
                unique_name = raw_name
            seen_members[author_id] = unique_name
        nickname = seen_members[author_id]
        human_text = items.content.replace("】", " ] ")
        img_urls :List[str] = [attachment.url for attachment in items.attachments]
        # 检查是否有 forwarding snapshots
        if hasattr(items, 'snapshots') and items.snapshots:
            for snapshot in items.snapshots:
                # snapshot 包含了被转发消息的快照
                fwd_content = snapshot.content
                if fwd_content:
                    human_text += f"\n ↳ [Forwarded]: \"{fwd_content.replace('】', ' ] ')}\""
                    
                    # 提取转发消息中的附件
                if IMG_VIEW and snapshot.attachments:
                    img_urls.extend([att.url for att in snapshot.attachments])

        current_text =f"【<display_name:\"{nickname}\"> say : \"{human_text}\" 】\n"

        if items.reference and items.reference.message_id: #说明回复了某一天消息
            reply_id = items.reference.message_id
            reply_str = message_id_hash.get(reply_id)
            if reply_str != None:
                pass
            else:
                reply_str = f"[<@reply_to:{items.reference.message_id}>]\n"#需要使用API获取
            current_text = reply_str + current_text

        if previous_time:
            time_diff = current_time - previous_time
            if time_diff.total_seconds() > 300:
                minutes_passed = int(time_diff.total_seconds() // 60)
            else:
                minutes_passed = None
            
            if minutes_passed:
                pass_time_str = f"\n[System Seed:{Seed}]: ---(过了{minutes_passed}分钟后，当前时间:{convert_to_local_timezone(current_time)})\n"
        
        #最后组合字符串：
        final_str = current_text
        if start_str:
            final_str = start_str + first_start_time_str + final_str
        if minutes_passed:
            final_str = pass_time_str + final_str
        main_prompt = await post_processing_callback(final_str,img_urls,main_prompt)
        #将此次的存入hash表中：
        if len(current_text) > 30:
            short_text = current_text[:30]+"......"
        else:
            short_text = current_text
        message_id_hash[items.id] = f"[reply to -> '{short_text}' from {nickname}(At {convert_to_local_timezone(current_time)})] "

    #结束for循环，说明聊天记录已全部封装入一个user input
    member_list_parts = [f"\n\n[System Seed:{Seed}]: ---聊天记录结束---", "<members_list>"]
    for m_id, m_name in seen_members.items():
        member_list_parts.append(f"<display_name=\"{m_name}\">:\"{m_id}\"")
    member_list_parts.append("</members_list>")
    end_str = "\n".join(member_list_parts)
    main_prompt = await post_processing_callback(end_str, None, main_prompt)
    
    return main_prompt