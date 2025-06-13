#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
微信登录状态监控服务 - 定期检查微信登录状态并在需要时发送二维码
"""

import logging
logger = logging.getLogger(__name__)
import time
import threading
import api.login as login
from api.base import telegram_api
from service.tg2wx import get_user_id
import config
from utils.locales import Locale

locale = Locale(config.LANG)

# 全局变量
is_logged_in = None
service_running = True
stop_event = threading.Event()

def check_login_status():
    global is_logged_in
    
    try:
        response_json = login.get_profile(config.MY_WXID)
        tg_user_id = get_user_id()

        if response_json.get("Data") is not None:
            if is_logged_in is False:
                telegram_api(tg_user_id, locale.common("online"))
                logger.info("微信状态：已上线")
            is_logged_in = True
            return True
        else:
            if is_logged_in is not False:
                telegram_api(tg_user_id, locale.common("offline"))
                logger.warning("微信状态：已离线")
            is_logged_in = False
            return False
    except Exception as e:
        logger.error(f"检查登录状态时出错: {e}")
        return False
            
def periodic_check(interval=600):
    """定期执行检查的函数"""
    while not stop_event.is_set():
        if not stop_event.wait(interval):
            try:
                if service_running:
                    check_login_status()
            except Exception as e:
                logger.error(f"定期检查过程中出错: {e}")

def stop_service():
    """停止服务"""
    global service_running
    service_running = False
    stop_event.set()
    logger.info("服务停止信号已发送")

def main():
    """启动服务的主函数"""
    global service_running
    logger.info("微信登录状态监控服务启动")
    
    # 首次检查
    try:
        check_login_status()
    except Exception as e:
        logger.error(f"初始登录状态检查失败: {e}")
    
    # 启动定时检查线程
    check_interval = getattr(config, 'WX_CHECK_INTERVAL', 300)
    check_thread = threading.Thread(target=periodic_check, args=(check_interval,), daemon=True)
    check_thread.start()
    
    # 保持服务运行
    try:
        while service_running:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在关闭服务...")
        stop_service()
        raise

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("微信登录状态监控服务被手动停止")
    except Exception as e:
        logger.error(f"微信登录状态监控服务遇到全局异常: {e}")
