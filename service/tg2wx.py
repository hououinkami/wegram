import asyncio
import logging
import requests
import time
import threading
from telethon import TelegramClient, events
from telethon.tl.types import MessageService, MessageActionChatAddUser, MessageActionChatDeleteUser
from telethon.errors import SessionPasswordNeededError, FloodWaitError
import os
from typing import Set, Optional, Dict
import config
from utils.sender import process_telegram_update, process_telethon_update, revoke_message

# 配置日志
logger = logging.getLogger(__name__)

# 监控模式枚举
class MonitorMode:
    POLLING_ONLY = "polling"      # 仅轮询
    TELETHON_ONLY = "telethon"    # 仅Telethon事件
    HYBRID = "hybrid"             # 混合模式（默认）

class IntegratedTelegramMonitor:
    def __init__(self, session_path: str, bot_token: str, api_id: int, api_hash: str, device_model: str = "WeGram", mode: str = MonitorMode.HYBRID):
        """
        初始化整合的Telegram监控器
        
        Args:
            session_path: session文件路径
            bot_token: 要监控的BOT TOKEN
            api_id: Telegram API ID
            api_hash: Telegram API Hash
            mode: 监控模式 ('polling', 'telethon', 'hybrid')
        """
        self.session_path = session_path
        self.bot_token = bot_token
        self.api_id = api_id
        self.api_hash = api_hash
        self.device_model = device_model
        self.mode = mode
        self.client = None
        self.user_id = None
        self.is_running = False
        self.bot_entity = None
        
        # 轮询相关
        self.polling_offset = None
        self.polling_task = None
        
        # 群组缓存：记录已检查过的群组
        self.chat_cache: Dict[int, bool] = {}  # chat_id -> has_target_bot
        
        # 从BOT TOKEN提取BOT ID
        self.target_bot_id = self.bot_token.split(':')[0] if ':' in self.bot_token else None
        
        # 验证监控模式
        if self.mode not in [MonitorMode.POLLING_ONLY, MonitorMode.TELETHON_ONLY, MonitorMode.HYBRID]:
            logger.warning(f"无效的监控模式: {self.mode}，使用默认混合模式")
            self.mode = MonitorMode.HYBRID
        
    def _get_mode_description(self) -> str:
        """获取模式描述"""
        descriptions = {
            MonitorMode.POLLING_ONLY: "仅轮询模式 - 只处理新消息",
            MonitorMode.TELETHON_ONLY: "仅Telethon模式 - 处理新消息和删除消息",
            MonitorMode.HYBRID: "混合模式 - 轮询处理新消息，Telethon处理删除消息"
        }
        return descriptions.get(self.mode, "未知模式")
    
    def is_polling_enabled(self) -> bool:
        """检查是否启用轮询"""
        return self.mode in [MonitorMode.POLLING_ONLY, MonitorMode.HYBRID]
    
    def is_telethon_events_enabled(self) -> bool:
        """检查是否启用Telethon事件监听"""
        return self.mode in [MonitorMode.TELETHON_ONLY, MonitorMode.HYBRID]
    
    def is_telethon_new_message_enabled(self) -> bool:
        """检查是否启用Telethon新消息事件"""
        # 只有在纯Telethon模式下才启用新消息事件
        return self.mode == MonitorMode.TELETHON_ONLY
        
    async def initialize(self):
        """初始化客户端并获取用户信息"""
        try:
            # 使用已有的session文件创建客户端
            self.client = TelegramClient(
                self.session_path,
                self.api_id,
                self.api_hash,
                device_model=self.device_model
            )
            await self.client.start()
            
            # 获取当前用户ID
            me = await self.client.get_me()
            self.user_id = me.id
            logger.info(f"已登录用户: {me.first_name} (ID: {self.user_id})")

            # 添加：获取bot实体
            if self.target_bot_id:
                try:
                    self.bot_entity = await self.client.get_entity(int(self.target_bot_id))
                    logger.info(f"已获取Bot实体: {getattr(self.bot_entity, 'username', 'Unknown')}")
                except Exception as e:
                    logger.error(f"获取Bot实体失败: {e}")
                    self.bot_entity = None
            
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            raise
    
    # ==================== 轮询相关方法 ====================
    
    def get_updates_sync(self, offset=None):
        """同步获取Telegram消息更新"""
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {"timeout": 30}
        if offset:
            params["offset"] = offset
        
        try:
            response = requests.get(url, params=params)
            return response.json()
        except Exception as e:
            logger.error(f"获取Telegram更新时出错: {e}")
            return {"ok": False, "error": str(e)}
    
    async def polling_loop(self):
        """轮询循环 - 处理新消息"""
        if not self.is_polling_enabled():
            logger.info("📴 轮询功能已禁用")
            return
            
        logger.info("🔄 启动消息轮询循环...")
        
        while self.is_running:
            try:
                # 在异步环境中调用同步的HTTP请求
                loop = asyncio.get_event_loop()
                updates = await loop.run_in_executor(
                    None, 
                    self.get_updates_sync, 
                    self.polling_offset
                )
                
                if updates.get("ok", False):
                    results = updates.get("result", [])
                    
                    for update in results:
                        # 更新offset为最新消息的ID+1
                        self.polling_offset = update["update_id"] + 1
                        
                        # 处理轮询获取的消息
                        await self.process_polling_update(update)
                else:
                    logger.error(f"轮询获取更新失败: {updates}")
                
                # 短暂休眠，避免过于频繁的请求
                await asyncio.sleep(config.POLLING_INTERVAL)
                
            except Exception as e:
                logger.error(f"轮询过程中出错: {e}")
                await asyncio.sleep(config.POLLING_INTERVAL)
    
    async def process_polling_update(self, update):
        """处理轮询获取的更新"""
        try:
            # 检查是否包含消息
            if 'message' not in update:
                return
            
            message_data = update['message']
            
            # 检查是否是群组消息
            chat = message_data.get('chat', {})
            chat_type = chat.get('type', '')
            
            if chat_type not in ['group', 'supergroup']:
                return
            
            # 检查发送者是否是当前用户
            sender = message_data.get('from', {})
            sender_id = sender.get('id')
            
            if sender_id != self.user_id:
                return
            
            chat_id = chat.get('id')
            chat_title = chat.get('title', f'Chat_{chat_id}')
            
            # 检查这个群组是否包含目标BOT
            if await self.check_bot_in_chat(chat_id):
                logger.info(f"📨 [轮询] 检测到来自 {chat_title} 的消息，调用处理函数")
                # 调用外部处理函数
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    process_telegram_update,
                    update
                )
            else:
                logger.debug(f"❌ [轮询] 群组 {chat_title} 不包含目标BOT，跳过")
            
        except Exception as e:
            logger.error(f"处理轮询更新时出错: {e}")
    
    # ==================== 群组检查相关方法 ====================
    
    async def check_bot_in_chat(self, chat_id: int, force_check: bool = False) -> bool:
        """
        检查指定群组是否包含目标BOT
        
        Args:
            chat_id: 群组ID
            force_check: 是否强制检查（忽略缓存）
            
        Returns:
            bool: 是否包含目标BOT
        """
        # 检查缓存
        if not force_check and chat_id in self.chat_cache:
            return self.chat_cache[chat_id]
        
        try:
            # 获取群组信息
            chat = await self.client.get_entity(chat_id)
            chat_title = getattr(chat, 'title', f'Chat_{chat_id}')
            
            # 跳过私聊，直接返回False
            if not (hasattr(chat, 'megagroup') or hasattr(chat, 'broadcast') or hasattr(chat, 'participants_count')):
                self.chat_cache[chat_id] = False
                return False
            
            # 方法1: 尝试获取所有群组成员
            has_bot = await self._check_participants_full(chat, chat_title)
            
            # 如果方法1失败，尝试方法2
            if not has_bot:
                has_bot = await self._check_participants_batched(chat, chat_title)
            
            # 如果方法2也失败，尝试方法3
            if not has_bot:
                has_bot = await self._check_by_search(chat, chat_title)
            
            # 缓存结果
            self.chat_cache[chat_id] = has_bot
            
            return has_bot
                    
        except Exception as e:
            logger.warning(f"检查群组 {chat_id} 时出错: {e}")
            # 出错时缓存为False，避免重复检查
            self.chat_cache[chat_id] = False
            return False
    
    async def _check_participants_full(self, chat, chat_title: str) -> bool:
        """方法1: 获取所有成员检查"""
        try:
            logger.debug(f"方法1: 获取群组 {chat_title} 的所有成员...")
            participants = await self.client.get_participants(chat)
            
            for participant in participants:
                if participant.bot and self.is_target_bot(participant):
                    logger.info(f"🎯 在 {chat_title} 中找到目标BOT: {getattr(participant, 'username', 'Unknown')}")
                    return True
            
            return False
            
        except FloodWaitError as e:
            logger.warning(f"方法1遇到速率限制: {e}")
            return False
        except Exception as e:
            logger.debug(f"方法1失败: {e}")
            return False
    
    async def _check_participants_batched(self, chat, chat_title: str) -> bool:
        """方法2: 分批检查成员"""
        try:
            logger.debug(f"方法2: 分批检查群组 {chat_title} 的成员...")
            
            # 分批获取成员，每批200个
            offset = 0
            batch_size = 200
            max_batches = 10  # 最多检查10批（2000个成员）
            
            for batch in range(max_batches):
                try:
                    participants = await self.client.get_participants(
                        chat, 
                        limit=batch_size, 
                        offset=offset
                    )
                    
                    if not participants:
                        break
                    
                    logger.debug(f"检查第 {batch + 1} 批成员 ({len(participants)} 个)")
                    
                    for participant in participants:
                        if participant.bot and self.is_target_bot(participant):
                            logger.info(f"🎯 在 {chat_title} 第 {batch + 1} 批中找到目标BOT")
                            return True
                    
                    offset += batch_size
                    
                    # 如果这批成员少于batch_size，说明已经到底了
                    if len(participants) < batch_size:
                        break
                    
                    # 避免API限制，稍微延迟
                    await asyncio.sleep(0.1)
                    
                except FloodWaitError as e:
                    logger.warning(f"方法2遇到速率限制: {e}")
                    break
                except Exception as e:
                    logger.debug(f"方法2批次 {batch + 1} 失败: {e}")
                    break
            
            return False
            
        except Exception as e:
            logger.debug(f"方法2失败: {e}")
            return False
    
    async def _check_by_search(self, chat, chat_title: str) -> bool:
        """方法3: 通过搜索BOT用户名检查"""
        try:
            if not self.target_bot_id:
                return False
            
            logger.debug(f"方法3: 在群组 {chat_title} 中搜索BOT...")
            
            # 尝试直接获取BOT实体
            try:
                bot_entity = await self.client.get_entity(int(self.target_bot_id))
                
                # 检查BOT是否在这个群组中
                try:
                    # 尝试获取BOT在群组中的信息
                    participant = await self.client.get_participants(
                        chat, 
                        search=bot_entity.username if hasattr(bot_entity, 'username') else str(self.target_bot_id)
                    )
                    
                    if participant:
                        logger.info(f"🎯 通过搜索在 {chat_title} 中找到目标BOT")
                        return True
                        
                except:
                    pass
                
            except Exception as e:
                logger.debug(f"无法获取BOT实体: {e}")
            
            return False
            
        except Exception as e:
            logger.debug(f"方法3失败: {e}")
            return False
    
    def is_target_bot(self, participant) -> bool:
        """
        判断是否是目标BOT
        """
        try:
            # 方法1: 通过BOT ID匹配
            if self.target_bot_id and str(participant.id) == self.target_bot_id:
                return True
            
            # 方法2: 通过用户名匹配（如果配置中有BOT用户名）
            if hasattr(config, 'BOT_USERNAME') and hasattr(participant, 'username'):
                if participant.username and participant.username.lower() == config.BOT_USERNAME.lower():
                    return True
            
            # 方法3: 通过显示名称匹配（如果配置中有）
            if hasattr(config, 'BOT_NAME') and hasattr(participant, 'first_name'):
                if participant.first_name and participant.first_name == config.BOT_NAME:
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"检查BOT时出错: {e}")
            return False
    
    # ==================== 事件处理方法 ====================
    
    async def start_monitoring(self):
        """开始监控消息"""
        if not self.client:
            await self.initialize()
        
        self.is_running = True
        logger.info("🚀 开始监控服务...")
        logger.info(f"🎛️ 当前模式: {self._get_mode_description()}")
        
        # 根据模式启动相应的监控功能
        if self.is_polling_enabled():
            logger.info("📡 启动轮询功能...")
            self.polling_task = asyncio.create_task(self.polling_loop())
        
        if self.is_telethon_events_enabled():
            logger.info("🎧 启动Telethon事件监听...")
            
            # 监听新消息事件（仅在纯Telethon模式下）
            if self.is_telethon_new_message_enabled():
                @self.client.on(events.NewMessage)
                async def handle_new_message(event):
                    await self.process_telethon_new_message(event)
                logger.info("📝 已启用Telethon新消息监听")
            
            # 监听消息删除事件
            @self.client.on(events.MessageDeleted)
            async def handle_deleted_message(event):
                # await self.process_deleted_message(event)
                 await revoke_message(event)
            logger.info("🗑️ 已启用消息删除监听")
        
        # 保持客户端运行
        try:
            await self.client.run_until_disconnected()
        except Exception as e:
            logger.error(f"监控循环出错: {e}")
        finally:
            await self.stop()
    
    async def process_telethon_new_message(self, event):
        """处理Telethon新消息事件（仅在纯Telethon模式下使用）"""
        try:
            # 检查是否是当前用户发送的消息
            if event.sender_id != self.user_id:
                return
            
            # 检查是否在群组中（排除私聊）
            if not event.is_group:
                return
            
            # 获取消息和群组信息
            message = event.message
            chat = await event.get_chat()
            
            # 检查这个群组是否包含目标BOT
            if await self.check_bot_in_chat(event.chat_id):
                logger.info(f"📨 [Telethon] 检测到来自 {chat.title} 的消息，调用处理函数")
                await process_telethon_update(message, chat, self.client)
            else:
                logger.debug(f"❌ [Telethon] 群组 {chat.title} 不包含目标BOT，跳过")
            
        except Exception as e:
            logger.error(f"处理Telethon新消息时出错: {e}")
    
    async def process_deleted_message(self, event):
        logger.warning(f"{event}")
        """处理消息删除事件"""
        try:
            logger.info(f"🗑️ 检测到消息删除:")
            logger.info(f"   删除的消息ID: {event.deleted_ids}")
            logger.info(f"   聊天ID: {event.chat_id if hasattr(event, 'chat_id') else 'Unknown'}")
            
            # 检查是否在包含目标BOT的群组中
            if hasattr(event, 'chat_id') and event.chat_id:
                if await self.check_bot_in_chat(event.chat_id):
                    logger.info(f"🎯 在目标群组中检测到消息删除，准备处理...")
                    
                    # 获取群组信息
                    try:
                        chat = await self.client.get_entity(event.chat_id)
                        chat_title = getattr(chat, 'title', f'Chat_{event.chat_id}')
                        
                        # 调用专门的删除消息处理函数
                        await revoke_message(event, chat)
                        
                    except Exception as e:
                        logger.error(f"获取群组信息失败: {e}")
                else:
                    logger.debug(f"❌ 消息删除发生在非目标群组，跳过")
            
        except Exception as e:
            logger.error(f"处理删除消息时出错: {e}")
    
    # ==================== 控制方法 ====================
    
    def clear_cache(self):
        """清空群组缓存"""
        self.chat_cache.clear()
        logger.info("已清空群组缓存")
    
    def switch_mode(self, new_mode: str):
        """切换监控模式（需要重启服务生效）"""
        if new_mode in [MonitorMode.POLLING_ONLY, MonitorMode.TELETHON_ONLY, MonitorMode.HYBRID]:
            self.mode = new_mode
            logger.info(f"🔄 监控模式已切换为: {self._get_mode_description()}")
            logger.warning("⚠️ 模式切换需要重启服务才能生效")
        else:
            logger.error(f"❌ 无效的监控模式: {new_mode}")
    
    async def stop(self):
        """停止监控并断开连接"""
        try:
            self.is_running = False
            
            # 停止轮询任务
            if self.polling_task and not self.polling_task.done():
                self.polling_task.cancel()
                try:
                    await self.polling_task
                except asyncio.CancelledError:
                    pass
            
            # 断开客户端连接
            if self.client:
                await self.client.disconnect()
                logger.info("已停止监控")
        except Exception as e:
            logger.error(f"停止监控时出错: {e}")

