import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Update
from telegram.ext import ContextTypes

import config
from config import LOCALE as locale
from api import wechat_contacts, wechat_login
from api.telegram_sender import telegram_sender
from api.wechat_api import wechat_api
from service.telethon_client import get_user_id
from utils.contact_manager import contact_manager
from utils.group_binding import process_avatar_from_url
from utils.telegram_callbacks import create_callback_data
from utils.telegram_to_wechat import revoke_by_telegram_bot_command

logger = logging.getLogger(__name__)

class BotCommands:
    """机器人命令处理类"""
    
    @staticmethod
    async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """更新联系人信息"""
        chat_id = update.effective_chat.id
        
        try:
            to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
            if not to_wxid:
                await telegram_sender.send_text(chat_id, locale.command("no_binding"))
                return
            
            user_info = await wechat_contacts.get_user_info(to_wxid)
            
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
                await telegram_sender.send_text(chat_id, locale.command("receive_on"))
            else:
                await telegram_sender.send_text(chat_id, locale.command("receive_off"))
                
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")

    @staticmethod
    async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """删除联系人数据"""
        chat_id = update.effective_chat.id
        
        try:
            to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
            if not to_wxid:
                await telegram_sender.send_text(chat_id, locale.command("no_binding"))
                return
            
            unbind_result = await contact_manager.delete_contact(to_wxid)
            if unbind_result:
                await telegram_sender.send_text(chat_id, locale.command("unbind_successed"))
            else:
                await telegram_sender.send_text(chat_id, locale.common('failed'))
                
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")
    
    @staticmethod
    async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """添加联系人"""
        chat_id = update.effective_chat.id

        # 仅支持在Bot中使用该命令
        if chat_id != get_user_id():
            await telegram_sender.send_text(chat_id, locale.command("only_in_bot"))
            return
        
        scene_list = {"id": 3, "qq": 4, "group": 8, "phone": 15, "card": 17, "qr": 30}

        # 获取命令后的参数
        args = context.args  # 这是一个列表，包含命令后的所有参数
        if len(args) > 0:
            user_id = args[0]
            add_message = args[1] if len(args) > 1 else ""
            add_scene = scene_list.get(args[2], 0) if len(args) > 2 else 0
        else:
            await telegram_sender.send_text(chat_id, locale.command("no_phone"))
            return

        try:           
            search_payload = {
                "FromScene": add_scene,
                "SearchScene": 1,
                "ToUserName": str(user_id),
                "Wxid": config.MY_WXID
            }
            search_result = await wechat_api("USER_SEARCH", search_payload)

            search_data = search_result.get("Data", {})

            # 用户不存在
            if search_data.get('BaseResponse', {}).get('ret') == -4:
                await telegram_sender.send_text(chat_id, locale.command("no_user"))
                return
            
            # 用户存在
            nickname = search_data.get('NickName', {}).get('string', '')
            username = search_data.get('UserName', {}).get('string', '')
            ticket = search_data.get('AntispamTicket')
            avatar_url = search_data.get('BigHeadImgUrl') or search_data.get('SmallHeadImgUrl')

            # 已经是好友
            if not ticket:
                await telegram_sender.send_text(chat_id, locale.command("user_added"))
                return
            
            # 发送搜索结果
            if avatar_url:
                processed_photo_content = await process_avatar_from_url(avatar_url)

            callback_data = {
                "Opcode": 2,
                "Scene": add_scene,
                "V1": username,
                "V2": ticket,
                "VerifyContent": add_message,
                "Wxid": config.MY_WXID
            }

            keyboard = [
                [InlineKeyboardButton(
                    f"{locale.common('add_contact')}", 
                    callback_data=create_callback_data("add_contact", callback_data)
                )]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            send_text = f"<blockquote>{nickname}</blockquote>"
            await telegram_sender.send_photo(chat_id, processed_photo_content, send_text, reply_markup=reply_markup)
                
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")

    @staticmethod
    async def remark_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """设置好友备注"""
        chat_id = update.effective_chat.id
        message = update.message
        
        to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
        remark_name = context.args[0]

        try:
            payload = {
                "Remarks": remark_name,
                "ToWxid": to_wxid,
                "Wxid": config.MY_WXID
            }
            
            await wechat_api("USER_REMARK", payload)

            # 设置完成后更新群组信息
            await BotCommands.update_command(update, context)
            
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")

    @staticmethod
    async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """撤回消息"""
        chat_id = update.effective_chat.id
        message = update.message
        
        try:
            if not message.reply_to_message:
                await telegram_sender.send_text(chat_id, locale.command("no_reply"))
                return
            
            await revoke_by_telegram_bot_command(chat_id, message)
            
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")

    @staticmethod
    async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """执行二次登录"""
        chat_id = update.effective_chat.id
        
        try:
            relogin = await wechat_login.twice_login(config.MY_WXID)
            
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
            ["add", locale.command("add")],
            ["remark", locale.command("remark")],
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
            "add": cls.add_command,
            "remark": cls.remark_command,
            "rm": cls.revoke_command,
            "login": cls.login_command
        }
