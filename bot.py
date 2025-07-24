# -*- coding: utf-8 -*-

import telebot
from telebot.types import InputMediaPhoto, InputMediaVideo
import requests
import os
import re
import logging
import configparser
import sys

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
    PARSE_API_URL = config.get('api', 'parse_url')
    DOWNLOAD_DIR = config.get('app', 'download_dir', fallback='downloads')

    # 关键配置校验
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN_HERE':
        logger.critical("错误: Telegram Bot Token 未在 config.ini 文件中正确配置。")
        sys.exit("请先配置好 config.ini 文件中的 bot_token。")
    if not PARSE_API_URL:
        logger.critical("错误: 解析API地址(parse_url)未在 config.ini 文件中配置。")
        sys.exit("请先配置好 config.ini 文件中的 parse_url。")

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
    user_url = message.text

    if not is_valid_url(user_url):
        bot.reply_to(message, "您发送的似乎不是一个有效的URL，请检查后重试。")
        return

    processing_msg = None
    try:
        processing_msg = bot.reply_to(message, "正在处理链接，请稍候...")

        logger.info(f"收到来自用户 {message.from_user.id} 的URL: {user_url}")
        payload = {"url": user_url}
        response = requests.post(PARSE_API_URL, json=payload, timeout=30)
        response.raise_for_status()

        api_data = response.json()

        if api_data and api_data.get('data') and api_data['data'].get('下载地址'):
            download_urls = [url for url in api_data['data']['下载地址'] if is_valid_url(url)]
            media_type = api_data['data'].get('作品类型', '未知')

            if not download_urls:
                bot.edit_message_text("解析成功，但未找到有效的下载链接。", chat_id=message.chat.id,
                                      message_id=processing_msg.message_id)
                return

            bot.edit_message_text(f"解析成功！共找到 {len(download_urls)} 个文件，正在下载并打包发送...",
                                  chat_id=message.chat.id, message_id=processing_msg.message_id)

            # --- 新逻辑：下载所有文件并创建媒体组 ---
            media_group = []
            for index, dl_url in enumerate(download_urls):
                try:
                    logger.info(f"正在下载第 {index + 1}/{len(download_urls)} 个文件: {dl_url}")
                    media_response = requests.get(dl_url, stream=True, timeout=120)
                    media_response.raise_for_status()

                    content = media_response.content

                    if media_type.lower() in ['video', '视频']:
                        media_group.append(InputMediaVideo(media=content))
                    elif media_type.lower() in ['image', '图片']:
                        media_group.append(InputMediaPhoto(media=content))
                    else:  # 如果类型未知，则单独作为文件发送
                        bot.send_document(message.chat.id, content, caption=f"来自: {user_url}", timeout=120)

                except requests.exceptions.RequestException as e:
                    logger.error(f"下载文件失败: {dl_url}, 错误: {e}", exc_info=True)
                    bot.send_message(message.chat.id, f"下载第 {index + 1} 个文件时出错，已跳过。")

            # --- 新逻辑：发送媒体组 ---
            if media_group:
                # 给媒体组的第一个元素添加标题
                media_group[0].caption = f"共 {len(media_group)} 个文件\n来源: {user_url}"

                logger.info(f"准备发送 {len(media_group)} 个媒体文件...")
                # Telegram 一次最多发送10个, 所以需要分块
                for i in range(0, len(media_group), 10):
                    chunk = media_group[i:i + 10]
                    try:
                        bot.send_media_group(message.chat.id, chunk, timeout=180)
                    except Exception as e:
                        logger.error(f"发送媒体组失败: {e}", exc_info=True)
                        bot.send_message(message.chat.id, "发送媒体组时遇到错误，请稍后再试。")

            bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)

        else:
            error_message = api_data.get('message', '无法解析此链接，未找到下载地址。')
            bot.edit_message_text(error_message, chat_id=message.chat.id, message_id=processing_msg.message_id)

    except requests.exceptions.Timeout:
        logger.warning("请求解析API超时")
        if processing_msg:
            bot.edit_message_text("请求解析服务器超时，请稍后再试。", chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)
    except requests.exceptions.RequestException as e:
        logger.error(f"请求解析API时发生错误: {e}", exc_info=True)
        if processing_msg:
            bot.edit_message_text("连接解析服务器失败，请检查API服务是否正常运行。", chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)
    except Exception as e:
        logger.error(f"处理过程中发生未知错误: {e}", exc_info=True)
        if processing_msg:
            bot.edit_message_text(f"处理过程中发生未知错误，请联系管理员。", chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)


# --- 主程序入口 ---
if __name__ == '__main__':
    logger.info("Establishing battle field control, standby...")
    logger.info("Bot is running and polling for updates...")
    bot.polling(none_stop=True)
