import logging
import json
import uuid
import time
from functools import wraps
from typing import Dict, Callable, Optional, Any
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Update
from telegram.ext import ContextTypes

from config import LOCALE as locale
from api.wechat_api import wechat_api
from utils.contact_manager import contact_manager

logger = logging.getLogger(__name__)

class CallbackDataCache:
    """回调数据缓存管理器"""
    
    def __init__(self, default_ttl: int = 3600):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.default_ttl = default_ttl
    
    def store(self, data: Dict[str, Any], ttl: Optional[int] = None) -> str:
        """存储数据，返回唯一ID"""
        callback_id = str(uuid.uuid4())[:8]
        expire_time = time.time() + (ttl or self.default_ttl)
        
        self._cache[callback_id] = {
            'data': data,
            'expire_time': expire_time,
            'created_at': datetime.now()
        }
        
        self._cleanup_expired()
        return callback_id
    
    def get(self, callback_id: str) -> Optional[Dict[str, Any]]:
        """获取数据"""
        if callback_id not in self._cache:
            return None
            
        cache_item = self._cache[callback_id]
        
        if time.time() > cache_item['expire_time']:
            del self._cache[callback_id]
            return None
            
        return cache_item['data']
    
    def remove(self, callback_id: str):
        """删除数据"""
        self._cache.pop(callback_id, None)
    
    def _cleanup_expired(self):
        """清理过期数据"""
        current_time = time.time()
        expired_keys = [
            key for key, value in self._cache.items() 
            if current_time > value['expire_time']
        ]
        for key in expired_keys:
            del self._cache[key]

# 全局缓存实例
callback_data_cache = CallbackDataCache()

class CallbackRegistry:
    """回调注册器 - 扩展版本"""
    _handlers: Dict[str, Callable] = {}
    _pattern_handlers: Dict[str, Callable] = {}  # 新增：模式匹配处理器
    
    @classmethod
    def register(cls, callback_data: str):
        """装饰器：注册精确匹配的回调处理器"""
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
                        await query.answer("❌ 处理失败，请重试")
                        try:
                            await query.edit_message_text("❌ 处理失败，请重试")
                        except:
                            pass  # 消息可能已被删除
            return wrapper
        return decorator
    
    @classmethod
    def register_with_data(cls, action: str):
        """装饰器：注册带数据传递的回调处理器"""
        def decorator(func):
            pattern = f"{action}:"
            
            @wraps(func)
            async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
                try:
                    query = update.callback_query
                    callback_data = query.data
                    
                    # 解析回调数据
                    if not callback_data.startswith(pattern):
                        await query.answer("❌ 回调数据格式错误")
                        return
                    
                    callback_id = callback_data[len(pattern):]
                    data = callback_data_cache.get(callback_id)
                    
                    if data is None:
                        await query.answer("❌ 回调数据已过期或不存在")
                        return
                    
                    # 调用处理函数，传入解析的数据
                    return await func(update, context, data)
                    
                except Exception as e:
                    logger.error(f"回调处理器 {action} 出错: {e}")
                    query = update.callback_query
                    if query:
                        await query.answer("❌ 处理失败，请重试")
                        try:
                            await query.edit_message_text("❌ 处理失败，请重试")
                        except:
                            pass
            
            # 注册到模式处理器
            cls._pattern_handlers[pattern] = wrapper
            return wrapper
        return decorator
    
    @classmethod
    def get_handlers(cls):
        """获取所有注册的处理器"""
        return cls._handlers.copy()
    
    @classmethod
    def get_pattern_handlers(cls):
        """获取所有模式处理器"""
        return cls._pattern_handlers.copy()

def create_callback_data(action: str, data: Dict[str, Any], ttl: Optional[int] = None) -> str:
    """创建带数据的回调字符串"""
    callback_id = callback_data_cache.store(data, ttl)
    return f"{action}:{callback_id}"

class BotCallbacks:
    """Bot回调处理器类 - 扩展版本"""
    
    @staticmethod
    async def universal_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """通用回调处理器 - 支持模式匹配"""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        
        # 1. 先检查精确匹配
        handlers = CallbackRegistry.get_handlers()
        if callback_data in handlers:
            await handlers[callback_data](update, context)
            return
        
        # 2. 检查模式匹配
        pattern_handlers = CallbackRegistry.get_pattern_handlers()
        for pattern, handler in pattern_handlers.items():
            if callback_data.startswith(pattern):
                await handler(update, context)
                return
        
        # 3. 未找到处理器
        logger.warning(f"未找到回调处理器: {callback_data}")
        await query.edit_message_text("❌ 未知操作")
    
    @staticmethod
    def get_callback_handlers():
        """获取回调处理器配置"""
        return {
            ".*": BotCallbacks.universal_callback_handler,
        }