# 全局监控器实例
monitor_instance = None
monitor_thread = None

def get_monitor_mode():
    """从配置文件获取监控模式"""
    # 优先从配置文件读取
    if hasattr(config, 'MONITOR_MODE'):
        return config.MONITOR_MODE
    
    # 默认使用混合模式
    return MonitorMode.HYBRID

def run_monitor_in_thread():
    """在新线程中运行监控器"""
    global monitor_instance
    
    try:
        # 为新线程创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # 立即将事件循环注册到消息处理器
        from utils import message
        message.set_main_loop(loop)
        logger.info("✅ 已将 Telegram 事件循环注册到消息处理器")
        
        # 配置参数
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)  # 上级目录
        SESSION_PATH = os.path.join(project_root, 'sessions', 'tg_session')
        
        BOT_TOKEN = config.BOT_TOKEN
        API_ID = config.API_ID
        API_HASH = config.API_HASH
        DEVICE_MODEL = config.DEVICE_MODEL
        MONITOR_MODE = get_monitor_mode()
        
        # 检查session文件是否存在
        if not os.path.exists(SESSION_PATH + '.session'):
            logger.error(f"Session文件不存在: {SESSION_PATH}.session")
            logger.error("请先运行登录程序创建session文件")
            return
        
        # 创建整合监控器实例
        monitor_instance = IntegratedTelegramMonitor(SESSION_PATH, BOT_TOKEN, API_ID, API_HASH, DEVICE_MODEL, MONITOR_MODE)
        
        # 运行监控
        loop.run_until_complete(monitor_instance.start_monitoring())
        
    except Exception as e:
        logger.error(f"监控线程出错: {e}")
    finally:
        # 清理事件循环
        try:
            loop.close()
        except:
            pass

