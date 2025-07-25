# -*- coding: utf-8 -*-

import telebot
from telebot.types import InputMediaPhoto, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton
import requests
import os
import re
import logging
import configparser
import sys
import asyncio
from asyncio import new_event_loop, set_event_loop, Semaphore, create_task, gather, wait_for, TimeoutError as AsyncTimeoutError
from datetime import datetime
import json
import weakref
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Set
import time

# 导入XHS类
from source.application.app import XHS

# --- 日志记录设置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- 读取配置 ---
try:
    config = configparser.ConfigParser()
    config.read('config.ini')

    # 从配置文件读取信息
    BOT_TOKEN = config.get('telegram', 'bot_token', fallback=None)
    PROXY_URL = config.get('proxy', 'url', fallback=None)
    # 不再需要PARSE_API_URL
    DOWNLOAD_DIR = config.get('app', 'download_dir', fallback='downloads')

    # 关键配置校验
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN_HERE':
        logger.critical("错误: Telegram Bot Token 未在 config.ini 文件中正确配置。")
        sys.exit("请先配置好 config.ini 文件中的 bot_token。")

except (configparser.NoSectionError, configparser.NoOptionError) as e:
    logger.critical(f"配置文件 config.ini 读取错误: {e}")
    sys.exit("请确保 config.ini 文件存在且格式正确。")
except Exception as e:
    logger.critical(f"加载配置时发生未知错误: {e}")
    sys.exit("加载配置失败。")

# --- 初始化 ---
# 设置代理
if PROXY_URL:
    telebot.apihelper.proxy = {'https': PROXY_URL}
    logger.info(f"已启用代理: {PROXY_URL}")

# 确保下载目录存在
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# 初始化TeleBot
bot = telebot.TeleBot(BOT_TOKEN)

# === XHS实例池管理 ===
class XHSInstancePool:
    """XHS实例池管理器"""

    def __init__(self, max_instances=3):
        self.max_instances = max_instances
        self.available_instances = []
        self.busy_instances = set()
        self.semaphore = Semaphore(max_instances)
        self._lock = asyncio.Lock()

    async def get_instance(self, user_preferences=None):
        """获取可用的XHS实例"""
        await self.semaphore.acquire()

        async with self._lock:
            if self.available_instances:
                instance = self.available_instances.pop()
                self.busy_instances.add(instance)
                return instance

            # 创建新实例
            instance = await self._create_instance(user_preferences)
            self.busy_instances.add(instance)
            return instance

    async def return_instance(self, instance):
        """归还实例到池中"""
        async with self._lock:
            if instance in self.busy_instances:
                self.busy_instances.remove(instance)
                self.available_instances.append(instance)
        self.semaphore.release()

    async def _create_instance(self, user_preferences=None):
        """创建新的XHS实例"""
        # 使用用户偏好或默认设置
        image_format = "WEBP"
        if user_preferences and "image_format" in user_preferences:
            image_format = user_preferences["image_format"]

        instance = XHS(
            work_path=DOWNLOAD_DIR,
            folder_name="",
            record_data=False,
            download_record=False,
            folder_mode=False,
            _print=True,
            image_format=image_format,
        )
        await instance.__aenter__()
        return instance

    async def cleanup(self):
        """清理所有实例"""
        async with self._lock:
            all_instances = list(self.available_instances) + list(self.busy_instances)
            for instance in all_instances:
                try:
                    await instance.__aexit__(None, None, None)
                except Exception as e:
                    logger.error(f"清理XHS实例时出错: {e}")
            self.available_instances.clear()
            self.busy_instances.clear()

# 全局XHS实例池
xhs_pool = XHSInstancePool()

@asynccontextmanager
async def get_xhs_instance(user_preferences=None):
    """上下文管理器：安全获取和归还XHS实例"""
    instance = None
    try:
        instance = await xhs_pool.get_instance(user_preferences)
        yield instance
    finally:
        if instance:
            await xhs_pool.return_instance(instance)

