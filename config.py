import os

from utils.locales import Locale

# 语言
LANG = os.getenv("LANG", "zh")
locale = Locale(LANG)

# 下载目录
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
IMAGE_DIR = os.path.join(DOWNLOAD_DIR, "image")
VIDEO_DIR = os.path.join(DOWNLOAD_DIR, "video")
STICKER_DIR = os.path.join(DOWNLOAD_DIR, "sticker")
FILE_DIR = os.path.join(DOWNLOAD_DIR, "file")
VOICE_DIR = os.path.join(DOWNLOAD_DIR, "voice")

# 设置
DEVICE_MODEL = os.getenv("DEVICE_MODEL", "WeGram")
TG_MODE = os.getenv("TG_MODE", "polling")
WECHAT_MODE = os.getenv("WECHAT_MODE", "callback")
AUTO_CREATE_GROUPS = os.getenv("AUTO_CREATE_GROUPS", "True").lower() == "true"
ENABLE_BLACKLIST = os.getenv('ENABLE_BLACKLIST', 'true').lower() == 'true'
MAX_RATIO = os.getenv("MAX_RATIO", 4.0)
MAX_SIZE = os.getenv("MAX_SIZE", 10)

# WeChat API
CALLBACK_PORT = int(os.getenv("CALLBACK_PORT", "8088"))
BASE_URL = os.getenv("BASE_URL", "http://wegram-server:8058/api")
MY_WXID = os.getenv("MY_WXID")
if not MY_WXID:
    raise ValueError("MY_WXID environment variable is required")
PUSH_WXID = os.getenv("PUSH_WXID")
DEVICE_ID = os.getenv("DEVICE_ID")
RABBITMQ_URL = os.getenv("RABBITMQ_URL")
if not RABBITMQ_URL:
    raise ValueError("RABBITMQ_URL environment variable is required")

# Telegram Bot
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
if not PHONE_NUMBER:
    raise ValueError("PHONE_NUMBER environment variable is required")
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")
API_ID = int(os.getenv("API_ID"))
if not API_ID:
    raise ValueError("API_ID environment variable is required")
API_HASH = os.getenv("API_HASH")
if not API_HASH:
    raise ValueError("API_HASH environment variable is required")
WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN")
WEBHOOK_PORT = os.getenv("WEBHOOK_PORT")
SSL_CERT_NAME = os.getenv("SSL_CERT_NAME", "cert.pem")
SSL_KEY_NAME = os.getenv("SSL_KEY_NAME", "key.pem")
WECHAT_CHAT_FOLDER = os.getenv("WECHAT_CHAT_FOLDER", "聊天")
WECHAT_OFFICAL_FOLDER = os.getenv("WECHAT_OFFICAL_FOLDER", "公众号")

# 处理黑名单关键词
def _parse_blacklist_keywords():
    """解析黑名单关键词"""
    # 从环境变量获取
    blacklist_str = os.getenv('BLACKLIST', '')
    
    if blacklist_str:
        # 按逗号分割，并去除每个关键词的前后空格
        keywords = [keyword.strip() for keyword in blacklist_str.split(',')]
        # 过滤掉空字符串
        keywords = [keyword for keyword in keywords if keyword]
        return keywords
    
    # 如果环境变量没有设置，使用默认值
    return [
        # 这里可以设置一些默认的黑名单关键词
        # "默认关键词1",
        # "默认关键词2",
    ]

# 黑名单关键词列表
BLACKLIST_KEYWORDS = _parse_blacklist_keywords()

# 和风天气API
QWEATHER_HOST = os.getenv("QWEATHER_HOST")
QWEATHER_PRIVATE_KEY = os.getenv("QWEATHER_PRIVATE_KEY")
QWEATHER_PROJECT_ID = os.getenv("QWEATHER_PROJECT_ID")
QWEATHER_KEY_ID = os.getenv("QWEATHER_KEY_ID")
LOCATION_ID = os.getenv("LOCATION_ID")
LOCATION = os.getenv("LOCATION")
