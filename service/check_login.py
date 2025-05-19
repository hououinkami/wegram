#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
微信登录状态监控服务 - 定期检查微信登录状态并在需要时发送二维码
"""

import logging
logger = logging.getLogger(__name__)
import time
import json
import threading
import api.login as login
from api.base import telegram_api
import config

def check_login_status():
    """
    检查登录状态的函数
    如果A函数返回的JSON中存在"Data"键，则表示登录正常
    否则表示登录失效，需要调用B函数重新登录
    """
    try:
        # 调用A函数获取JSON数据
        response_json = login.get_profile(config.MY_WXID)
        
        # 检查是否存在"Data"键
        if response_json.get("Data") is not None:
            logger.info("登录状态正常")
            return True
        else:
            logger.info("登录已失效，正在重新登录...")
            telegram_api(
                chat_id=config.CHAT_ID,
                content="WeChatがオフラインしました",
            )
            push_qr_code()
            return False
    except Exception as e:
        logger.error(f"检查登录状态时出错: {e}")
        push_qr_code()
        
        return False

def periodic_check(interval=300):
    """
    定期执行检查的函数
    参数:
        interval: 检查间隔，单位为秒，默认300秒(5分钟)
    """
    while True:
        try:
            check_login_status()
        except Exception as e:
            logger.error(f"定期检查过程中出错: {e}")
        time.sleep(interval)

def push_qr_code():
    """
    获取并推送微信登录二维码到Telegram
    """
    try:
        qr_json = login.get_qr_code()
        data = json.loads(qr_json) if isinstance(qr_json, str) else qr_json

        if data.get("Success") and "Data" in data:
            qr_url = data["Data"].get("QrUrl", "")
            if qr_url:
                result = telegram_api(
                    chat_id=config.CHAT_ID,
                    content=qr_url,
                    method="sendPhoto",
                    additional_payload={
                        "caption": "QRコードをスキャンしてログイン"
                    }
                )
                logger.info("已发送登录二维码到Telegram")
                return result
            else:
                logger.error("获取到的二维码URL为空")
                return None
        else:
            logger.error(f"获取二维码失败: {data.get('Message', '未知错误')}")
            return None
    except Exception as e:
        logger.error(f"推送二维码过程中出错: {e}")
        return None

def main():
    """
    启动服务的主函数 - 被main.py框架调用
    """
    logger.info("微信登录状态监控服务启动")
    
    # 首次运行立即检查登录状态
    try:
        check_login_status()
    except Exception as e:
        logger.error(f"初始登录状态检查失败: {e}")
    
    # 创建并启动定时检查线程
    check_interval = getattr(config, 'WX_CHECK_INTERVAL', 300)  # 从配置获取间隔，默认5分钟
    check_thread = threading.Thread(target=periodic_check, args=(check_interval,), daemon=True)
    check_thread.start()
    
    # 保持服务运行
    # 注意：由于main.py框架会管理线程，这里不需要额外的循环来保持主线程运行
    # 但为了防止函数立即返回，我们可以让线程加入主线程
    while True:
        # 简单的心跳日志，每小时记录一次
        logger.info("微信登录状态监控服务正在运行")
        time.sleep(3600)  # 1小时

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("微信登录状态监控服务被手动停止")
    except Exception as e:
        logger.error(f"微信登录状态监控服务遇到全局异常: {e}")