# 使用装饰器注册回调处理器
# 1. 保持原有的简单回调处理器不变
@CallbackRegistry.register("simple_action")
async def handle_simple_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
  """简单回调处理器 - 无需数据传递"""
  query = update.callback_query
  await query.edit_message_text("✅ 简单操作完成！")

# 2. 新的带数据传递的回调处理器
@CallbackRegistry.register_with_data("agree_accept")
async def handle_agree_accept(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]):
    """处理接受好友按钮"""
    query = update.callback_query
    
    # 直接使用传入的数据
    payload = {
        "Scene": data['Scene'],
        "V1": data['V1'],
        "V2": data['V2'],
        "Wxid": data['Wxid']
    }

    try:
        await wechat_api("USER_PASS", payload)

        new_keyboard = [
            [InlineKeyboardButton(locale.common("accept_successed"), callback_data="_")]
        ]
        new_reply_markup = InlineKeyboardMarkup(new_keyboard)

        await query.edit_message_reply_markup(reply_markup=new_reply_markup)
        await query.answer(f"✅ 成功")
      
    except Exception as e:
        logger.error(f"❌ 通过好友请求失败: {e}")
        await query.answer("❌ 失敗")

@CallbackRegistry.register_with_data("add_contact")
async def handle_add_contact(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]):
    """处理添加好友按钮"""
    query = update.callback_query

    if not data['V2']:
        return
    
    # 直接使用传入的数据
    payload = {
        "Opcode": 2,
        "Scene": data['Scene'],
        "V1": data['V1'],
        "V2": data['V2'],
        "VerifyContent": data['VerifyContent'],
        "Wxid": data['Wxid']
    }

    try:
        await wechat_api("USER_ADD", payload)
      
        new_keyboard = [
            [InlineKeyboardButton(locale.common("request_successed"), callback_data="_")]
        ]
        new_reply_markup = InlineKeyboardMarkup(new_keyboard)

        await query.edit_message_reply_markup(reply_markup=new_reply_markup)
        await query.answer(f"✅ 成功")
      
    except Exception as e:
        logger.error(f"❌ 添加好友失败: {e}")
        await query.answer("❌ 失敗")

@CallbackRegistry.register_with_data("add_wecom_contact")
async def handle_add_contact(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]):
    """处理添加企业微信好友按钮"""
    query = update.callback_query

    if not data['V1']:
        return
    
    # 尝试直接添加
    add_payload = {
        "Username": data['Username'],
        "V1": data['V1'],
        "Wxid": data['Wxid']
    }
    add_result = await wechat_api("WECOM_ADD", add_payload)

    # 若直接添加失败则发送好友申请
    if add_result.get("Data", {}).get('BaseResponse', {}).get('ret') == -44:
        new_payload = {
            "Context": "",
            "Username": data['Username'],
            "V1": data['V1'],
            "Wxid": data['Wxid']
        }
        await wechat_api("WECOM_APPLY", new_payload)

    try:      
        new_keyboard = [
            [InlineKeyboardButton(locale.common("request_successed"), callback_data="_")]
        ]
        new_reply_markup = InlineKeyboardMarkup(new_keyboard)

        await query.edit_message_reply_markup(reply_markup=new_reply_markup)
        await query.answer(f"✅ 成功")
      
    except Exception as e:
        logger.error(f"❌ 添加好友失败: {e}")
        await query.answer("❌ 失敗")

@CallbackRegistry.register_with_data("contact_page")
async def handle_contact_page(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]):
    """处理联系人列表分页回调"""
    query = update.callback_query
    page = data.get("page", 0)
    
    try:
        # 直接调用 BotCommands 的方法来构建页面数据
        from utils.telegram_commands import BotCommands
        
        message_text, reply_markup = await BotCommands.build_contacts_page_data(page)
        
        if reply_markup is None:
            await query.edit_message_text(message_text, reply_markup=None)
        else:
            await query.edit_message_text(message_text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"处理联系人分页失败: {e}")
        await query.answer(f"❌ 操作失败: {str(e)}", show_alert=True)

