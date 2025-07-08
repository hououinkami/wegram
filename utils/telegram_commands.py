import asyncio
import logging
from enum import Enum
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Update
from telegram.ext import ContextTypes

import config
from config import LOCALE as locale
from api import wechat_contacts, wechat_login
from api.telegram_sender import telegram_sender
from api.wechat_api import wechat_api
from service.telethon_client import get_user_id
from utils import tools
from utils.contact_manager import contact_manager
from utils.group_manager import group_manager
from utils.telegram_callbacks import create_callback_data
from utils.telegram_to_wechat import revoke_by_telegram_bot_command

logger = logging.getLogger(__name__)

class CommandScope(Enum):
    BOT_ONLY = "bot_only"
    GROUP_ONLY = "group_only"
    CHAT_ONLY = "chat_only"
    NOT_BOT = "not_bot"
    ALL = "all"

def command_scope(scope: CommandScope):
    """装饰器：限制命令使用范围"""
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            chat_id = update.effective_chat.id
            chat_type = update.effective_chat.type
            
            # 如果是 BOT_ONLY 或 NOT_BOT，不需要查询 wxid
            if scope == CommandScope.BOT_ONLY:
                if chat_id != get_user_id():
                    await telegram_sender.send_text(chat_id, locale.command("only_in_bot"))
                    return
            elif scope == CommandScope.NOT_BOT:
                if chat_id == get_user_id():
                    await telegram_sender.send_text(chat_id, locale.command("not_in_bot"))
                    return
            elif scope in [CommandScope.GROUP_ONLY, CommandScope.CHAT_ONLY]:
                # 只有需要区分微信群聊/私聊时才查询 wxid
                try:
                    wxid = await contact_manager.get_wxid_by_chatid(chat_id)
                    if not wxid:
                        await telegram_sender.send_text(chat_id, locale.command("no_binding"))
                        return
                    
                    if scope == CommandScope.GROUP_ONLY and not wxid.endswith('@chatroom'):
                        await telegram_sender.send_text(chat_id, locale.command("only_in_group"))
                        return
                    elif scope == CommandScope.CHAT_ONLY and wxid.endswith('@chatroom'):
                        await telegram_sender.send_text(chat_id, locale.command("only_in_chat"))
                        return
                        
                except Exception as e:
                    await telegram_sender.send_text(chat_id, locale.common("failed") + f": {str(e)}")
                    return
            
            # CommandScope.ALL 不需要检查
            return await func(update, context)
        return wrapper
    return decorator

