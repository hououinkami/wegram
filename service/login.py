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
from service.tg2wx import get_user_id
import config

# 全局变量，用于跟踪当前登录状态
is_logged_in = None  # None表示初始状态，True表示登录，False表示离线

def check_login_status():
    """
    检查登录状态的函数
    如果A函数返回的JSON中存在"Data"键，则表示登录正常
    否则表示登录失效，需要调用B函数重新登录
    """
    global is_logged_in
    
    try:
        # 调用A函数获取JSON数据
        response_json = login.get_profile(config.MY_WXID)
        tg_user_id = get_user_id()

        # 检查是否存在"Data"键
        if response_json.get("Data") is not None:
            # 登录状态正常
            logger.info("🟢登录状态正常")
            
            # 如果之前是离线状态，发送上线通知
            if is_logged_in is False:
                telegram_api(
                    chat_id=tg_user_id,
                    content="🟢WeChatがオンラインしました",
                )

            is_logged_in = True
            return True
        else:
            # 登录已失效
            logger.info("🔴登录已失效")
            
            # 只有在首次检测到离线或从在线状态变为离线状态时才发送通知
            if is_logged_in is not False:  # None(初始状态)或True(之前在线)
                telegram_api(
                    chat_id=tg_user_id,
                    content="🔴WeChatがオフラインしました",
                )
                # push_qr_code()
            
            is_logged_in = False
            return False
    except Exception as e:
        logger.error(f"检查登录状态时出错: {e}")
            
def periodic_check(interval=600):
    """
    定期执行检查的函数
    参数:
        interval: 检查间隔，单位为秒，默认300秒(5分钟)
    """
    while True:
        time.sleep(interval)
        try:
            check_login_status()
        except Exception as e:
            logger.error(f"定期检查过程中出错: {e}")
        

def push_qr_code():
    """
    获取并推送微信登录二维码到Telegram
    """
    try:
        qr_json = login.get_qr_code()
        data = json.loads(qr_json) if isinstance(qr_json, str) else qr_json
        tg_user_id = get_user_id()
        
        if data.get("Success") and "Data" in data:
            qr_url = data["Data"].get("QrUrl", "")
            if qr_url:
                result = telegram_api(
                    chat_id=tg_user_id,
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

# 初始化
def newinit():
    result = login.newinit(config.MY_WXID)
    if result:
        logger.info("Newinit初始化成功")

        # 检查是否需要继续同步
        continue_flag = result.get("ContinueFlag")
        if continue_flag == 1:
            logger.info("需要继续同步数据")
            # 获取同步键
            current_synckey = result.get("CurrentSynckey", {}).get("Buffer", "")
            max_synckey = result.get("MaxSynckey", {}).get("Buffer", "")

            # 再次执行Newinit，带入同步键
            newinit(config.MY_WXID, max_synckey, current_synckey)

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
