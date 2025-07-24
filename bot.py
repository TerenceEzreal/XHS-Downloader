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
from asyncio import new_event_loop, set_event_loop
from datetime import datetime

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

# 初始化XHS实例
xhs_instance = None

async def init_xhs():
    """异步初始化XHS实例"""
    global xhs_instance
    xhs_instance = XHS(
        work_path=DOWNLOAD_DIR,
        folder_name="",  # 不使用子文件夹
        record_data=False,
        download_record=False,
        folder_mode=False,
        _print=True,  # 禁用打印输出

        # image_format="PNG",
        image_format="WEBP",
        # image_format="JPEG",
        # image_format="HEIC",
        # image_format="AVIF",
        # image_format="AUTO",
    )
    await xhs_instance.__aenter__()
    return xhs_instance

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
        info_lines.append(f"📝 {title}")
    
    description = data.get('作品描述', '')
    if description:
        # 移除方括号表情
        description = re.sub(r'\[.*?]', '', description).strip()
        # 扩大描述长度限制
        desc = description[:150] + "..." if len(description) > 150 else description
        if desc:
            info_lines.append(f"📄 {desc}")
    
    publish_time = data.get('发布时间', '未知')
    if publish_time and publish_time != '未知':
        formatted_time = format_publish_time(publish_time)
        info_lines.append(f"⏰ {formatted_time}")

    author = data.get('作者昵称', '未知')
    if author and author != '未知':
        info_lines.append(f"👤 {author}")
    
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

# 存储用户待处理的URL列表
user_pending_urls = {}

# --- Bot 消息处理器 ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """处理 /start 和 /help 命令"""
    welcome_text = """
你好！欢迎使用URL内容下载机器人。
请直接向我发送包含有效网址的链接，我将尝试解析并下载其中的图片或视频。
"""
    bot.reply_to(message, welcome_text)


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """处理所有文本消息，并从中提取URL"""
    user_text = message.text
    user_id = message.from_user.id
    
    # 提取所有URL
    extracted_urls = extract_urls_from_text(user_text)
    
    if not extracted_urls:
        bot.reply_to(message, "未检测到有效的URL，请发送包含小红书链接的消息。")
        return
    
    # 如果只有一个URL且文本就是这个URL，直接处理
    if len(extracted_urls) == 1 and user_text.strip() == extracted_urls[0]:
        process_single_url(message, extracted_urls[0])
        return
    
    # 多个URL或包含其他文字，需要确认
    user_pending_urls[user_id] = extracted_urls
    
    urls_text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(extracted_urls)])
    confirm_text = f"检测到 {len(extracted_urls)} 个链接：\n\n{urls_text}\n\n是否处理这些链接？"
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ 确认处理", callback_data=f"confirm_{user_id}"),
        InlineKeyboardButton("❌ 取消", callback_data=f"cancel_{user_id}")
    )
    
    bot.reply_to(message, confirm_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith(('confirm_', 'cancel_')))
def handle_confirmation(call):
    """处理用户确认或取消操作"""
    user_id = call.from_user.id
    action, callback_user_id = call.data.split('_', 1)
    
    # 验证用户身份
    if str(user_id) != callback_user_id:
        bot.answer_callback_query(call.id, "无效操作")
        return
    
    if action == "cancel":
        if user_id in user_pending_urls:
            del user_pending_urls[user_id]
        bot.edit_message_text("操作已取消", call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "已取消")
        return
    
    # 确认处理
    if user_id not in user_pending_urls:
        bot.answer_callback_query(call.id, "链接已过期，请重新发送")
        return
    
    urls = user_pending_urls[user_id]
    del user_pending_urls[user_id]

    # 删除确认消息
    bot.delete_message(call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "开始处理")



    # 处理所有URL
    process_multiple_urls(call.message, urls)

def process_single_url(message, url):
    """处理单个URL"""
    processing_msg = bot.reply_to(message, "正在解析，请稍候...")
    # 删除处理消息
    bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)
    # 发送新的发送消息
    sending_msg = bot.send_message(message.chat.id, "正在发送，请稍候...",
                                   reply_to_message_id=message.message_id)

    try:
        result = extract_and_send_media(url, message, processing_msg)
        if result:

            # 发送完成后删除发送消息
            bot.delete_message(chat_id=message.chat.id, message_id=sending_msg.message_id)
    except Exception as e:
        logger.error(f"处理单个URL时发生错误: {e}", exc_info=True)
        bot.edit_message_text("处理过程中发生错误，请稍后重试。", 
                            chat_id=message.chat.id, message_id=processing_msg.message_id)