def run_async(coro):
    """在同步环境中运行异步函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = new_event_loop()
        set_event_loop(loop)
    
    return loop.run_until_complete(coro)

logger.info("Telegram Bot starting...")


# --- 功能函数 ---
def is_valid_url(url):
    """使用正则表达式简单验证URL格式"""
    if not isinstance(url, str):
        return False
    
    regex = re.compile(
        r'^(?:http|ftp)s?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, url) is not None

def extract_urls_from_text(text):
    """从文本中提取所有URL"""
    url_pattern = r'https?://[^\s\u4e00-\u9fa5]+'
    urls = re.findall(url_pattern, text)
    return [url for url in urls if is_valid_url(url)]

def format_work_info(data):
    """格式化作品信息为用户友好的文本"""
    info_lines = []
    
    title = data.get('作品标题', '未知')
    if title and title != '未知':
        info_lines.append(f"📝标题: {title}")
    
    description = data.get('作品描述', '')
    if description:
        # 移除方括号表情
        description = re.sub(r'\[.*?]', '', description).strip()
        # 扩大描述长度限制
        desc = description[:150] + "..." if len(description) > 150 else description
        if desc:
            info_lines.append(f"📄描述: {desc}")
    
    publish_time = data.get('发布时间', '未知')
    if publish_time and publish_time != '未知':
        formatted_time = format_publish_time(publish_time)
        info_lines.append(f"⏰时间: {formatted_time}")

    author = data.get('作者昵称', '未知')
    if author and author != '未知':
        info_lines.append(f"🦊作者: {author}")
    
    return "\n".join(info_lines)

def format_publish_time(time_str):
    """格式化发布时间"""
    try:
        # 处理下划线分隔的格式: 2025-07-08_06:00:48
        if '_' in time_str:
            time_str = time_str.replace('_', ' ')
        
        # 尝试解析常见的时间格式
        if '-' in time_str and ':' in time_str:
            # 格式如: 2024-01-15 14:30:25
            dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
            return dt.strftime('%Y年%m月%d日 %H:%M')
        elif '年' in time_str and '月' in time_str:
            # 已经是中文格式，直接返回
            return time_str
        else:
            # 其他格式，直接返回原始字符串
            return time_str
    except:
        # 解析失败，返回原始字符串
        return time_str

# === 用户数据管理 ===
class UserDataManager:
    """用户数据管理器"""

    def __init__(self):
        self.pending_urls: Dict[int, List[str]] = {}
        self.user_preferences: Dict[int, Dict] = {}
        self.active_tasks: Dict[int, Set] = {}  # 用户活跃任务
        self.user_settings_file = "user_settings.json"
        self._load_user_settings()

    def _load_user_settings(self):
        """加载用户设置"""
        try:
            if os.path.exists(self.user_settings_file):
                with open(self.user_settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 转换字符串键为整数键
                    self.user_preferences = {int(k): v for k, v in data.items()}
        except Exception as e:
            logger.error(f"加载用户设置失败: {e}")
            self.user_preferences = {}

    def _save_user_settings(self):
        """保存用户设置"""
        try:
            # 转换整数键为字符串键以便JSON序列化
            data = {str(k): v for k, v in self.user_preferences.items()}
            with open(self.user_settings_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存用户设置失败: {e}")

    def get_user_preferences(self, user_id: int) -> Dict:
        """获取用户偏好设置"""
        return self.user_preferences.get(user_id, {
            "image_format": "WEBP",
            "max_concurrent": 3,
            "timeout": 60
        })

    def set_user_preference(self, user_id: int, key: str, value):
        """设置用户偏好"""
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {}
        self.user_preferences[user_id][key] = value
        self._save_user_settings()

    def add_pending_urls(self, user_id: int, urls: List[str]):
        """添加待处理URL"""
        self.pending_urls[user_id] = urls

    def get_pending_urls(self, user_id: int) -> List[str]:
        """获取待处理URL"""
        return self.pending_urls.get(user_id, [])

    def remove_pending_urls(self, user_id: int):
        """移除待处理URL"""
        self.pending_urls.pop(user_id, None)

    def add_active_task(self, user_id: int, task_info: str):
        """添加活跃任务信息"""
        if user_id not in self.active_tasks:
            self.active_tasks[user_id] = set()
        self.active_tasks[user_id].add(task_info)

    def remove_active_task(self, user_id: int, task_info: str):
        """移除活跃任务信息"""
        if user_id in self.active_tasks:
            self.active_tasks[user_id].discard(task_info)

    def cancel_user_tasks(self, user_id: int):
        """清除用户任务记录"""
        if user_id in self.active_tasks:
            self.active_tasks[user_id].clear()
        # 同时清除待处理的URL
        self.remove_pending_urls(user_id)

# 全局用户数据管理器
user_manager = UserDataManager()

# === 媒体发送重试机制 ===
async def validate_media_url(url, timeout=10):
    """验证媒体URL是否可访问"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.head(url)
            return response.status_code == 200
    except Exception:
        return False

