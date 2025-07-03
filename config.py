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

LOCALE = Locale(LANG)