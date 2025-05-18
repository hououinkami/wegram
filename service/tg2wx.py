#!/usr/bin/env python3
"""
Telegram消息轮询服务 - 轮询Telegram消息并调用外部处理函数
"""

import time
import requests
import logging
import threading
from datetime import datetime
import config
from typing import Dict, Any, Optional
from utils import sender

# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

# 获取Telegram消息更新
def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    
    try:
        response = requests.get(url, params=params)
        return response.json()
    except Exception as e:
        logger.error(f"获取Telegram更新时出错: {e}")
        return {"ok": False, "error": str(e)}

# Telegram轮询线程
def telegram_polling_thread():
    logger.info("启动Telegram消息轮询线程...")
    offset = None
    
    while True:
        try:
            updates = get_updates(offset)
            
            if updates.get("ok", False):
                results = updates.get("result", [])
                
                for update in results:
                    # 更新offset为最新消息的ID+1
                    offset = update["update_id"] + 1
                    
                    # 调用外部模块处理消息
                    sender.process_telegram_update(update)
            else:
                logger.error(f"获取更新失败: {updates}")
            
            # 短暂休眠，避免过于频繁的请求
            time.sleep(config.POLLING_INTERVAL)
        except Exception as e:
            logger.error(f"轮询过程中出错: {e}")
            time.sleep(config.POLLING_INTERVAL)

def main():
    """主函数"""
    logger.info("主函数开始执行")
    
    try:
        # 启动Telegram轮询线程
        polling_thread = threading.Thread(target=telegram_polling_thread, daemon=True)
        polling_thread.start()
        logger.info("Telegram轮询线程已启动")
        
        logger.info("仅启动Telegram轮询服务")
            
        # 保持主线程运行
        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"主函数异常: {e}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"全局异常: {e}")
