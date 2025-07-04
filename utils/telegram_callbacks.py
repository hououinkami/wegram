import logging
import json
import uuid
import time
from functools import wraps
from typing import Dict, Callable, Optional, Any
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from config import LOCALE as locale
from api.wechat_api import wechat_api

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
      
        await query.edit_message_reply_markup(reply_markup=None)  # 移除按钮
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
        "Scene": 0,
        "V1": data['V1'],
        "V2": data['V2'],
        "VerifyContent": data['VerifyContent'],
        "Wxid": data['Wxid']
    }

    try:
        await wechat_api("USER_ADD", payload)
      
        await query.edit_message_reply_markup(reply_markup=None)  # 移除按钮
        await query.answer(f"✅ 成功")
      
    except Exception as e:
        logger.error(f"❌ 添加好友失败: {e}")
        await query.answer("❌ 失敗")
