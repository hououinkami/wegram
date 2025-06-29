import logging
from functools import wraps
from typing import Dict, Callable

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

class CallbackRegistry:
    """回调注册器"""
    _handlers: Dict[str, Callable] = {}
    
    @classmethod
    def register(cls, callback_data: str):
        """装饰器：注册回调处理器"""
        def decorator(func):
            cls._handlers[callback_data] = func
            
            @wraps(func)
            async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
                try:
                    return await func(update, context)
                except Exception as e:
                    logger.error(f"回调处理器 {callback_data} 出错: {e}")
                    query = update.callback_query
                    if query:
                        await query.edit_message_text("❌ 处理失败，请重试")
            return wrapper
        return decorator
    
    @classmethod
    def get_handlers(cls):
        """获取所有注册的处理器"""
        return cls._handlers.copy()

class BotCallbacks:
    """Bot回调处理器类"""
    
    @staticmethod
    async def universal_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """通用回调处理器"""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        handlers = CallbackRegistry.get_handlers()
        
        if callback_data in handlers:
            await handlers[callback_data](update, context)
        else:
            logger.warning(f"未找到回调处理器: {callback_data}")
            await query.edit_message_text("❌ 未知操作")
    
    @staticmethod
    def get_callback_handlers():
        """获取回调处理器配置"""
        return {
            ".*": BotCallbacks.universal_callback_handler,  # 匹配所有回调
        }

# 使用装饰器注册回调处理器
@CallbackRegistry.register("add_to_contact")
async def handle_add_to_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理名片按钮"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    
    logger.info(update)
    # await query.edit_message_text("✅ 已同意接受！")

# 添加新的回调处理器就这么简单：
@CallbackRegistry.register("new_feature")
async def handle_new_feature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """新功能处理器"""
    query = update.callback_query
    await query.edit_message_text("🎉 新功能已激活！")