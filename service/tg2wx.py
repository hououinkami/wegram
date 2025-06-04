import asyncio
import logging
from telethon import TelegramClient, events
from telethon.tl.types import MessageService, MessageActionChatAddUser, MessageActionChatDeleteUser
from telethon.errors import SessionPasswordNeededError, FloodWaitError
import os
import threading
from typing import Set, Optional, Dict
import config
from utils.sender import process_telegram_update
from utils.sender_telethon import process_telethon_update


# 配置日志
logger = logging.getLogger(__name__)

class TelegramMonitor:
    def __init__(self, session_path: str, bot_token: str, api_id: int, api_hash: str):
        """
        初始化Telegram监控器
        
        Args:
            session_path: session文件路径
            bot_token: 要监控的BOT TOKEN
            api_id: Telegram API ID
            api_hash: Telegram API Hash
        """
        self.session_path = session_path
        self.bot_token = bot_token
        self.api_id = api_id
        self.api_hash = api_hash
        self.client = None
        self.user_id = None
        self._running = False
        
        # 群组缓存：记录已检查过的群组
        self.chat_cache: Dict[int, bool] = {}  # chat_id -> has_target_bot
        
        # 从BOT TOKEN提取BOT ID
        self.target_bot_id = self.bot_token.split(':')[0] if ':' in self.bot_token else None
        logger.info(f"目标BOT ID: {self.target_bot_id}")
        
    async def initialize(self):
        """初始化客户端并获取用户信息"""
        try:
            # 使用已有的session文件创建客户端
            self.client = TelegramClient(self.session_path, self.api_id, self.api_hash)
            await self.client.start()
            
            # 获取当前用户ID
            me = await self.client.get_me()
            self.user_id = me.id
            logger.info(f"已登录用户: {me.first_name} (ID: {self.user_id})")
            
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            raise
    
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
    
    async def start_monitoring(self):
        """开始监控消息"""
        if not self.client:
            await self.initialize()
        
        self._running = True
        logger.info("🚀 开始被动监控消息...")
        logger.info("📝 只有在包含目标BOT的群组中发送消息时才会进行处理")
        
        # 监听新消息事件
        @self.client.on(events.NewMessage)
        async def handle_new_message(event):
            await self.process_new_message(event)
        
        # 监听消息删除事件
        @self.client.on(events.MessageDeleted)
        async def handle_deleted_message(event):
            await self.process_deleted_message(event)
        
        # 保持客户端运行
        try:
            await self.client.run_until_disconnected()
        except Exception as e:
            logger.error(f"监控循环出错: {e}")
        finally:
            await self.stop()
    
    async def process_new_message(self, event):
        """处理新消息事件"""
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
                await process_telegram_update(message, chat, self.client)
            else:
                logger.debug(f"❌ 群组 {chat.title} 不包含目标BOT，跳过")
            
        except Exception as e:
            logger.error(f"处理新消息时出错: {e}")
    
    async def process_deleted_message(self, event):
        """处理消息删除事件"""
        try:
            logger.info(f"🗑️ 检测到消息删除:")
            logger.info(f"   删除的消息ID: {event.deleted_ids}")
            
            # 在这里添加你的处理逻辑
            
            
        except Exception as e:
            logger.error(f"处理删除消息时出错: {e}")
    
    def clear_cache(self):
        """清空群组缓存"""
        self.chat_cache.clear()
        logger.info("已清空群组缓存")
    
    async def send_to_wechat(self, message_data):
        """
        发送消息到微信
        TODO: 实现具体的微信发送逻辑
        """
        try:
            # 这里添加调用微信API的代码
            logger.info(f"📤 准备发送到微信: {message_data['text']}")
            
            # 示例API调用
            # result = await wechat_api.send_message(
            #     content=message_data['text'],
            #     chat_title=message_data['chat_title']
            # )
            
        except Exception as e:
            logger.error(f"发送到微信时出错: {e}")
    
    async def recall_from_wechat(self, message_id):
        """
        从微信撤回消息
        TODO: 实现具体的微信撤回逻辑
        """
        try:
            logger.info(f"🔄 准备从微信撤回消息: {message_id}")
            
            # 示例API调用
            # result = await wechat_api.recall_message(message_id)
            
        except Exception as e:
            logger.error(f"从微信撤回消息时出错: {e}")
    
    async def stop(self):
        """停止监控并断开连接"""
        try:
            self._running = False
            if self.client:
                await self.client.disconnect()
                logger.info("已停止监控")
        except Exception as e:
            logger.error(f"停止监控时出错: {e}")

# 全局监控器实例
monitor_instance = None
monitor_thread = None

def run_monitor_in_thread():
    """在新线程中运行监控器"""
    global monitor_instance
    
    try:
        # 为新线程创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # 配置参数
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)  # 上级目录
        SESSION_PATH = os.path.join(project_root, 'sessions', 'tg_session')
        
        BOT_TOKEN = config.BOT_TOKEN
        API_ID = config.API_ID
        API_HASH = config.API_HASH
        
        logger.info(f"使用Session路径: {SESSION_PATH}")
        logger.info(f"BOT Token: {BOT_TOKEN[:10]}...")
        
        # 检查session文件是否存在
        if not os.path.exists(SESSION_PATH + '.session'):
            logger.error(f"Session文件不存在: {SESSION_PATH}.session")
            logger.error("请先运行登录程序创建session文件")
            return
        
        # 创建监控器实例
        monitor_instance = TelegramMonitor(SESSION_PATH, BOT_TOKEN, API_ID, API_HASH)
        
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
        logger.info("🚀 正在启动Telegram被动监控服务...")
        
        # 在新线程中启动监控
        monitor_thread = threading.Thread(target=run_monitor_in_thread, daemon=True)
        monitor_thread.start()
        
        logger.info("✅ Telegram被动监控服务已在后台启动")
        logger.info("💡 提示: 只有在包含目标BOT的群组中发送消息时才会触发检查")
        
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
        
        logger.info(f"使用Session路径: {SESSION_PATH}")
        
        # 检查session文件是否存在
        if not os.path.exists(SESSION_PATH + '.session'):
            logger.error(f"Session文件不存在: {SESSION_PATH}.session")
            logger.error("请先运行登录程序创建session文件")
            return
        
        # 创建监控器实例
        monitor = TelegramMonitor(SESSION_PATH, BOT_TOKEN, API_ID, API_HASH)
        
        # 开始监控
        await monitor.start_monitoring()
        
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止...")
    except Exception as e:
        logger.error(f"监控过程中出现错误: {e}")

def get_client():
    """获取当前的telethon客户端实例"""
    global monitor_instance
    if monitor_instance and monitor_instance.client:
        return monitor_instance.client
    return None

if __name__ == "__main__":
    # 直接运行时使用异步版本
    asyncio.run(async_main())