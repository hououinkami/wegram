import asyncio
import logging

import config
import api.login as login
from api.bot import telegram_sender
from service.telethon_client import get_user_id
from utils.locales import Locale

logger = logging.getLogger(__name__)

# 全局变量
locale = Locale(config.LANG)
is_logged_in = None
service_running = True

async def check_login_status():
    """检查微信登录状态"""
    global is_logged_in
    
    try:
        response_json = await login.get_profile(config.MY_WXID)
        tg_user_id = get_user_id()

        if response_json.get("Data") is not None:
            if is_logged_in is False:
                await telegram_sender.send_text(tg_user_id, locale.common("online"))
            is_logged_in = True
            return True
        else:
            if is_logged_in is not False:
                await telegram_sender.send_text(tg_user_id, locale.common("offline"))
            is_logged_in = False
            return False
    except Exception as e:
        logger.error(f"检查登录状态时出错: {e}")
        return False

async def periodic_check(interval=600):
    """定期执行检查的异步函数"""
    global service_running
    
    while service_running:
        try:
            await check_login_status()
        except Exception as e:
            logger.error(f"定期检查过程中出错: {e}")
        
        # 使用 asyncio.sleep 替代 threading.Event.wait
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("定期检查任务被取消")
            break

def stop_service():
    """停止服务"""
    global service_running
    service_running = False
    logger.info("服务停止信号已发送")

async def main():
    """启动服务的主函数"""
    global service_running
    logger.info("微信登录状态监控服务启动")
    
    # 首次检查
    try:
        await check_login_status()
    except Exception as e:
        logger.error(f"初始登录状态检查失败: {e}")
    
    # 启动定时检查任务
    check_interval = getattr(config, 'WX_CHECK_INTERVAL', 300)
    check_task = asyncio.create_task(periodic_check(check_interval))
    
    # 保持服务运行
    try:
        while service_running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在关闭服务...")
        stop_service()
        check_task.cancel()
        try:
            await check_task
        except asyncio.CancelledError:
            pass
        raise
    finally:
        # 确保任务被正确取消
        if not check_task.done():
            check_task.cancel()
            try:
                await check_task
            except asyncio.CancelledError:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("微信登录状态监控服务被手动停止")
    except Exception as e:
        logger.error(f"微信登录状态监控服务遇到全局异常: {e}")