def main():
    """主函数 - 同步版本，适配服务管理器"""
    global monitor_thread
    
    try:
        mode = get_monitor_mode()
        logger.info("🚀 正在启动Telegram监控服务...")
        logger.info(f"🎛️ 监控模式: {mode}")
        
        # 在新线程中启动监控
        monitor_thread = threading.Thread(target=run_monitor_in_thread, daemon=True)
        monitor_thread.start()
        
        logger.info("✅ Telegram监控服务已在后台启动")
        
        # 保持主线程运行
        try:
            while monitor_thread.is_alive():
                monitor_thread.join(timeout=1)
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在停止...")
            
    except Exception as e:
        logger.error(f"监控服务启动失败: {e}")
        raise

async def async_main():
    """异步主函数 - 用于直接运行"""
    try:
        # 配置参数
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        SESSION_PATH = os.path.join(project_root, 'sessions', 'tg_session')
        
        BOT_TOKEN = config.BOT_TOKEN
        API_ID = config.API_ID
        API_HASH = config.API_HASH
        DEVICE_MODEL = config.DEVICE_MODEL
        MONITOR_MODE = get_monitor_mode()
        
        # 检查session文件是否存在
        if not os.path.exists(SESSION_PATH + '.session'):
            logger.error(f"Session文件不存在: {SESSION_PATH}.session")
            logger.error("请先运行登录程序创建session文件")
            return
        
        # 创建整合监控器实例
        monitor = IntegratedTelegramMonitor(SESSION_PATH, BOT_TOKEN, API_ID, API_HASH, DEVICE_MODEL, MONITOR_MODE)
        
        # 开始监控
        await monitor.start_monitoring()
        
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止...")
    except Exception as e:
        logger.error(f"监控过程中出现错误: {e}")

def get_client():
    """获取当前的监控器实例"""
    global monitor_instance
    if monitor_instance:
        return monitor_instance
    return None

if __name__ == "__main__":
    # 直接运行时使用异步版本
    asyncio.run(async_main())