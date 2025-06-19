import asyncio
import logging
from typing import Callable

from telegram import Update
from telegram.ext import Application, CallbackContext, CommandHandler, MessageHandler, filters

import config
from utils.telegram_commands import BotCommands
from utils.telegram_to_wechat import process_telegram_update

logger = logging.getLogger(__name__)

class TelegramPollingService:
    """Telegram 轮询服务类"""
    
    def __init__(self, bot_token: str, process_function: Callable, commands: list = None, command_handlers: dict = None):
        """
        初始化轮询服务
        
        Args:
            bot_token (str): Telegram Bot Token
            process_function (Callable): 处理 update 的外部函数
            commands (list): Bot命令列表，格式为 [{"command": "start", "description": "开始使用"}]
            command_handlers (dict): 命令处理器字典 {"command_name": handler_function}
        """
        self.bot_token = bot_token
        self.process_function = process_function
        self.commands = commands or []
        self.command_handlers = command_handlers or {}
        self.application = None
        self.is_running = False
        
    async def handle_update(self, update: Update, context: CallbackContext):
        """处理接收到的 update"""
        try:
            # 调用外部指定的处理函数
            await self.process_function(update)
        except Exception as e:
            logger.error(f"处理 update 时发生错误: {e}")
    
    async def error_handler(self, update: Update, context: CallbackContext):
        """错误处理器"""
        logger.error(f"Update {update} 引发了错误 {context.error}")
    
    def setup_handlers(self):
        """设置消息处理器"""
        # 添加命令处理器
        for command, handler in self.command_handlers.items():
            command_handler = CommandHandler(command, handler)
            self.application.add_handler(command_handler)
        
        # 处理所有其他非命令消息
        message_handler = MessageHandler(
            filters.ALL & ~filters.COMMAND,  # 排除命令消息
            self.handle_update
        )
        self.application.add_handler(message_handler)
        
        # 添加错误处理器
        self.application.add_error_handler(self.error_handler)
    
    async def setup_commands(self):
        """设置机器人命令菜单"""
        if not self.commands:
            return
            
        try:
            await self.application.bot.set_my_commands(self.commands)
            logger.info(f"✅ 成功设置 {len(self.commands)} 个机器人命令")
            
        except Exception as e:
            logger.error(f"❌ 设置机器人命令失败: {e}")
    
    async def start_polling(self):
        """启动轮询服务"""
        try:
            # 创建 Application 实例
            self.application = Application.builder().token(self.bot_token).build()
            
            # 设置处理器
            self.setup_handlers()
            
            logger.info("正在启动 Telegram 轮询服务...")
            
            # 初始化应用
            await self.application.initialize()
            await self.application.start()
            
            # 设置机器人命令
            await self.setup_commands()
            
            # 启动轮询器
            await self.application.updater.start_polling(
                poll_interval=1.0,
                timeout=20,
                drop_pending_updates=False
            )
            
            self.is_running = True
            logger.info("Telegram 轮询服务已启动")
            
            # 保持运行状态，直到被停止
            while self.is_running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"启动轮询服务时发生错误: {e}")
            raise
        finally:
            # 确保资源被清理
            if self.application:
                await self._cleanup()
    
    async def stop_polling(self):
        """停止轮询服务"""
        self.is_running = False
        await self._cleanup()
    
    async def _cleanup(self):
        """清理资源"""
        try:
            if self.application:
                logger.info("正在停止 Telegram 轮询服务...")
                
                # 停止轮询器
                if hasattr(self.application, 'updater') and self.application.updater.running:
                    await self.application.updater.stop()
                
                # 停止应用
                await self.application.stop()
                await self.application.shutdown()
                
                logger.info("Telegram 轮询服务已停止")
        except Exception as e:
            logger.error(f"清理资源时发生错误: {e}")
        finally:
            self.application = None

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
            command_handlers=BotCommands.get_command_handlers()
        )
        
        await polling_service.start_polling()
        
    except asyncio.CancelledError:
        logger.info("Telegram 服务被取消")
    except Exception as e:
        logger.error(f"启动轮询服务时发生错误: {e}")
        raise

async def shutdown():
    """关闭服务"""
    global polling_service
    if polling_service:
        await polling_service.stop_polling()

# 如果直接运行此脚本
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("接收到中断信号，程序退出")
