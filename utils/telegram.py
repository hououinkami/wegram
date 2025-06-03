import logging
import os
import asyncio
import threading
from typing import Optional, Dict, Any
from telethon import TelegramClient
from telethon.tl.types import PeerUser

logger = logging.getLogger(__name__)

class TelegramClientManager:
    def __init__(self):
        self._clients: Dict[str, TelegramClient] = {}
        self._bot_entities: Dict[str, Any] = {}
        self._lock = threading.Lock()
    
    def _get_client_key(self, session_dir: str, api_id: int, api_hash: str, phone_number: str) -> str:
        """生成客户端唯一标识"""
        return f"{session_dir}_{api_id}_{phone_number}"
    
    async def get_client_and_bot(self, session_dir: str, api_id: int, api_hash: str, 
                                phone_number: str, bot_token: str) -> tuple:
        """获取客户端和bot实体（在当前事件循环中）"""
        client_key = self._get_client_key(session_dir, api_id, api_hash, phone_number)
        
        # 每次都创建新的客户端实例，避免事件循环冲突
        session_path = os.path.join(session_dir, 'tg_session')
        client = TelegramClient(session_path, api_id, api_hash)
        
        try:
            await client.start(phone=phone_number)
            logger.info(f"创建新的 Telegram 客户端连接")
            
            # 填充实体缓存
            await client.get_dialogs()
            
            # 获取 bot 实体
            bot_entity = await self._get_bot_entity(client, bot_token)
            
            return client, bot_entity
            
        except Exception as e:
            logger.error(f"创建客户端失败: {e}")
            if client.is_connected():
                await client.disconnect()
            raise e
    
    async def _get_bot_entity(self, client: TelegramClient, bot_token: str):
        """获取 bot 实体"""
        # 使用线程安全的方式检查缓存
        with self._lock:
            if bot_token in self._bot_entities:
                logger.info(f"使用缓存的 bot 实体: {bot_token.split(':')[0]}")
                return self._bot_entities[bot_token]
        
        # 如果缓存中没有，则获取
        bot_id = int(bot_token.split(':')[0])
        try:
            bot_entity = await client.get_entity(PeerUser(bot_id))
            
            # 线程安全地更新缓存
            with self._lock:
                self._bot_entities[bot_token] = bot_entity
            
            logger.info(f"获取 bot 实体成功: {bot_id}")
            return bot_entity
            
        except Exception as e:
            logger.error(f"获取 bot 实体失败: {e}")
            raise e
    
    def clear_cache(self):
        """清理缓存"""
        with self._lock:
            self._bot_entities.clear()
        logger.info("已清理 bot 实体缓存")
    
    def get_cache_info(self) -> dict:
        """获取缓存信息"""
        with self._lock:
            return {
                'cached_bots': list(self._bot_entities.keys()),
                'cache_count': len(self._bot_entities)
            }

# 全局实例
telegram_manager = TelegramClientManager()

# 为了保持向后兼容，提供原来的函数接口
async def get_telegram_client(session_dir: str, api_id: int, api_hash: str, phone_number: str) -> TelegramClient:
    """获取 Telegram 客户端（每次创建新实例）"""
    session_path = os.path.join(session_dir, 'tg_session')
    client = TelegramClient(session_path, api_id, api_hash)
    await client.start(phone=phone_number)
    await client.get_dialogs()
    logger.info("创建新的 Telegram 客户端")
    return client

async def get_bot_entity(client: TelegramClient, bot_token: str):
    """获取 bot 实体"""
    return await telegram_manager._get_bot_entity(client, bot_token)

def get_client_info() -> dict:
    """获取客户端信息"""
    return telegram_manager.get_cache_info()

def clear_cache():
    """清理缓存"""
    telegram_manager.clear_cache()