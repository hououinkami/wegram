import asyncio
import logging
from typing import Callable

from telegram import Update
from telegram.ext import Application, CallbackContext, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest
from telegram.error import NetworkError, TimedOut, TelegramError

import config
from utils.telegram_callbacks import BotCallbacks
from utils.telegram_commands import BotCommands
from utils.telegram_to_wechat import process_telegram_update

logger = logging.getLogger(__name__)

class TelegramPollingService:
    """Telegram 轮询服务类"""
    
    def __init__(self, bot_token: str, process_function: Callable, 
                 commands: list = None, command_handlers: dict = None, callback_handlers: dict = None):
        """
        初始化轮询服务
        
        Args:
            bot_token (str): Telegram Bot Token
            process_function (Callable): 处理 update 的外部函数
            commands (list): Bot命令列表
            command_handlers (dict): 命令处理器字典
            callback_handlers (dict): 回调处理器字典
        """
        self.bot_token = bot_token
        self.process_function = process_function
        self.commands = commands or []
        self.command_handlers = command_handlers or {}
        self.callback_handlers = callback_handlers or {}
        self.application = None
        self.is_running = False
        
    def create_application(self):
        """创建Application实例，优化网络配置"""
        # 配置HTTP请求参数，主要是增加连接池大小和超时时间
        request = HTTPXRequest(
            connection_pool_size=10,  # 增加连接池大小
            read_timeout=30,          # 读取超时
            write_timeout=30,         # 写入超时
            connect_timeout=30,       # 连接超时
            pool_timeout=30           # 连接池超时
        )
        
        return Application.builder().token(self.bot_token).request(request).build()
        
    async def handle_update(self, update: Update, context: CallbackContext):
        """处理接收到的 update"""
        try:
            # 调用外部指定的处理函数
            await self.process_function(update)
        except Exception as e:
            logger.error(f"❌ 处理 update 时发生错误: {e}")
    
    async def error_handler(self, update: Update, context: CallbackContext):
        """错误处理器 - 只记录日志，让轮询机制自然处理"""
        error = context.error
        
        if isinstance(error, (NetworkError, TimedOut)):
            # 网络错误很常见，降低日志级别
            logger.debug(f"❌ 网络错误: {error}")
        elif isinstance(error, TelegramError):
            logger.error(f"❌ Telegram API 错误: {error}")
        else:
            logger.error(f"❌ 未知错误: {error}")
    
    def setup_handlers(self):
        """设置消息处理器"""
        # 添加命令处理器
        for command, handler in self.command_handlers.items():
            self.application.add_handler(CommandHandler(command, handler))
        
        # 添加回调查询处理器 
        for pattern, handler in self.callback_handlers.items():
            self.application.add_handler(CallbackQueryHandler(handler, pattern=pattern))

        # 处理所有其他非命令消息
        self.application.add_handler(
            MessageHandler(filters.ALL & ~filters.COMMAND, self.handle_update)
        )
        
        # 添加错误处理器
        self.application.add_error_handler(self.error_handler)
    
    async def setup_commands(self):
        """设置机器人命令菜单"""
        if not self.commands:
            return
            
        try:
            await self.application.bot.set_my_commands(self.commands)
            logger.info(f"✅ 设置了 {len(self.commands)} 个机器人命令")
        except Exception as e:
            # 设置命令失败不影响主要功能
            logger.warning(f"❌ 设置机器人命令失败: {e}")
    
    async def start_polling(self):
        """启动轮询服务"""
        try:
            self.is_running = True
            
            # 创建并初始化应用
            self.application = self.create_application()
            self.setup_handlers()
            
            await self.application.initialize()
            await self.application.start()
            
            # 设置机器人命令
            await self.setup_commands()
            
            # 启动轮询 - 让 python-telegram-bot 自己处理重试
            await self.application.updater.start_polling(
                poll_interval=1.0,  # 轮询间隔
                timeout=20, # 长轮询超时
                bootstrap_retries=-1,   # 启动重试（-1=无限重试）
                drop_pending_updates=False # 保留待处理的消息
            )
            
            logger.info("✅ Telegram 轮询服务已启动")
            
            # 保持运行状态
            while self.is_running:
                await asyncio.sleep(1)
                
        except asyncio.CancelledError:
            logger.info("⚠️ 轮询服务被取消")
            raise
        except Exception as e:
            logger.error(f"❌ 轮询服务异常: {e}")
            raise
        finally:
            await self.stop_polling()
    
    async def stop_polling(self):
        """停止轮询服务"""
        if not self.application:
            return
            
        logger.info("⚠️ 正在停止轮询服务...")
        self.is_running = False
        
        try:
            # 停止轮询器
            if hasattr(self.application, 'updater') and self.application.updater.running:
                await self.application.updater.stop()
            
            # 停止并清理应用
            await self.application.stop()
            await self.application.shutdown()
            
        except Exception as e:
            logger.error(f"❌ 停止轮询服务时发生错误: {e}")
        finally:
            self.application = None
            logger.info("🔴 轮询服务已停止")

# 全局服务实例
polling_service = None

async def main():
    """异步主函数"""
    global polling_service
    
    try:
        polling_service = TelegramPollingService(
            bot_token=config.BOT_TOKEN,
            process_function=process_telegram_update,
            commands=BotCommands.get_command_config(),
            command_handlers=BotCommands.get_command_handlers(),
            callback_handlers=BotCallbacks.get_callback_handlers()
        )
        
        await polling_service.start_polling()
        
    except KeyboardInterrupt:
        logger.info("接收到中断信号")
    except Exception as e:
        logger.error(f"主函数发生错误: {e}")
        raise

async def shutdown():
    """关闭服务"""
    global polling_service
    if polling_service:
        await polling_service.stop_polling()

if __name__ == "__main__":   
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {e}")