async def filter_valid_media(media_group, max_concurrent=3):
    """过滤出有效的媒体"""
    import asyncio

    async def check_media(media_item):
        """检查单个媒体项"""
        try:
            url = media_item.media
            is_valid = await validate_media_url(url)
            return (media_item, is_valid)
        except Exception:
            return (media_item, False)

    # 并发检查媒体有效性
    semaphore = asyncio.Semaphore(max_concurrent)

    async def check_with_semaphore(media_item):
        async with semaphore:
            return await check_media(media_item)

    # 执行并发检查
    results = await asyncio.gather(
        *[check_with_semaphore(media) for media in media_group],
        return_exceptions=True
    )

    # 分离有效和无效的媒体
    valid_media = []
    invalid_media = []

    for i, result in enumerate(results):
        if isinstance(result, tuple):
            media_item, is_valid = result
            if is_valid:
                valid_media.append(media_item)
            else:
                invalid_media.append((i, media_item))
        else:
            # 检查过程中出现异常，标记为无效
            invalid_media.append((i, media_group[i]))

    return valid_media, invalid_media

async def send_media_with_retry_option(chat_id, media_group, work_info, original_message, original_url):
    """发送媒体组，失败时提供重试选项"""

    # 首先预验证媒体（可选，用于快速检测）
    logger.info(f"开始发送 {len(media_group)} 个媒体文件")

    total_chunks = (len(media_group) + 9) // 10
    failed_chunks = []
    successful_chunks = 0

    for i in range(0, len(media_group), 10):
        chunk = media_group[i:i + 10]
        current_chunk = (i // 10) + 1

        try:
            # 为第一个媒体项目添加caption
            if chunk:
                caption_parts = []

                # 添加作品信息
                if work_info:
                    caption_parts.append(work_info)

                # 如果需要分片，添加分片信息
                if total_chunks > 1:
                    caption_parts.append(f"📦 分片: [{current_chunk}/{total_chunks}]")

                chunk[0].caption = "\n\n".join(caption_parts)

            # 发送媒体组
            send_kwargs = {
                'chat_id': chat_id,
                'media': chunk,
                'timeout': 180
            }

            # 如果有原始消息，则回复该消息
            if original_message:
                send_kwargs['reply_to_message_id'] = original_message.message_id

            bot.send_media_group(**send_kwargs)
            successful_chunks += 1
            logger.info(f"成功发送分片 {current_chunk}/{total_chunks}")

        except Exception as e:
            logger.error(f"发送媒体组分片 {current_chunk} 失败: {e}")
            failed_chunks.append((chunk, current_chunk, str(e)))

    # 如果有失败的分片，提供选项
    if failed_chunks:
        logger.warning(f"有 {len(failed_chunks)} 个分片发送失败，提供重试选项")
        await handle_media_send_failure(
            chat_id, failed_chunks, successful_chunks, total_chunks,
            work_info, original_message, original_url, media_group
        )
        return False

    logger.info(f"所有 {total_chunks} 个分片发送成功")
    return True

async def handle_media_send_failure(chat_id, failed_chunks, successful_chunks, total_chunks,
                                   work_info, original_message, original_url, original_media_group):
    """处理媒体发送失败，提供用户选项"""

    # 分析失败原因
    failure_reasons = [chunk[2] for chunk in failed_chunks]
    is_media_error = any("WEBPAGE_MEDIA_EMPTY" in reason or "wrong type" in reason for reason in failure_reasons)

    if is_media_error:
        failure_msg = "🚫 呜呜~ 部分媒体内容小猫咪抓不到了（可能已被删除或链接失效了喵）"
    else:
        failure_msg = "⚠️ 呜呜~ 部分媒体发送失败了喵"

    # 构建状态消息
    status_parts = [
        failure_msg,
        f"📊 状态: {successful_chunks}/{total_chunks} 个分片发送成功",
        f"❌ 失败: {len(failed_chunks)} 个分片"
    ]

    if successful_chunks > 0:
        status_parts.append("✅ 已成功发送的内容会保持不变的喵~")

    status_text = "\n".join(status_parts)

    # 创建重试选项按钮
    markup = InlineKeyboardMarkup()

    # 生成唯一的回调数据
    callback_prefix = f"retry_{hash(original_url) % 10000}"

    # 存储重试数据
    retry_data = {
        'failed_chunks': failed_chunks,
        'work_info': work_info,
        'original_message': original_message,
        'original_url': original_url,
        'chat_id': chat_id
    }

    # 简单的内存存储（生产环境建议使用数据库）
    if not hasattr(user_manager, 'retry_data'):
        user_manager.retry_data = {}
    user_manager.retry_data[callback_prefix] = retry_data

    markup.row(
        InlineKeyboardButton("🔄 重试失败的媒体喵", callback_data=f"{callback_prefix}_retry"),
        InlineKeyboardButton("✅ 发送可用媒体喵", callback_data=f"{callback_prefix}_partial")
    )
    markup.row(
        InlineKeyboardButton("❌ 算了喵", callback_data=f"{callback_prefix}_cancel")
    )

    # 发送选项消息
    try:
        if original_message:
            bot.send_message(
                chat_id,
                status_text + "\n\n主人想要怎么处理呢？🐱",
                reply_markup=markup,
                reply_to_message_id=original_message.message_id
            )
        else:
            bot.send_message(
                chat_id,
                status_text + "\n\n主人想要怎么处理呢？🐱",
                reply_markup=markup
            )
    except Exception as e:
        logger.error(f"发送重试选项失败: {e}")

async def send_available_media_only(chat_id, original_media_group, failed_chunks, work_info, original_message):
    """只发送可用的媒体，跳过失败的"""

    # 获取失败的媒体索引
    failed_media_indices = set()
    for chunk, chunk_num, _ in failed_chunks:
        start_idx = (chunk_num - 1) * 10
        for i, media in enumerate(chunk):
            failed_media_indices.add(start_idx + i)

    # 创建只包含可用媒体的新组
    available_media = []
    for i, media in enumerate(original_media_group):
        if i not in failed_media_indices:
            available_media.append(media)

    if not available_media:
        return False

    # 发送可用媒体
    try:
        # 为第一个媒体添加说明
        if available_media and work_info:
            caption_parts = [work_info, f"📋 小猫咪已过滤无效媒体，共 {len(available_media)} 个可用文件喵~ ✨"]
            available_media[0].caption = "\n\n".join(caption_parts)

        # 分片发送
        for i in range(0, len(available_media), 10):
            chunk = available_media[i:i + 10]

            send_kwargs = {
                'chat_id': chat_id,
                'media': chunk,
                'timeout': 180
            }

            if original_message:
                send_kwargs['reply_to_message_id'] = original_message.message_id

            bot.send_media_group(**send_kwargs)

        return True

    except Exception as e:
        logger.error(f"发送可用媒体失败: {e}")
        return False

# --- Bot 消息处理器 ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """处理 /start 和 /help 命令"""
    welcome_text = """
🐱✨ 喵呜~ 欢迎来到小红书下载助手的世界喵！

主人只要把小红书链接发给我，我就会像小猫咪一样敏捷地帮你抓取里面的图片和视频哦~
快来试试吧，我已经迫不及待想为主人服务了呢，喵呜~ ✨

💡 使用小贴士喵：
• 直接发送小红书链接就可以啦~
• 支持一次处理多个链接呢，很厉害吧喵！
• 图片视频统统都能下载，我可是全能小猫咪喵~

🔧 设置命令喵：
• /settings - 查看当前设置喵
• /set_format <格式> - 设置图片格式 (WEBP/PNG/JPEG)
• /cancel - 取消当前所有任务喵
"""
    bot.reply_to(message, welcome_text)

@bot.message_handler(commands=['settings'])
def show_settings(message):
    """显示用户设置"""
    user_id = message.from_user.id
    preferences = user_manager.get_user_preferences(user_id)

    settings_text = f"""
🔧✨ 主人的当前设置喵：

📸 图片格式: {preferences.get('image_format', 'WEBP')} 喵
⚡ 最大并发: {preferences.get('max_concurrent', 3)} 个任务
⏱️ 超时时间: {preferences.get('timeout', 60)}秒

使用 /set_format <格式> 来修改图片格式喵~
支持的格式: WEBP, PNG, JPEG, HEIC, AVIF
我会按照主人的喜好来处理图片的喵！✨
"""
    bot.reply_to(message, settings_text)

@bot.message_handler(commands=['set_format'])
def set_image_format(message):
    """设置图片格式"""
    user_id = message.from_user.id
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        bot.reply_to(message, "喵~ 主人需要指定图片格式哦，例如: /set_format WEBP\n支持的格式: WEBP, PNG, JPEG, HEIC, AVIF 喵！")
        return

    format_name = args[0].upper()
    valid_formats = ["WEBP", "PNG", "JPEG", "HEIC", "AVIF", "AUTO"]

    if format_name not in valid_formats:
        bot.reply_to(message, f"喵？{format_name} 这个格式我还不会呢~\n支持的格式: {', '.join(valid_formats)} 喵！")
        return

    user_manager.set_user_preference(user_id, "image_format", format_name)
    bot.reply_to(message, f"✅ 好的喵~ 图片格式已经设置为 {format_name} 啦！我会按照主人的喜好来处理图片的喵~ ✨")

@bot.message_handler(commands=['cancel'])
def cancel_tasks(message):
    """取消用户所有任务"""
    user_id = message.from_user.id
    user_manager.cancel_user_tasks(user_id)
    bot.reply_to(message, "✅ 好的喵~ 已经帮主人清除所有待处理任务啦！现在可以重新开始了呢~ 🐾")


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """处理所有文本消息，并从中提取URL"""
    user_text = message.text
    user_id = message.from_user.id
    
    # 提取所有URL
    extracted_urls = extract_urls_from_text(user_text)
    
    if not extracted_urls:
        bot.reply_to(message, "喵~ 没有发现小红书链接呢，请发送包含小红书链接的消息给我吧~ 🐾")
        return
    
    # 如果只有一个URL且文本就是这个URL，直接处理
    if len(extracted_urls) == 1 and user_text.strip() == extracted_urls[0]:
        process_single_url(message, extracted_urls[0])
        return
    
    # 多个URL或包含其他文字，需要确认
    user_manager.add_pending_urls(user_id, extracted_urls)

    urls_text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(extracted_urls)])
    confirm_text = f"喵呜~ 我的小爪子发现了 {len(extracted_urls)} 个链接呢：\n\n{urls_text}\n\n要让我帮主人把这些都抓取下来吗？我已经准备好了哦~ 🐱✨"

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ 好的喵~ 开始吧！", callback_data=f"confirm_{user_id}"),
        InlineKeyboardButton("❌ 不用了喵", callback_data=f"cancel_{user_id}")
    )

    bot.reply_to(message, confirm_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.endswith(('_retry', '_partial', '_cancel')))