@CallbackRegistry.register_with_data("contact_info")
async def handle_contact_info(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]):
    """处理联系人信息查看回调"""
    query = update.callback_query
    
    try:
        # 构建联系人详细信息
        wxid = data.get('wxid', '')
        name = data.get('name', wxid)
        chat_id = data.get('chat_id', '')
        alias = data.get('alias', '') or ''
        is_group = data.get('is_group', False)
        is_receive = data.get('is_receive', True)
        avatar_url = data.get('avatar_url', '') or ''
        
        contact_info = f"👤 {name}"
        
        # 构建操作按钮
        keyboard = []
        
        # 第一行：聊天和接收状态
        first_row = []
        
        # 如果有有效的chatId，添加"前往聊天"按钮
        if chat_id and chat_id != -9999999999:
            first_row.append(InlineKeyboardButton(
                    f"{locale.command('group_binded')}", 
                    callback_data="page_info"
                ))
        else:
            bind_data = {
                "wxid": wxid,
                "name": name,
                "avatar_url": avatar_url
            }
            first_row.append(InlineKeyboardButton(
                f"{locale.command('group_binding')}", 
                callback_data=create_callback_data("group_binding", bind_data)
            ))
        
        # 切换接收状态按钮
        receive_text = f"{locale.command('receive_off')}" if is_receive else f"{locale.command('receive_on')}"
        receive_emoji = "🔕" if is_receive else "🔔"
        toggle_data = {
            "wxid": wxid,
            "current_receive": is_receive
        }
        first_row.append(InlineKeyboardButton(
            f"{receive_emoji} {receive_text}",
            callback_data=create_callback_data("toggle_receive", toggle_data)
        ))
        
        if first_row:
            keyboard.append(first_row)
        
        # 第二行：管理操作
        second_row = []
        
        # 编辑联系人按钮（如果需要的话）
        edit_data = {
            "wxid": wxid,
            "name": name,
            "alias": alias
        }
        second_row.append(InlineKeyboardButton(
            f"{locale.command('edit_contact')}",
            callback_data=create_callback_data("page_info", edit_data)
        ))
        
        # 删除联系人按钮
        delete_data = {
            "wxid": wxid,
            "name": name
        }
        second_row.append(InlineKeyboardButton(
            f"{locale.command('delete_contact')}",
            callback_data=create_callback_data("delete_contact", delete_data)
        ))
        
        keyboard.append(second_row)
        
        # 第三行：返回按钮
        keyboard.append([
            InlineKeyboardButton(
                f"{locale.command('back')}",
                callback_data=create_callback_data("contact_page", {"page": 0})
            )
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # 编辑消息显示联系人详情
        await query.edit_message_text(contact_info, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"获取联系人信息失败: {e}")
        await query.answer(f"❌ 获取联系人信息失败: {str(e)}", show_alert=True)

@CallbackRegistry.register_with_data("group_binding")
async def handle_group_binding(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]):
    """处理群组绑定回调"""
    query = update.callback_query
    
    try:
        wxid = data.get('wxid')
        name = data.get('name', wxid)
        avatar_url = data.get('avatar_url', '')
        
        if not wxid:
            await query.answer("❌ 联系人ID无效", show_alert=True)
            return
        
        await query.answer("🔄 正在创建群组...")
        
        # 创建群组
        result = await contact_manager.create_group_for_contact_async(
            wxid=wxid,
            contact_name=name,
            avatar_url=avatar_url
        )
        
        if result:
            # 简单替换：直接查找包含特定文本的按钮并替换
            current_markup = query.message.reply_markup
            if current_markup:
                new_keyboard = []
                for row in current_markup.inline_keyboard:
                    new_row = []
                    for button in row:
                        if button.text == locale.command('group_binding'):
                            # 找到目标按钮，替换它
                            new_button = InlineKeyboardButton(
                                f"{locale.command('group_binded')}", 
                                callback_data="page_info"
                            )
                            new_row.append(new_button)
                        else:
                            new_row.append(button)
                    new_keyboard.append(new_row)
                
                new_reply_markup = InlineKeyboardMarkup(new_keyboard)
                await query.edit_message_reply_markup(reply_markup=new_reply_markup)
            
            await query.answer("✅ 群组创建成功！")
        else:
            await query.answer("❌ 群组创建失败", show_alert=True)
            
    except Exception as e:
        logger.error(f"群组绑定失败: {e}")
        await query.answer(f"❌ 操作失败: {str(e)}", show_alert=True)

@CallbackRegistry.register_with_data("toggle_receive")
async def handle_toggle_receive(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]):
    """处理切换接收状态回调"""
    query = update.callback_query
    
    try:
        wxid = data.get('wxid')
        current_receive = data.get('current_receive', True)
        
        if not wxid:
            await query.answer("❌ 联系人ID无效", show_alert=True)
            return
        
        # 获取联系人信息
        contact = await contact_manager.get_contact(wxid)
        if not contact:
            await query.answer("❌ 联系人不存在", show_alert=True)
            return
        
        chat_id = contact.get('chatId')
        if not chat_id:
            await query.answer("❌ 联系人未绑定Telegram聊天", show_alert=True)
            return
        
        # 切换接收状态
        await contact_manager.update_contact_by_chatid(chat_id, {"isReceive": "toggle"})
        
        # 获取更新后的状态
        updated_contact = await contact_manager.get_contact(wxid)
        new_receive_status = updated_contact.get('isReceive', True)
        
        # 显示操作结果
        status_text = "✅ 已开启消息接收" if new_receive_status else "🔕 已关闭消息接收"
        await query.answer(status_text)
        
        # 只更新键盘，不重新构建整个消息
        current_markup = query.message.reply_markup
        if current_markup and current_markup.inline_keyboard:
            # 复制现有的键盘
            new_keyboard = []
            
            for row in current_markup.inline_keyboard:
                new_row = []
                for button in row:
                    # 检查是否是接收状态按钮
                    if button.callback_data and "toggle_receive:" in button.callback_data:
                        # 更新接收状态按钮
                        receive_text = locale.command('receive_off') if new_receive_status else locale.command('receive_on')
                        receive_emoji = "🔕" if new_receive_status else "🔔"
                        toggle_data = {
                            "wxid": wxid,
                            "current_receive": new_receive_status
                        }
                        new_button = InlineKeyboardButton(
                            f"{receive_emoji} {receive_text}",
                            callback_data=create_callback_data("toggle_receive", toggle_data)
                        )
                        new_row.append(new_button)
                    else:
                        # 保持其他按钮不变
                        new_row.append(button)
                new_keyboard.append(new_row)
            
            new_reply_markup = InlineKeyboardMarkup(new_keyboard)
            await query.edit_message_reply_markup(reply_markup=new_reply_markup)
        
    except Exception as e:
        logger.error(f"切换接收状态失败: {e}")
        await query.answer(f"❌ 操作失败: {str(e)}", show_alert=True)

