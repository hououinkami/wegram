import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
from api import wechat_contacts, wechat_login
from api.telegram_sender import telegram_sender
from utils.contact_manager import contact_manager
from utils.locales import Locale
from utils.telegram_to_wechat import revoke_by_telegram_bot_command

logger = logging.getLogger(__name__)

locale = Locale(config.LANG)

class BotCommands:
    """机器人命令处理类"""
    
    @staticmethod
    async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """更新联系人信息"""
        chat_id = update.effective_chat.id
        
        try:
            to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
            if not to_wxid:
                await telegram_sender.send_text(chat_id, locale.common("no_binding"))
                return
            
            user_info = wechat_contacts.get_user_info(to_wxid)
            
            # 更新TG群组
            await wechat_contacts.update_info(chat_id, user_info.name, user_info.avatar_url)
            
            # 更新映射文件
            await contact_manager.update_contact_by_chatid(chat_id, {
                "name": user_info.name,
                "avatarLink": user_info.avatar_url
            })
            
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")

    @staticmethod
    async def receive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """切换接收消息状态"""
        chat_id = update.effective_chat.id
        
        try:
            await contact_manager.update_contact_by_chatid(chat_id, {"isReceive": "toggle"})
            contact_now = await contact_manager.get_contact_by_chatid(chat_id)
            
            if contact_now and contact_now.get("isReceive"):
                await telegram_sender.send_text(chat_id, locale.common("receive_on"))
            else:
                await telegram_sender.send_text(chat_id, locale.common("receive_off"))
                
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")

    @staticmethod
    async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """删除联系人数据"""
        chat_id = update.effective_chat.id
        
        try:
            to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
            if not to_wxid:
                await telegram_sender.send_text(chat_id, locale.common("no_binding"))
                return
            
            unbind_result = await contact_manager.delete_contact(to_wxid)
            if unbind_result:
                await telegram_sender.send_text(chat_id, locale.common("unbind"))
            else:
                await telegram_sender.send_text(chat_id, locale.common('failed'))
                
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")

    @staticmethod
    async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """撤回消息"""
        chat_id = update.effective_chat.id
        message = update.message
        
        try:
            if not message.reply_to_message:
                await telegram_sender.send_text(chat_id, locale.common("no_reply"))
                return
            
            await revoke_by_telegram_bot_command(chat_id, message)
            
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")

    @staticmethod
    async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """执行二次登录"""
        chat_id = update.effective_chat.id
        
        try:
            relogin = wechat_login.twice_login(config.MY_WXID)
            
            if relogin.get('Message') == "登录成功":
                await telegram_sender.send_text(chat_id, locale.common("twice_login_success"))
            else:
                await telegram_sender.send_text(chat_id, locale.common("twice_login_fail"))
                
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")
    
    # 命令配置
    @classmethod
    def get_command_config(cls):
        """获取命令配置"""
        return [
            ["update", locale.command("update")],
            ["receive", locale.command("receive")], 
            ["unbind", locale.command("unbind")],
            ["rm", locale.command("revoke")],
            ["login", locale.command("login")]
        ]
    
    # 命令处理器映射
    @classmethod
    def get_command_handlers(cls):
        """获取命令处理器映射"""
        return {
            "update": cls.update_command,
            "receive": cls.receive_command,
            "unbind": cls.unbind_command,
            "rm": cls.revoke_command,
            "login": cls.login_command
        }