def handle_retry_options(call):
    """处理媒体发送重试选项"""
    callback_data = call.data
    callback_prefix = callback_data.rsplit('_', 1)[0]
    action = callback_data.rsplit('_', 1)[1]

    # 获取重试数据
    if not hasattr(user_manager, 'retry_data') or callback_prefix not in user_manager.retry_data:
        bot.answer_callback_query(call.id, "❌ 喵？重试数据已过期了，请重新发送链接给我吧~")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        return

    retry_data = user_manager.retry_data[callback_prefix]
    chat_id = retry_data['chat_id']

    # 验证用户权限
    if call.message.chat.id != chat_id:
        bot.answer_callback_query(call.id, "❌ 喵？这不是主人的操作呢~ 我只听主人的话哦~")
        return

    try:
        if action == "cancel":
            # 取消操作
            try:
                bot.edit_message_text(
                    "❌ 好的喵~ 已取消媒体发送，有需要随时叫我哦~ 🐾",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )
            except Exception as edit_error:
                logger.warning(f"编辑取消消息失败: {edit_error}")
                try:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                except Exception:
                    pass
            bot.answer_callback_query(call.id, "已取消喵~")

        elif action == "retry":
            # 重试失败的媒体
            try:
                bot.edit_message_text(
                    "🔄 好的喵~ 小猫咪正在重试发送失败的媒体... 这次一定要成功喵！✨",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )
            except Exception as edit_error:
                logger.warning(f"编辑重试消息失败: {edit_error}")
                try:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                except Exception:
                    pass
            bot.answer_callback_query(call.id, "开始重试喵~")

            # 异步重试
            run_async(retry_failed_media(retry_data, call.message.chat.id))

        elif action == "partial":
            # 发送可用媒体
            try:
                bot.edit_message_text(
                    "✅ 好的喵~ 小猫咪正在发送可用的媒体内容... 马上就好~ ✨",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )
            except Exception as edit_error:
                # 如果编辑失败（内容相同），直接删除消息
                logger.warning(f"编辑消息失败: {edit_error}")
                try:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                except Exception:
                    pass

            bot.answer_callback_query(call.id, "发送可用媒体喵~")

            # 异步发送可用媒体
            run_async(send_partial_media(retry_data, call.message.chat.id))

    except Exception as e:
        logger.error(f"处理重试选项失败: {e}")
        bot.answer_callback_query(call.id, "❌ 呜呜~ 操作失败了喵... 😿")
    finally:
        # 清理重试数据
        if hasattr(user_manager, 'retry_data') and callback_prefix in user_manager.retry_data:
            del user_manager.retry_data[callback_prefix]

