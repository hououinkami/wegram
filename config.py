import os

from utils.locales import Locale

# 语言
LANG = os.getenv("LANG", "zh")

# Telegram Bot
TG_MODE = os.getenv("TG_MODE", "polling")
WECHAT_MODE = os.getenv("WECHAT_MODE", "callback")
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")
API_ID = int(os.getenv("API_ID"))
if not API_ID:
    raise ValueError("API_ID environment variable is required")
API_HASH = os.getenv("API_HASH")
if not API_HASH:
    raise ValueError("API_HASH environment variable is required")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
if not PHONE_NUMBER:
    raise ValueError("PHONE_NUMBER environment variable is required")
DEVICE_MODEL = os.getenv("DEVICE_MODEL", "WeGram")
WECHAT_CHAT_FOLDER = os.getenv("WECHAT_CHAT_FOLDER", "チャット")
WECHAT_OFFICAL_FOLDER = os.getenv("WECHAT_OFFICAL_FOLDER", "WeChat")
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "1"))
AUTO_CREATE_GROUPS = os.getenv("AUTO_CREATE_GROUPS", "True").lower() == "true"
WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN")
WEBHOOK_PORT = os.getenv("WEBHOOK_PORT", 8443)
SSL_CERT_NAME = os.getenv("SSL_CERT_NAME", "cert.pem")
SSL_KEY_NAME = os.getenv("SSL_KEY_NAME", "key.pem")

# WeChat API
PORT = int(os.getenv("PORT", "8088"))
BASE_URL = os.getenv("BASE_URL", "http://wegram-server:8058/api")
MY_WXID = os.getenv("MY_WXID")
if not MY_WXID:
    raise ValueError("MY_WXID environment variable is required")
RABBITMQ_URL = os.getenv("RABBITMQ_URL")
if not RABBITMQ_URL:
    raise ValueError("RABBITMQ_URL environment variable is required")
WX_CHECK_INTERVAL = int(os.getenv("WX_CHECK_INTERVAL", "300"))
PUSH_WXID = "49925190240@chatroom"

LOCALE = Locale(LANG)

# 黑名单功能开关
ENABLE_BLACKLIST = os.getenv('ENABLE_BLACKLIST', 'true').lower() == 'true'

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