def process_multiple_urls(message, urls):
    """处理多个URL"""
    total = len(urls)
    
    for index, url in enumerate(urls, 1):
        try:
            status_msg = bot.send_message(message.chat.id, 
                                        f"正在处理第 {index}/{total} 个链接...",
                                        reply_to_message_id=message.message_id)
            
            result = extract_and_send_media(url, message, status_msg)
            
            if result:
                bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)
            else:
                bot.edit_message_text(f"第 {index} 个链接处理失败", 
                                    chat_id=message.chat.id, message_id=status_msg.message_id)
                
        except Exception as e:
            logger.error(f"处理第 {index} 个URL时发生错误: {e}", exc_info=True)
            bot.send_message(message.chat.id, f"第 {index} 个链接处理失败: {str(e)[:100]}")

def extract_and_send_media(url, original_message, processing_msg):
    """提取并发送媒体文件"""
    try:
        logger.info(f"开始处理URL: {url}")
        
        # 使用XHS直接处理
        async def process_url():
            global xhs_instance
            if xhs_instance is None:
                await init_xhs()
            
            results = await xhs_instance.extract(
                url,
                download=False,
                data=True
            )
            return results

        results = run_async(process_url())
        
        if not results or len(results) == 0:
            bot.edit_message_text("解析失败，请检查链接是否有效。", 
                                chat_id=original_message.chat.id, 
                                message_id=processing_msg.message_id)
            return False

        data = results[0]
        
        if not data or not data.get('下载地址'):
            error_message = data.get('message', '无法解析此链接，未找到下载地址。') if data else '解析失败'
            bot.edit_message_text(error_message, 
                                chat_id=original_message.chat.id, 
                                message_id=processing_msg.message_id)
            return False

        # 格式化作品信息
        work_info = format_work_info(data)
        
        download_urls = data['下载地址']
        if isinstance(download_urls, str):
            download_urls = download_urls.split()
        
        download_urls = [url for url in download_urls if is_valid_url(url)]
        media_type = data.get('作品类型', '未知')

        if not download_urls:
            bot.edit_message_text("解析成功，但未找到有效的下载链接。", 
                                chat_id=original_message.chat.id,
                                message_id=processing_msg.message_id)
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

        # 发送媒体组（分片处理）
        if media_group:
            total_chunks = (len(media_group) + 9) // 10  # 向上取整
            
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
                    
                    bot.send_media_group(
                        chat_id=original_message.chat.id, 
                        media=chunk, 
                        reply_to_message_id=original_message.message_id,
                        timeout=180
                    )
                    
                except Exception as e:
                    logger.error(f"发送媒体组失败: {e}")
                    # 备用方案：逐个发送
                    for media_index, media_item in enumerate(chunk):
                        try:
                            caption = None
                            # 只在第一个媒体项目添加caption
                            if media_index == 0 and i == 0:
                                caption_parts = []
                                if work_info:
                                    caption_parts.append(work_info)
                                caption_parts.append(f"📁 共 {len(download_urls)} 个文件")
                                if total_chunks > 1:
                                    caption_parts.append(f"📦 分片: [{current_chunk}/{total_chunks}]")
                                caption = "\n\n".join(caption_parts)
                            
                            if isinstance(media_item, InputMediaVideo):
                                bot.send_video(
                                    chat_id=original_message.chat.id,
                                    video=media_item.media,
                                    caption=caption,
                                    reply_to_message_id=original_message.message_id,
                                    timeout=120
                                )
                            else:
                                bot.send_photo(
                                    chat_id=original_message.chat.id,
                                    photo=media_item.media,
                                    caption=caption,
                                    reply_to_message_id=original_message.message_id,
                                    timeout=120
                                )
                        except Exception as single_error:
                            logger.error(f"单独发送媒体失败: {single_error}")

        return True

    except Exception as e:
        logger.error(f"提取和发送媒体时发生错误: {e}", exc_info=True)
        bot.edit_message_text("处理过程中发生未知错误，请联系管理员。", 
                            chat_id=original_message.chat.id,
                            message_id=processing_msg.message_id)
        return False


# --- 主程序入口 ---
if __name__ == '__main__':
    logger.info("Establishing battle field control, standby...")
    logger.info("Bot is running and polling for updates...")
    
    try:
        bot.polling(none_stop=True)
    except KeyboardInterrupt:
        logger.info("Bot停止中...")
    finally:
        # 清理XHS实例
        if xhs_instance:
            try:
                run_async(xhs_instance.__aexit__(None, None, None))
            except Exception as e:
                logger.error(f"清理XHS实例时出错: {e}")
        logger.info("Bot已停止")