async def retry_failed_media(retry_data, chat_id):
    """重试发送失败的媒体"""
    failed_chunks = retry_data['failed_chunks']
    work_info = retry_data['work_info']
    original_message = retry_data['original_message']

    success_count = 0
    total_failed = len(failed_chunks)

    for chunk, chunk_num, _ in failed_chunks:
        try:
            # 重新尝试发送
            send_kwargs = {
                'chat_id': chat_id,
                'media': chunk,
                'timeout': 180
            }

            if original_message:
                send_kwargs['reply_to_message_id'] = original_message.message_id

            bot.send_media_group(**send_kwargs)
            success_count += 1

        except Exception as e:
            logger.error(f"重试发送分片 {chunk_num} 失败: {e}")

    # 发送结果
    if success_count == total_failed:
        result_msg = f"✅ 喵呜~ 重试成功啦！所有 {total_failed} 个分片都已发送给主人了~ 小猫咪很棒吧！✨"
    elif success_count > 0:
        result_msg = f"⚠️ 部分重试成功喵：{success_count}/{total_failed} 个分片发送成功~ 小猫咪已经很努力了呢~ 🐾"
    else:
        result_msg = f"❌ 呜呜~ 重试失败了，所有 {total_failed} 个分片仍然无法发送... 对不起喵~ 😿"

    bot.send_message(chat_id, result_msg)

