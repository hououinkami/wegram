import logging
from typing import Optional

from telethon import TelegramClient

logger = logging.getLogger(__name__)

# 全局客户端实例
client_instance: Optional['TelethonClient'] = None

class TelethonClient:
    def __init__(self, session_path: str, api_id: int, api_hash: str, 
                device_model: str = "WeGram"):
        self.session_path = session_path
        self.api_id = api_id
        self.api_hash = api_hash
        self.device_model = device_model
        self.client = None
        self.user_id = None
        self._is_initialized = False
    
    async def initialize(self):
        """初始化Telethon客户端"""
        if self._is_initialized:
            return
            
        try:
            self.client = TelegramClient(
                self.session_path, 
                self.api_id, 
                self.api_hash, 
                device_model=self.device_model
            )
            await self.client.start()
            
            me = await self.client.get_me()
            self.user_id = me.id
            self._is_initialized = True
            logger.info(f"🔗 Telethon已连接 - 用户: {me.first_name} (ID: {self.user_id})")
            
            # 更新全局实例
            global client_instance
            client_instance = self
            
        except Exception as e:
            logger.error(f"Telethon初始化失败: {e}")
            raise
    
    async def disconnect(self):
        """断开连接"""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
        self._is_initialized = False
        logger.info("🔌 Telethon客户端已断开连接")
    
    def get_client(self):
        """获取Telethon客户端"""
        return self.client
    
    def get_user_id(self):
        """获取当前用户ID"""
        return self.user_id
    
    @property
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._is_initialized

# ==================== 便捷函数 ====================
def get_user_id() -> Optional[int]:
    """获取当前用户ID"""
    global client_instance
    if client_instance and client_instance.user_id:
        return client_instance.user_id
    return None

def get_client():
    """获取Telethon客户端"""
    global client_instance
    if client_instance and client_instance.client:
        return client_instance.client
    return None

def get_client_instance() -> Optional[TelethonClient]:
    """获取客户端实例"""
    global client_instance
    return client_instance

def is_client_initialized() -> bool:
    """检查客户端是否已初始化"""
    global client_instance
    return client_instance.is_initialized if client_instance else False

async def create_client(session_path: str, api_id: int, api_hash: str, 
                       device_model: str = "WeGram") -> TelethonClient:
    """创建并初始化客户端"""
    global client_instance
    client_instance = TelethonClient(session_path, api_id, api_hash, device_model)
    await client_instance.initialize()
    return client_instance
