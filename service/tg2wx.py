import asyncio
import logging
import requests
from telethon import TelegramClient, events
import os
from typing import Dict
import config
from utils.sender import process_telegram_update, process_telethon_update, revoke_message

logger = logging.getLogger(__name__)

class MonitorMode:
  POLLING_ONLY = "polling"
  TELETHON_ONLY = "telethon"
  HYBRID = "hybrid"

class IntegratedTelegramMonitor:
    def __init__(self, session_path: str, bot_token: str, api_id: int, api_hash: str, 
                device_model: str = "WeGram", mode: str = MonitorMode.HYBRID):
        self.session_path = session_path
        self.bot_token = bot_token
        self.api_id = api_id
        self.api_hash = api_hash
        self.device_model = device_model
        self.mode = mode
        self.client = None
        self.user_id = None
        self.is_running = False
        self.polling_offset = None
        
        # 群组缓存：记录已检查过的群组
        self.chat_cache: Dict[int, bool] = {}
        self.target_bot_id = self.bot_token.split(':')[0] if ':' in self.bot_token else None
        
        # 验证监控模式
        if self.mode not in [MonitorMode.POLLING_ONLY, MonitorMode.TELETHON_ONLY, MonitorMode.HYBRID]:
            logger.warning(f"无效的监控模式: {self.mode}，使用默认混合模式")
            self.mode = MonitorMode.HYBRID
    
    def _get_mode_description(self) -> str:
        descriptions = {
            MonitorMode.POLLING_ONLY: "仅轮询模式 - 只处理新消息",
            MonitorMode.TELETHON_ONLY: "仅Telethon模式 - 处理新消息和删除消息",
            MonitorMode.HYBRID: "混合模式 - 轮询处理新消息，Telethon处理删除消息"
        }
        return descriptions.get(self.mode, "未知模式")
    
    async def initialize(self):
        """初始化客户端"""
        try:
            self.client = TelegramClient(self.session_path, self.api_id, self.api_hash, 
                                        device_model=self.device_model)
            await self.client.start()
            
            me = await self.client.get_me()
            self.user_id = me.id
            logger.info(f"已登录用户: {me.first_name} (ID: {self.user_id})")
            
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            raise
    
    # ==================== 轮询相关 ====================
    def get_updates_sync(self, offset=None):
        """同步获取更新"""
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {"timeout": 30}
        if offset:
            params["offset"] = offset
        
        try:
            response = requests.get(url, params=params)
            result = response.json()
            return result
        except Exception as e:
            logger.error(f"获取更新出错: {e}")
            return {"ok": False}
    
    async def polling_loop(self):
        """轮询循环"""
        if self.mode == MonitorMode.TELETHON_ONLY:
            return
            
        logger.info("🔄 启动消息轮询...")
        
        while self.is_running:
            try:
                loop = asyncio.get_event_loop()
                updates = await loop.run_in_executor(None, self.get_updates_sync, self.polling_offset)
                
                if updates.get("ok", False):
                    results = updates.get("result", [])
                                        
                    for update in results:
                        self.polling_offset = update["update_id"] + 1
                        await self.process_polling_update(update)
                else:
                    logger.warning(f"轮询响应异常: {updates}")
                
                await asyncio.sleep(getattr(config, 'POLLING_INTERVAL', 1))
                
            except Exception as e:
                logger.error(f"轮询出错: {e}", exc_info=True)
                await asyncio.sleep(getattr(config, 'POLLING_INTERVAL', 1))
    
    async def process_polling_update(self, update):
        """处理轮询更新"""
        try:
                        
            if 'message' not in update:
                    return
            
            message_data = update['message']
            chat = message_data.get('chat', {})
            from_user = message_data.get('from', {})
                        
            # 只处理群组消息且是当前用户发送的
            if (chat.get('type') not in ['group', 'supergroup'] or 
                from_user.get('id') != self.user_id):
                return
            
            chat_id = chat.get('id')
                        
            if await self.check_bot_in_chat(chat_id):
                logger.warning(f"调试：：：：：：{update}")
                try:
                    await process_telegram_update(update)
                except Exception as e:
                    logger.error(f"❌ [轮询] 消息处理失败: {e}", exc_info=True)
            
        except Exception as e:
            logger.error(f"处理轮询更新出错: {e}", exc_info=True)
    
    # ==================== 群组检查 ====================
    async def check_bot_in_chat(self, chat_id: int) -> bool:
        """检查群组是否包含目标BOT"""
        if chat_id in self.chat_cache:
                return self.chat_cache[chat_id]
        
        try:
            chat = await self.client.get_entity(chat_id)
            
            # 跳过私聊
            if not hasattr(chat, 'participants_count'):
                self.chat_cache[chat_id] = False
                return False
            
            # 简化检查：只用一种方法
            has_bot = await self._check_participants(chat)
            self.chat_cache[chat_id] = has_bot
            return has_bot
                    
        except Exception as e:
            self.chat_cache[chat_id] = False
            return False
    
    async def _check_participants(self, chat) -> bool:
        """检查群组成员"""
        try:
            # 分批检查，避免大群组问题
            participants = await self.client.get_participants(chat, limit=500)
            
            for participant in participants:
                if (participant.bot and self.target_bot_id and 
                    str(participant.id) == self.target_bot_id):
                    return True
            
            return False
            
        except Exception as e:
            return False
    
    # ==================== 事件处理 ====================
    async def start_monitoring(self):
        """开始监控"""
        if not self.client:
            await self.initialize()
        
        self.is_running = True
        logger.info(f"🚀 开始监控 - {self._get_mode_description()}")
        
        # 设置事件处理器
        if self.mode != MonitorMode.POLLING_ONLY:
            # @self.client.on(events.NewMessage)
            # async def handle_new_message(event):
            #     logger.warning(f"调试：：：：：：{event}")
            # 新消息事件（仅纯Telethon模式）
            if self.mode == MonitorMode.TELETHON_ONLY:
                @self.client.on(events.NewMessage)
                async def handle_new_message(event):
                    await self.process_telethon_message(event)
                logger.info("📝 已启用Telethon新消息监听")
            
            # 删除消息事件
            @self.client.on(events.MessageDeleted)
            async def handle_deleted_message(event):
                await revoke_message(event)
            logger.info("🗑️已启用消息删除监听")
        
        # 启动轮询任务
        polling_task = None
        if self.mode != MonitorMode.TELETHON_ONLY:
            polling_task = asyncio.create_task(self.polling_loop())
            logger.info("🔄 轮询任务已启动")
        
        try:
            # 同时等待轮询任务和客户端运行
            if polling_task:
                await asyncio.gather(
                    polling_task,
                    self.client.run_until_disconnected(),
                    return_exceptions=True
                )
            else:
                await self.client.run_until_disconnected()
        except Exception as e:
            logger.error(f"监控运行出错: {e}")
        finally:
            self.is_running = False
            if polling_task and not polling_task.done():
                polling_task.cancel()
    
    async def process_telethon_message(self, event):
        """处理Telethon新消息"""
        try:
            if event.sender_id != self.user_id or not event.is_group:
                return
            
            if await self.check_bot_in_chat(event.chat_id):
                message = event.message
                chat = await event.get_chat()
                
                await process_telethon_update(message, chat, self.client)
            
        except Exception as e:
            logger.error(f"处理Telethon消息出错: {e}")
    
    def clear_cache(self):
        """清空缓存"""
        self.chat_cache.clear()
        logger.info("已清空群组缓存")

# ==================== 全局函数 ====================
monitor_instance = None

def get_monitor_mode():
    """获取监控模式"""
    return getattr(config, 'MONITOR_MODE', MonitorMode.HYBRID)

def main():
    """主函数"""
    try:
        # 配置参数
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        SESSION_PATH = os.path.join(project_root, 'sessions', 'tg_session')
        
        # 检查session文件
        if not os.path.exists(SESSION_PATH + '.session'):
            logger.error(f"Session文件不存在: {SESSION_PATH}.session")
            return
        
        # 创建监控器
        global monitor_instance
        monitor_instance = IntegratedTelegramMonitor(
            SESSION_PATH, config.BOT_TOKEN, config.API_ID, config.API_HASH, 
            config.DEVICE_MODEL, get_monitor_mode()
        )
        
        # 运行监控
        asyncio.run(monitor_instance.start_monitoring())
        
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止...")
    except Exception as e:
        logger.error(f"监控失败: {e}")

def get_client():
    """获取监控器实例"""
    return monitor_instance

if __name__ == "__main__":
    main()