async def send_partial_media(retry_data, chat_id):
    """发送部分可用媒体"""
    try:
        # 重新解析原始URL获取媒体
        original_url = retry_data['original_url']
        work_info = retry_data['work_info']
        original_message = retry_data['original_message']
        failed_chunks = retry_data['failed_chunks']

        # 重新获取媒体数据
        user_preferences = user_manager.get_user_preferences(
            original_message.from_user.id if original_message else None
        )

        async with get_xhs_instance(user_preferences) as xhs_instance:
            results = await xhs_instance.extract(
                original_url,
                download=False,
                data=True
            )

            if not results or len(results) == 0:
                bot.send_message(chat_id, "❌ 呜呜~ 小猫咪无法重新获取媒体数据... 😿")
                return

            data = results[0]
            download_urls = data['下载地址']
            if isinstance(download_urls, str):
                download_urls = download_urls.split()

            download_urls = [url for url in download_urls if is_valid_url(url)]
            media_type = data.get('作品类型', '未知')

            if not download_urls:
                bot.send_message(chat_id, "❌ 呜呜~ 没有可用的媒体链接了喵... 😿")
                return

            # 创建新的媒体组，但要测试每个链接
            available_media = []
            failed_indices = set()

            # 获取失败的媒体索引（从失败的分片推算）
            for chunk, chunk_num, _ in failed_chunks:
                start_idx = (chunk_num - 1) * 10
                for i in range(len(chunk)):
                    failed_indices.add(start_idx + i)

            # 只添加未失败的媒体
            for index, dl_url in enumerate(download_urls):
                if index not in failed_indices:
                    try:
                        if media_type in ['视频', 'video']:
                            available_media.append(InputMediaVideo(media=dl_url))
                        else:
                            available_media.append(InputMediaPhoto(media=dl_url))
                    except Exception as e:
                        logger.error(f"创建媒体项失败: {dl_url}, 错误: {e}")

            if not available_media:
                bot.send_message(
                    chat_id,
                    "❌ 呜呜~ 没有可用的媒体内容了喵...\n"
                    "💡 所有媒体都无法访问，请主人检查链接是否有效呢~ 😿"
                )
                return

            # 为第一个媒体添加说明
            if available_media and work_info:
                caption_parts = [
                    work_info,
                    f"📋 小猫咪已过滤 {len(failed_indices)} 个无效媒体",
                    f"✅ 共 {len(available_media)} 个可用文件喵~ ✨"
                ]
                available_media[0].caption = "\n\n".join(caption_parts)

            # 分片发送可用媒体
            for i in range(0, len(available_media), 10):
                chunk = available_media[i:i + 10]

                send_kwargs = {
                    'chat_id': chat_id,
                    'media': chunk,
                    'timeout': 180
                }

                if original_message:
                    send_kwargs['reply_to_message_id'] = original_message.message_id

                bot.send_media_group(**send_kwargs)

            # 发送完成消息
            bot.send_message(
                chat_id,
                f"✅ 喵呜~ 已发送 {len(available_media)} 个可用媒体文件给主人啦！\n"
                f"🚫 跳过了 {len(failed_indices)} 个无法访问的文件喵~"
            )

    except Exception as e:
        logger.error(f"发送部分媒体失败: {e}")
        bot.send_message(
            chat_id,
            "❌ 呜呜~ 发送可用媒体时出现错误了喵...\n"
            "💡 请主人稍后重试或重新发送链接给我吧~ 😿"
        )

@bot.callback_query_handler(func=lambda call: call.data.startswith(('confirm_', 'cancel_')))
def handle_confirmation(call):
    """处理用户确认或取消操作"""
    user_id = call.from_user.id
    action, callback_user_id = call.data.split('_', 1)
    
    # 验证用户身份
    if str(user_id) != callback_user_id:
        bot.answer_callback_query(call.id, "喵？这不是主人的操作呢~ 我只听主人的话哦~ 🐾")
        return

    if action == "cancel":
        user_manager.remove_pending_urls(user_id)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "好的喵~ 那我就不处理啦~ 有需要随时叫我哦~ ✨")
        return

    # 确认处理
    urls = user_manager.get_pending_urls(user_id)
    if not urls:
        bot.answer_callback_query(call.id, "喵？链接好像跑掉了呢，请重新发送给我吧~ 🐱")
        return

    user_manager.remove_pending_urls(user_id)

    # 编辑确认消息为开始处理状态
    bot.edit_message_text(
        "🚀 收到喵~ 小猫咪开始努力工作啦！请稍等一下下~ ✨",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )
    bot.answer_callback_query(call.id, "开始处理喵~")

    # 处理所有URL，传递聊天信息而不是消息对象
    process_multiple_urls(call.message.chat.id, urls, user_id)

