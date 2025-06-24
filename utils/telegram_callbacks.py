import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

class BotCallbacks:
    """Bot回调处理器类"""
    
    @staticmethod
    async def handle_agreement_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理同意接受按钮回调"""
        query = update.callback_query
        await query.answer()
        
        try:
            if query.data == "agree_accept":
                # 调用你的指定函数
                await BotCallbacks.your_specified_function(update, context)
                await query.edit_message_text("✅ 已同意接受！")
                
        except Exception as e:
            logger.error(f"处理同意回调时出错: {e}")
            await query.edit_message_text("❌ 处理失败，请重试")
    
    @staticmethod
    async def your_specified_function(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """点击按钮后要调用的指定函数"""
        chat_id = update.effective_chat.id
        # 这里添加你的具体业务逻辑
        logger.info(f"用户 {chat_id} 同意了条款")
        # 可以发送确认消息或执行其他操作
    
    @staticmethod
    def get_callback_handlers():
        """获取回调处理器配置"""
        return {
            "^agree_accept$": BotCallbacks.handle_agreement_callback,
            # 可以添加更多回调处理器
            # "^other_callback$": BotCallbacks.handle_other_callback,
        }