def delete_command_message(func):
    """装饰器：自动删除命令消息"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        message = update.message
        
        try:
            # 执行原始命令
            result = await func(update, context)
            return result
        finally:
            # 无论成功还是失败都删除命令消息
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            except Exception:
                pass  # 忽略删除失败的错误
    
    return wrapper

class BotCommands:
    """机器人命令处理类"""
    
    @staticmethod
    @delete_command_message
    @command_scope(CommandScope.NOT_BOT)
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
    @delete_command_message
    @command_scope(CommandScope.NOT_BOT)
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
    @delete_command_message
    @command_scope(CommandScope.NOT_BOT)
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
    @delete_command_message
    @command_scope(CommandScope.BOT_ONLY)
    async def friend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """获取联系人列表并同步到contact.json"""
        chat_id = update.effective_chat.id
        
        try:
            # 发送开始处理的消息
            logger.info("🔄 正在获取联系人列表...")
            
            # 获取联系人列表
            friend_contacts, chatroom_contacts, gh_contacts = await wechat_contacts.get_friends()
            
            if not friend_contacts:
                await telegram_sender.send_text(chat_id, "❌ 未获取到好友联系人")
                return
            
            logger.info(f"📋 获取到 {len(friend_contacts)} 个好友，正在同步信息...")
            
            # 将friend_contacts按每组20个分割
            batch_size = 20
            batches = [friend_contacts[i:i + batch_size] for i in range(0, len(friend_contacts), batch_size)]
            
            new_contacts_count = 0
            total_batches = len(batches)
            
            # 处理每个批次
            for batch_index, batch in enumerate(batches):
                try:
                    # 发送进度更新
                    if batch_index % 5 == 0 or batch_index == total_batches - 1:  # 每5个批次或最后一个批次更新进度
                        progress = f"⏳ 处理进度: {batch_index + 1}/{total_batches} 批次"
                        logger.info(progress)
                    
                    # 调用get_user_info获取用户信息
                    user_info_dict = await wechat_contacts.get_user_info(batch)
                    
                    if not user_info_dict:
                        logger.warning(f"批次 {batch_index + 1} 未获取到用户信息")
                        continue
                    
                    # 遍历用户信息
                    for wxid, user_info in user_info_dict.items():
                        if user_info is None:
                            logger.warning(f"用户 {wxid} 信息获取失败")
                            continue
                        
                        # 检查wxId是否已存在于contact.json中
                        existing_contact = await contact_manager.get_contact(wxid)
                        
                        if existing_contact is None:
                            # 不存在则创建新联系人
                            new_contact = {
                                "name": user_info.name,
                                "wxId": wxid,
                                "chatId": -9999999999,
                                "isGroup": False,
                                "isReceive": True,
                                "alias": "",
                                "avatarLink": user_info.avatar_url if user_info.avatar_url else ""
                            }
                            
                            # 添加到联系人管理器
                            contact_manager.contacts.append(new_contact)
                            contact_manager.wxid_to_contact[wxid] = new_contact
                            
                            new_contacts_count += 1
                            logger.info(f"添加新联系人: {user_info.name} ({wxid})")
                    
                    # 每处理几个批次休眠一下，避免请求过于频繁
                    if batch_index < total_batches - 1:  # 不是最后一个批次
                        await asyncio.sleep(0.5)  # 休眠500毫秒
                        
                except Exception as e:
                    logger.error(f"处理批次 {batch_index + 1} 时出错: {str(e)}")
                    continue
            
            # 保存所有更改到文件
            if new_contacts_count > 0:
                await contact_manager._save_contacts()
                success_msg = f"✅ 同步完成！新增 {new_contacts_count} 个联系人到contact.json"
            else:
                success_msg = "✅ 同步完成！所有联系人已存在，无新增联系人"
            
            logger.info(success_msg)
            
            # 发送统计信息
            stats_msg = f"""
    📊 **同步统计**
    • 总好友数: {len(friend_contacts)}
    • 新增联系人: {new_contacts_count}
    • 处理批次: {total_batches}
    • 当前联系人总数: {len(contact_manager.contacts)}
            """
            logger.info(stats_msg)
            
        except Exception as e:
            error_msg = f"❌ {locale.common('failed')}: {str(e)}"
            await telegram_sender.send_text(chat_id, error_msg)
            logger.error(f"friend_command执行失败: {str(e)}")
    
    @staticmethod
    @delete_command_message
    @command_scope(CommandScope.BOT_ONLY)
    async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """添加联系人"""
        chat_id = update.effective_chat.id        
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
                processed_photo_content = await tools.get_image_from_url(avatar_url)

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
    @delete_command_message
    @command_scope(CommandScope.NOT_BOT)
    async def remark_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """设置好友备注"""
        chat_id = update.effective_chat.id
        
        # 添加参数检查
        if not context.args or len(context.args) == 0:
            await telegram_sender.send_text(chat_id, locale.command("no_remark_name"))
            return
        
        to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
        if not to_wxid:
            await telegram_sender.send_text(chat_id, locale.command("no_binding"))
            return
    
        remark_name = context.args[0]

        try:
            payload = {
                "Remarks": remark_name,
                "ToWxid": to_wxid,
                "Wxid": config.MY_WXID
            }
            
            await wechat_api("USER_REMARK", payload)

            # 更新联系人文件
            await contact_manager.update_contact_by_chatid(chat_id, {
                "name": remark_name
            })

            # 设置完成后更新群组信息
            await BotCommands.update_command(update, context)
            
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")
    
    @staticmethod
    @delete_command_message
    @command_scope(CommandScope.GROUP_ONLY)
    async def quit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """退出群聊"""
        chat_id = update.effective_chat.id
        
        to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
        if not to_wxid:
            await telegram_sender.send_text(chat_id, locale.command("no_binding"))
            return

        try:
            payload = {
                "QID": to_wxid,
                "Wxid": config.MY_WXID
            }
            
            await wechat_api("GROUP_QUIT", payload)

            # 更新联系人文件
            await contact_manager.delete_contact(to_wxid)
            await group_manager.delete_group(to_wxid)
            
        except Exception as e:
            await telegram_sender.send_text(chat_id, f"{locale.common('failed')}: {str(e)}")

    @staticmethod
    @delete_command_message
    @command_scope(CommandScope.NOT_BOT)
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
    @delete_command_message
    @command_scope(CommandScope.BOT_ONLY)
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
            ["friend", locale.command("friend")],
            ["add", locale.command("add")],
            ["remark", locale.command("remark")],
            ["quit", locale.command("quit")],
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
            "friend": cls.friend_command,
            "add": cls.add_command,
            "remark": cls.remark_command,
            "quit": cls.quit_command,
            "rm": cls.revoke_command,
            "login": cls.login_command
        }