def process_single_url(message, url):
    """处理单个URL"""
    processing_msg = bot.reply_to(message, "喵~ 小猫咪正在努力解析链接中，请主人稍等一下下... 🐾")
    # 删除处理消息
    bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)
    # 发送新的发送消息
    sending_msg = bot.send_message(message.chat.id, "喵呜~ 解析完成！正在用小爪子把内容送给主人，马上就好... ✨",
                                   reply_to_message_id=message.message_id)

    try:
        result = extract_and_send_media(url, message, processing_msg)
        if result:

            # 发送完成后删除发送消息
            bot.delete_message(chat_id=message.chat.id, message_id=sending_msg.message_id)
    except Exception as e:
        logger.error(f"处理单个URL时发生错误: {e}", exc_info=True)
        bot.edit_message_text("呜呜~ 小猫咪在处理过程中遇到了困难，请主人稍后重试吧... 对不起喵~ 😿",
                            chat_id=message.chat.id, message_id=processing_msg.message_id)

def process_multiple_urls(chat_id, urls, user_id=None):
    """处理多个URL - 使用并发处理"""
    if user_id is None:
        # 如果是从消息对象调用的（单链接处理）
        if hasattr(chat_id, 'from_user'):
            user_id = chat_id.from_user.id
            message = chat_id
            chat_id = message.chat.id
        else:
            logger.error("无法确定用户ID")
            return
    else:
        # 如果是从回调调用的（多链接处理）
        message = None

    # 在同步环境中运行异步任务
    try:
        run_async(process_multiple_urls_async(chat_id, urls, user_id, message))
    except Exception as e:
        logger.error(f"批量处理任务失败: {e}", exc_info=True)
        # 发送错误消息给用户
        try:
            bot.send_message(
                chat_id,
                "❌ 呜呜~ 批量处理过程中小猫咪遇到了困难，请主人稍后重试吧~ 😿"
            )
        except Exception:
            pass

async def process_multiple_urls_async(chat_id, urls, user_id, original_message=None):
    """异步批量处理多个URL"""
    total = len(urls)
    user_preferences = user_manager.get_user_preferences(user_id)
    max_concurrent = min(user_preferences.get('max_concurrent', 3), 5)  # 最大不超过5
    timeout = user_preferences.get('timeout', 60)

    # 创建进度消息
    try:
        progress_text = f"🚀 喵呜~ 小猫咪开始批量处理 {total} 个链接啦！\n📊 进度: 0/{total} (0%)\n⚡ 并发数: {max_concurrent} 个小爪子同时工作~ ✨"

        if original_message:
            # 单链接处理，回复原消息
            progress_msg = bot.send_message(
                chat_id,
                progress_text,
                reply_to_message_id=original_message.message_id
            )
        else:
            # 多链接处理，不回复特定消息
            progress_msg = bot.send_message(chat_id, progress_text)

    except Exception as e:
        logger.error(f"发送进度消息失败: {e}")
        return

    completed = 0
    failed = 0
    semaphore = Semaphore(max_concurrent)

    async def process_single_url_with_semaphore(url, index):
        """带信号量控制的单URL处理"""
        nonlocal completed, failed

        async with semaphore:
            try:
                success = await wait_for(
                    extract_and_send_media_async(url, chat_id, user_preferences, original_message),
                    timeout=timeout
                )

                if success:
                    completed += 1
                else:
                    failed += 1

                # 更新进度
                progress = int((completed + failed) / total * 100)
                progress_text = (
                    f"📊 进度: {completed + failed}/{total} ({progress}%)\n"
                    f"✅ 成功: {completed} | ❌ 失败: {failed}"
                )

                try:
                    bot.edit_message_text(
                        f"🚀 小猫咪正在努力工作中喵...\n{progress_text}",
                        chat_id=chat_id,
                        message_id=progress_msg.message_id
                    )
                except Exception:
                    pass  # 忽略编辑消息失败

                return success

            except AsyncTimeoutError:
                failed += 1
                logger.warning(f"URL处理超时: {url}")
                return False
            except Exception as e:
                failed += 1
                logger.error(f"处理URL时发生错误: {url}, 错误: {e}")
                return False

    # 并发处理所有URL
    try:
        tasks = [
            process_single_url_with_semaphore(url, i)
            for i, url in enumerate(urls, 1)
        ]

        await gather(*tasks, return_exceptions=True)

        # 最终结果
        final_text = (
            f"🎉 喵呜~ 小猫咪的任务完成啦！\n"
            f"📊 总计: {total} 个链接\n"
            f"✅ 成功: {completed} 个 (小猫咪很棒吧~ ✨)\n"
            f"❌ 失败: {failed} 个 {('(呜呜~ 对不起喵)' if failed > 0 else '')}"
        )

        bot.edit_message_text(
            final_text,
            chat_id=chat_id,
            message_id=progress_msg.message_id
        )

    except Exception as e:
        logger.error(f"批量处理过程中发生错误: {e}")
        bot.edit_message_text(
            f"❌ 呜呜~ 批量处理被中断了\n已完成: {completed}/{total}\n小猫咪会继续努力的喵~ 😿",
            chat_id=chat_id,
            message_id=progress_msg.message_id
        )