@CallbackRegistry.register_with_data("delete_contact")
async def handle_delete_contact(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]):
    """处理删除联系人回调"""
    query = update.callback_query
    
    try:
        wxid = data.get('wxid')
        name = data.get('name', wxid)
        
        if not wxid:
            await query.answer("❌ 联系人ID无效", show_alert=True)
            return
        
        # 显示确认删除界面
        confirm_text = f"""⚠️ **确认删除联系人**"""
      
        # 确认删除的键盘
        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ 确认删除",
                    callback_data=create_callback_data("confirm_delete", {"wxid": wxid, "name": name})
                ),
                InlineKeyboardButton(
                    "❌ 取消删除",
                    callback_data=create_callback_data("contact_info", data)
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 返回列表",
                    callback_data=create_callback_data("contact_page", {"page": 0})
                )
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(confirm_text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"显示删除确认失败: {e}")
        await query.answer(f"❌ 操作失败: {str(e)}", show_alert=True)

@CallbackRegistry.register_with_data("confirm_delete")
async def handle_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]):
    """处理确认删除联系人回调"""
    query = update.callback_query
    
    try:
        wxid = data.get('wxid')
        name = data.get('name', wxid)
        
        if not wxid:
            await query.answer("❌ 联系人ID无效", show_alert=True)
            return
        
        # 执行删除操作
        success = await contact_manager.delete_contact(wxid)
        
        if success:
            await query.answer(f"✅ 已删除联系人: {name}")
            
            # 显示删除成功页面
            success_text = f"""✅ **删除成功**"""
          
            # 成功页面的键盘
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🔙 返回列表",
                        callback_data=create_callback_data("contact_page", {"page": 0})
                    ),
                    InlineKeyboardButton(
                        "🔄 刷新列表",
                        callback_data=create_callback_data("contact_page", {"page": 0})
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(success_text, reply_markup=reply_markup)
        else:
            await query.answer("❌ 删除失败，请稍后重试", show_alert=True)
        
    except Exception as e:
        logger.error(f"确认删除联系人失败: {e}")
        await query.answer(f"❌ 删除失败: {str(e)}", show_alert=True)

@CallbackRegistry.register("page_info")
async def handle_page_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理页面信息按钮（不执行任何操作）"""
    query = update.callback_query
    await query.answer("📄 当前页面信息")  # 只是确认点击，显示提示