def extract_and_send_media(url, original_message, processing_msg):
    """提取并发送媒体文件 - 同步版本"""
    user_id = original_message.from_user.id
    user_preferences = user_manager.get_user_preferences(user_id)
    chat_id = original_message.chat.id

    # 运行异步版本
    return run_async(extract_and_send_media_async(url, chat_id, user_preferences, original_message))

async def extract_and_send_media_async(url, chat_id, user_preferences=None, original_message=None):
    """异步提取并发送媒体文件"""
    try:
        logger.info(f"开始处理URL: {url}")

        # 使用XHS实例池
        async with get_xhs_instance(user_preferences) as xhs_instance:
            results = await xhs_instance.extract(
                url,
                download=False,
                data=True
            )

            if not results or len(results) == 0:
                logger.warning(f"解析失败，无结果: {url}")
                return False

            data = results[0]

            if not data or not data.get('下载地址'):
                error_message = data.get('message', '无法解析此链接，没有找到下载地址') if data else '解析失败'
                logger.warning(f"解析失败: {url}, 原因: {error_message}")
                return False

            # 格式化作品信息
            work_info = format_work_info(data)

            download_urls = data['下载地址']
            if isinstance(download_urls, str):
                download_urls = download_urls.split()

            download_urls = [url for url in download_urls if is_valid_url(url)]
            media_type = data.get('作品类型', '未知')

            if not download_urls:
                logger.warning(f"解析成功但无有效下载链接: {url}")
                return False

            # 创建媒体组
            media_group = []
            for index, dl_url in enumerate(download_urls):
                try:
                    if media_type in ['视频', 'video']:
                        media_group.append(InputMediaVideo(media=dl_url))
                    elif media_type in ['图文', '图集', 'image']:
                        media_group.append(InputMediaPhoto(media=dl_url))
                    else:
                        media_group.append(InputMediaPhoto(media=dl_url))
                except Exception as e:
                    logger.error(f"添加文件到媒体组失败: {dl_url}, 错误: {e}")

            # 发送媒体组（使用新的重试机制）
            if media_group:
                return await send_media_with_retry_option(
                    chat_id, media_group, work_info, original_message, url
                )
            else:
                logger.warning(f"没有可发送的媒体内容: {url}")
                return False

            return True

    except Exception as e:
        logger.error(f"提取和发送媒体时发生错误: {e}", exc_info=True)
        return False


# --- 主程序入口 ---
if __name__ == '__main__':
    logger.info("🚀 喵呜~ 小红书下载机器人启动中...")
    logger.info("📡 小猫咪开始监听消息啦~ ✨")

    try:
        bot.polling(none_stop=True)
    except KeyboardInterrupt:
        logger.info("🛑 收到停止信号，小猫咪准备休息啦...")
    except Exception as e:
        logger.error(f"❌ 呜呜~ Bot运行时发生错误: {e}", exc_info=True)
    finally:
        logger.info("🧹 小猫咪开始整理玩具...")

        # 清理所有用户数据
        try:
            for user_id in list(user_manager.active_tasks.keys()):
                user_manager.cancel_user_tasks(user_id)
            logger.info("✅ 用户数据已整理好啦")
        except Exception as e:
            logger.error(f"❌ 整理用户数据时出错: {e}")

        # 清理XHS实例池
        try:
            run_async(xhs_pool.cleanup())
            logger.info("✅ XHS实例池已清理干净")
        except Exception as e:
            logger.error(f"❌ 清理XHS实例池时出错: {e}")

        # 保存用户设置
        try:
            user_manager._save_user_settings()
            logger.info("✅ 用户设置已保存好")
        except Exception as e:
            logger.error(f"❌ 保存用户设置时出错: {e}")

        logger.info("🎉 小猫咪已安全休息，晚安喵~ ✨")
