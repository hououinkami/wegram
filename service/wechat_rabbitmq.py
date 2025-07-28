import asyncio
import hashlib
import json
import logging
import sys
import signal
import time
import traceback
from typing import Dict, Any, Callable, Optional, Set

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

import config
from config import LOCALE as locale
from api.telegram_sender import telegram_sender
from service.telethon_client import get_user_id
from utils.wechat_to_telegram import process_rabbitmq_message

logger = logging.getLogger(__name__)

class ContactMessageProcessor:
    """单个联系人的消息处理器"""
    
    def __init__(self, contact_id: str):
        self.contact_id = contact_id
        self.message_queue = asyncio.Queue()
        self.processing_task = None
        self.is_running = False
        self.last_activity = time.time()  # 记录最后活动时间
        
    async def add_message(self, message_data: dict):
        """添加消息到队列"""
        self.last_activity = time.time()
        await self.message_queue.put(message_data)
    
    async def start(self):
        """启动消息处理器"""
        if not self.is_running:
            self.is_running = True
            self.processing_task = asyncio.create_task(self._process_messages())
            logger.debug(f"🚀 启动联系人 {self.contact_id} 的消息处理器")
    
    async def stop(self):
        """停止消息处理器"""
        self.is_running = False
        
        if self.processing_task and not self.processing_task.done():
            self.processing_task.cancel()
            try:
                await self.processing_task
            except asyncio.CancelledError:
                pass
        
        # 清空剩余消息
        while not self.message_queue.empty():
            try:
                self.message_queue.get_nowait()
                self.message_queue.task_done()
            except asyncio.QueueEmpty:
                break
        
        logger.debug(f"🔴 停止联系人 {self.contact_id} 的消息处理器")
    
    async def _process_messages(self):
        """处理消息的主循环"""
        while self.is_running:
            try:
                # 等待消息，设置超时以便能够响应停止信号
                message_data = await asyncio.wait_for(
                    self.message_queue.get(), 
                    timeout=1.0
                )
                
                # 更新活动时间
                self.last_activity = time.time()
                
                # 处理消息
                try:
                    await process_rabbitmq_message(message_data)
                    logger.debug(f"✅ 成功处理联系人 {self.contact_id} 的消息")
                except Exception as e:
                    logger.error(f"❌ 处理联系人 {self.contact_id} 消息失败: {e}")
                
                # 标记任务完成
                self.message_queue.task_done()
                
            except asyncio.TimeoutError:
                # 超时是正常的，继续循环
                continue
            except Exception as e:
                logger.error(f"❌ 联系人 {self.contact_id} 消息处理器出错: {e}")
                await asyncio.sleep(0.1)  # 短暂休息避免快速循环

class WeChatRabbitMQConsumer:
    """微信协议RabbitMQ异步消费者"""
    
    def __init__(self, rabbitmq_url: str, max_retries: int = 10):
        """
        初始化消费者
        
        Args:
            rabbitmq_url: RabbitMQ连接URL
            max_retries: 最大重试次数
        """
        self.rabbitmq_url = rabbitmq_url
        self.max_retries = max_retries
        self.connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self.channel: Optional[aio_pika.abc.AbstractRobustChannel] = None
        self.is_running = False
        self.consumer_tags = {}
        
        # 联系人消息处理器管理
        self.contact_processors: Dict[str, ContactMessageProcessor] = {}
        self.processor_lock = asyncio.Lock()
        
        # 清理任务
        self.cleanup_task = None
        
        # 消息去重器
        self.deduplicator = MessageDeduplicator(cache_size=1000, ttl=3600)  # 1小时过期
        
        # 统计信息
        self.stats = {
            "total_messages": 0,
            "duplicate_messages": 0,
            "processed_messages": 0,
            "failed_messages": 0
        }
        
        # 添加心跳监控器
        self.heartbeat_monitor = HeartbeatMonitor(timeout=300)  # 5分钟超时
    
    # 获取统计信息方法
    def get_stats(self) -> Dict[str, Any]:
        """获取消费者统计信息"""
        dedup_stats = self.deduplicator.get_stats()
        
        heartbeat_status = self.heartbeat_monitor.get_status()  # 添加心跳状态
        
        total = self.stats["total_messages"]
        duplicate_rate = (self.stats["duplicate_messages"] / total * 100) if total > 0 else 0
        
        return {
            **self.stats,
            "duplicate_rate_percent": round(duplicate_rate, 2),
            "deduplicator": dedup_stats,
            "active_processors": len(self.contact_processors),
            "heartbeat_monitor": heartbeat_status  # 添加心跳监控状态
        }
    
    async def connect(self) -> bool:
        """连接到RabbitMQ服务器"""
        for attempt in range(self.max_retries):
            try:                
                # 使用robust连接，自动重连
                self.connection = await aio_pika.connect_robust(
                    url=self.rabbitmq_url,
                    heartbeat=30,
                    blocked_connection_timeout=3,
                    connection_attempts=3,
                    retry_delay=1.0,
                    socket_timeout=0.5,
                    stack_timeout=1.0,
                )
                
                # 创建通道
                self.channel = await self.connection.channel()
                
                # 设置QoS，控制并发处理数量
                await self.channel.set_qos(
                    prefetch_count=5,  # 增加预取数量以支持并发
                    prefetch_size=0
                )
                
                logger.info("🟢 成功连接到RabbitMQ")
                return True
                
            except Exception as e:
                logger.error(f"❌ 第{attempt + 1}次连接尝试失败: {e}")
                if attempt < self.max_retries - 1:
                    wait_time = min(2 ** attempt, 30)  # 指数退避，最大30秒
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("❌ 所有连接尝试均失败")
                    return False
    
    async def wait_for_rabbitmq(self, timeout: int = 60) -> bool:
        """等待RabbitMQ服务可用"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                test_connection = await aio_pika.connect_robust(
                    self.rabbitmq_url,
                    connection_attempts=1,
                    retry_delay=1
                )
                await test_connection.close()
                return True
            except Exception as e:
                logger.debug(f"⚠️ RabbitMQ服务暂未就绪: {e}")
                await asyncio.sleep(2)
        
        logger.error(f"❌ RabbitMQ服务在{timeout}秒后仍不可用")
        return False
    
    async def get_or_create_processor(self, contact_id: str) -> ContactMessageProcessor:
        """获取或创建联系人处理器"""
        async with self.processor_lock:
            if contact_id not in self.contact_processors:
                processor = ContactMessageProcessor(contact_id)
                await processor.start()
                self.contact_processors[contact_id] = processor
                logger.debug(f"📝 为联系人 {contact_id} 创建新的处理器")
            return self.contact_processors[contact_id]
    
    async def cleanup_idle_processors(self):
        """清理空闲的处理器"""
        while self.is_running:
            try:
                await asyncio.sleep(300)  # 每5分钟检查一次
                
                async with self.processor_lock:
                    current_time = time.time()
                    idle_contacts = []
                    
                    for contact_id, processor in self.contact_processors.items():
                        # 检查队列是否为空且最后活动时间超过10分钟
                        if (processor.message_queue.empty() and 
                            current_time - processor.last_activity > 600):  # 10分钟无活动
                            idle_contacts.append(contact_id)
                    
                    # 只清理长时间无活动的处理器，保留活跃的
                    for contact_id in idle_contacts[:10]:  # 限制每次最多清理10个
                        processor = self.contact_processors.pop(contact_id)
                        await processor.stop()
                        logger.debug(f"🧹 清理空闲处理器: {contact_id}")
                        
            except Exception as e:
                logger.error(f"❌ 清理处理器时出错: {e}")
    
    async def consume_queue(self, queue_name: str, callback: Callable):
        """消费指定队列的消息"""
        try:
            if not self.channel:
                logger.error("❌ 通道不可用")
                return False
            
            # 声明队列（确保队列存在，参数与Go代码保持一致）
            queue = await self.channel.declare_queue(
                queue_name,
                durable=True,      # 持久化，与Go代码一致
                exclusive=False,   # 非独占
                auto_delete=False  # 不自动删除
            )
            
            # 开始消费并保存消费者对象
            async def message_handler(message):
                await self._message_wrapper(message, callback)
            
            # 开始消费，返回的是Consumer对象
            consumer = await queue.consume(
                callback=message_handler,
                no_ack=False
            )
            
            # 保存Consumer对象
            self.consumer_tags[queue_name] = consumer
            logger.info(f"🚀 开始消费队列: {queue_name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 设置队列'{queue_name}'消费者时出错: {e}")
            logger.debug(f"完整错误堆栈: {traceback.format_exc()}")
            return False
    
    async def _message_wrapper(self, message: AbstractIncomingMessage, callback: Callable):
        """消息处理包装器"""
        try:
            # 解码消息体为字符串
            body_str = message.body.decode('utf-8')
            
            # 调用处理函数
            result = await callback(body_str, message)
            
            if result:
                # 处理成功
                await message.ack()
            else:
                # 处理失败，但不是异常
                await message.nack(requeue=False)
                logger.warning(f"⚠️ 队列消息处理失败，消息已丢弃")
                
        except json.JSONDecodeError as e:
            # JSON解析错误
            await message.nack(requeue=False)
            logger.error(f"❌ 队列消息JSON格式错误: {e}")
                
        except Exception as e:
            # 其他异常
            try:
                await message.nack(requeue=False)
            except Exception as nack_error:
                logger.error(f"❌ 拒绝消息时出错: {nack_error}")
            
            logger.error(f"❌ 消息包装器处理出错: {e}")
            logger.debug(f"错误堆栈: {traceback.format_exc()}")

    async def start_consuming(self, queue_configs: Dict[str, Callable]):
        """
        开始消费多个队列
        
        Args:
            queue_configs: 队列名称到处理函数的映射
                例如: {"wxapi_messages": handle_wechat_message}
        """
        # 等待RabbitMQ服务可用
        if not await self.wait_for_rabbitmq():
            logger.error("❌ RabbitMQ服务不可用")
            return False
        
        # 连接到RabbitMQ
        if not await self.connect():
            logger.error("❌ 连接RabbitMQ失败")
            return False
        
        try:
            # 设置所有队列的消费者
            success_count = 0
            for queue_name, callback in queue_configs.items():
                if await self.consume_queue(queue_name, callback):
                    success_count += 1
                else:
                    logger.error(f"❌ 设置队列消费者失败: {queue_name}")
            
            if success_count == 0:
                logger.error("❌ 没有成功设置任何消费者")
                return False
            
            self.is_running = True
            
            # 启动清理任务
            self.cleanup_task = asyncio.create_task(self.cleanup_idle_processors())
            # 启动心跳监控
            await self.heartbeat_monitor.start_monitoring()
            
            logger.info("✅ RabbiMQ消费者已启动，服务正在运行...")
            
            # 保持服务运行
            while self.is_running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ 消费设置过程中出错: {e}")
            logger.debug(f"完整错误堆栈: {traceback.format_exc()}")
            return False
    
    async def stop_consuming(self):
        """停止消费消息"""
        self.is_running = False
        
        # 停止心跳监控
        await self.heartbeat_monitor.stop_monitoring()
        
        # 停止清理任务
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        
        # 停止所有联系人处理器
        async with self.processor_lock:
            for processor in self.contact_processors.values():
                await processor.stop()
            self.contact_processors.clear()
        
        # 停止消费者
        for queue_name, consumer_tag in self.consumer_tags.items():
            try:
                if self.channel and not self.channel.is_closed:
                    await consumer_tag.cancel()
            except Exception as e:
                logger.error(f"❌ 停止队列'{queue_name}'消费者时出错: {e}")
        
        # 清空消费者标签
        self.consumer_tags.clear()
        
        # 关闭通道和连接
        try:
            if self.channel and not self.channel.is_closed:
                await self.channel.close()
        except Exception as e:
            logger.error(f"❌ 关闭通道时出错: {e}")
        
        try:
            if self.connection and not self.connection.is_closed:
                await self.connection.close()
        except Exception as e:
            logger.error(f"❌ 关闭连接时出错: {e}")
        
        logger.info("🔴 所有消费者已停止")

class MessageDeduplicator:
    """消息去重器"""
    
    def __init__(self, cache_size: int = 1000, ttl: int = 3600):
        """
        初始化去重器
        
        Args:
            cache_size: 内存缓存大小
            ttl: 消息ID过期时间（秒）
        """
        self.processed_messages: Dict[str, float] = {}  # msg_id -> timestamp
        self.cache_size = cache_size
        self.ttl = ttl
        self.last_cleanup = time.time()
    
    def is_duplicate(self, msg_id: str) -> bool:
        """
        检查是否重复消息
        
        Args:
            msg_id: 消息ID
            
        Returns:
            bool: 是否重复
        """
        if not msg_id:
            return False
        
        current_time = time.time()
        
        # 定期清理过期消息
        if current_time - self.last_cleanup > 300:  # 每5分钟清理一次
            self._cleanup_expired(current_time)
            self.last_cleanup = current_time
        
        # 检查是否已处理
        if msg_id in self.processed_messages:
            # 检查是否过期
            if current_time - self.processed_messages[msg_id] < self.ttl:
                return True
            else:
                # 过期了，移除
                del self.processed_messages[msg_id]
        
        return False
    
    def mark_processed(self, msg_id: str):
        """
        标记消息已处理
        
        Args:
            msg_id: 消息ID
        """
        if not msg_id:
            return
        
        current_time = time.time()
        self.processed_messages[msg_id] = current_time
        
        # 如果缓存过大，清理最老的消息
        if len(self.processed_messages) > self.cache_size:
            self._cleanup_oldest()
    
    def _cleanup_expired(self, current_time: float):
        """清理过期消息"""
        expired_keys = [
            msg_id for msg_id, timestamp in self.processed_messages.items()
            if current_time - timestamp >= self.ttl
        ]
        
        for key in expired_keys:
            del self.processed_messages[key]
        
        if expired_keys:
            logger.debug(f"🧹 清理过期消息ID: {len(expired_keys)}个")
    
    def _cleanup_oldest(self):
        """清理最老的消息（当缓存过大时）"""
        if len(self.processed_messages) <= self.cache_size:
            return
        
        # 按时间戳排序，移除最老的消息
        sorted_items = sorted(self.processed_messages.items(), key=lambda x: x[1])
        remove_count = len(self.processed_messages) - self.cache_size + 1000  # 多删除一些
        
        for msg_id, _ in sorted_items[:remove_count]:
            del self.processed_messages[msg_id]
        
        logger.debug(f"🧹 清理最老消息ID: {remove_count}个")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "cached_messages": len(self.processed_messages),
            "cache_size_limit": self.cache_size,
            "ttl_seconds": self.ttl
        }

class HeartbeatMonitor:
    """心跳监控器 - 监控服务是否正常运行"""
    
    def __init__(self, timeout: int = 300):  # 5分钟超时
        """
        初始化心跳监控器
        
        Args:
            timeout: 超时时间（秒），默认5分钟
        """
        self.timeout = timeout
        self.last_heartbeat = time.time()
        self.is_running = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.service_down = False
        self.service_down_time = None
        
    async def update_heartbeat(self):
        """更新心跳时间"""
        current_time = time.time()
        self.last_heartbeat = current_time
        
        if self.service_down:
            # 服务恢复 - 计算异常持续时间
            if self.service_down_time:
                down_duration = current_time - self.service_down_time
                await self._send_service_recovery_alert(down_duration)
            
            # 重置状态
            self.service_down = False
            self.service_down_time = None
            logger.info("✅ 微信服务已恢复正常")

    async def start_monitoring(self):
        """开始监控"""
        if not self.is_running:
            self.is_running = True
            self.monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info(f"🔍 启动心跳监控，超时时间: {self.timeout}秒")
    
    async def stop_monitoring(self):
        """停止监控"""
        self.is_running = False
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("🔴 心跳监控已停止")
    
    async def _monitor_loop(self):
        """监控循环"""
        while self.is_running:
            try:
                current_time = time.time()
                time_since_last = current_time - self.last_heartbeat
                
                if time_since_last > self.timeout:
                    if not self.service_down:
                        # 首次检测到服务异常 - 记录异常开始时间
                        self.service_down = True
                        self.service_down_time = self.last_heartbeat  # 使用最后一次正常心跳时间
                        
                        logger.error(f"❌ 微信服务疑似DOWN - 已超过{self.timeout}秒未收到消息")
                        logger.error(f"⏰ 最后收到消息时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.last_heartbeat))}")
                        
                        # 发送异常告警
                        await self._send_service_down_alert(time_since_last)
                
                # 每30秒检查一次
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"❌ 心跳监控出错: {e}")
                await asyncio.sleep(10)
    
    async def _send_service_down_alert(self, down_time: float):
        """发送服务异常告警"""
        try:
            tg_user_id = get_user_id()
            down_minutes = int(down_time // 60)
            
            alert_message = f"⚠️ **WeChatサーバーに異常発生！**\n\n" \
                          f"🔴 サーバー状態: ダウン\n" \
                          f"⏱️ 異常継続時間: {down_minutes}分\n" \
                          f"📝 最終正常時刻: {time.strftime('%H:%M:%S', time.localtime(self.last_heartbeat))}\n\n" \
                          f"サーバーの稼働状況をご確認ください！"
            
            await telegram_sender.send_text(tg_user_id, alert_message)
            
        except Exception as e:
            logger.error(f"❌ 发送服务异常告警失败: {e}")
    
    async def _send_service_recovery_alert(self, total_down_time: float):
        """发送服务恢复告警"""
        try:
            tg_user_id = get_user_id()
            
            # 计算异常持续时间
            down_hours = int(total_down_time // 3600)
            down_minutes = int((total_down_time % 3600) // 60)
            down_seconds = int(total_down_time % 60)
            
            # 格式化持续时间
            if down_hours > 0:
                duration_str = f"{down_hours}時間{down_minutes}分{down_seconds}秒"
            elif down_minutes > 0:
                duration_str = f"{down_minutes}分{down_seconds}秒"
            else:
                duration_str = f"{down_seconds}秒"
            
            # 构建恢复消息
            recovery_message = f"✅ **WeChatサーバー復旧完了！**\n\n" \
                             f"🟢 サーバー状態: 正常稼働中\n" \
                             f"⏱️ 異常継続時間: {duration_str}\n" \
                             f"📝 異常開始時刻: {time.strftime('%H:%M:%S', time.localtime(self.service_down_time))}\n" \
                             f"📝 復旧完了時刻: {time.strftime('%H:%M:%S', time.localtime(self.last_heartbeat))}\n\n" \
                             f"サーバーが正常に復旧しました！"
            
            await telegram_sender.send_text(tg_user_id, recovery_message)
            
            logger.info(f"✅ 微信服务已恢复，总异常时间: {duration_str}")
            
        except Exception as e:
            logger.error(f"❌ 发送服务恢复告警失败: {e}")
    
    def get_status(self) -> dict:
        """获取监控状态"""
        current_time = time.time()
        time_since_last = current_time - self.last_heartbeat
        
        status = {
            "is_monitoring": self.is_running,
            "service_down": self.service_down,
            "last_heartbeat": self.last_heartbeat,
            "time_since_last_seconds": int(time_since_last),
            "time_since_last_minutes": round(time_since_last / 60, 1),
            "timeout_seconds": self.timeout
        }
        
        # 🆕 如果服务异常，添加异常开始时间和当前异常持续时间
        if self.service_down and self.service_down_time:
            current_down_time = current_time - self.service_down_time
            status.update({
                "service_down_start_time": self.service_down_time,
                "current_down_duration_seconds": int(current_down_time),
                "current_down_duration_minutes": round(current_down_time / 60, 1)
            })
        
        return status

# =============================================================================
# 消息处理函数
# =============================================================================

# 全局消费者实例，用于在处理函数中访问
_global_consumer: Optional[WeChatRabbitMQConsumer] = None

async def handle_wechat_message(message: str, msg_obj: AbstractIncomingMessage) -> bool:
    """
    处理微信消息（带去重功能和心跳功能）
    
    Args:
        message: 消息内容
        msg_obj: 原始消息对象
        
    Returns:
        bool: 处理是否成功
    """
    global _global_consumer
    
    try:
        # 更新心跳 - 无论什么消息都更新
        if _global_consumer:
            await _global_consumer.heartbeat_monitor.update_heartbeat()
            
        # 尝试解析JSON
        try:
            message_data = json.loads(message)
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON解析失败: {e}")

            if _global_consumer:
                _global_consumer.stats["failed_messages"] += 1

            return False
            
        # 检查是否无新消息
        if message_data.get('Message') != "成功":
            # 检查是否在线
            await login_check(message_data)
            return True
        
        # 获取消息列表
        add_msgs = message_data.get('Data', {}).get('AddMsgs', [])
        if not add_msgs:
            logger.debug("没有新消息")
            return True
        
        # 处理每条消息 - 改进去重逻辑
        processed_count = 0
        failed_count = 0
        duplicate_count = 0
        
        for msg in add_msgs:
            msg_id = msg.get('MsgId')
            from_wxid = msg.get('FromUserName', {}).get('string', '')
            
            if not msg_id or not from_wxid:
                continue
            
            if _global_consumer:
                _global_consumer.stats["total_messages"] += 1
            
            # 🔧 改进：使用复合键进行去重，包含消息ID和发送者ID
            msg_key = f"{msg_id}"
            
            # 🔧 改进：先检查去重，立即标记为处理中
            if _global_consumer and _global_consumer.deduplicator.is_duplicate(msg_key):
                duplicate_count += 1
                _global_consumer.stats["duplicate_messages"] += 1
                logger.warning(f"🔄 跳过重复消息: {msg_id} (来自: {from_wxid})")
                continue

            try:
                # 🔧 改进：立即标记为已处理，防止竞态条件
                if _global_consumer:
                    _global_consumer.deduplicator.mark_processed(msg_key)
                
                # 获取或创建该联系人的处理器
                if _global_consumer:
                    processor = await _global_consumer.get_or_create_processor(from_wxid)
                    # 🔧 关键修改：只传递单个消息数据，移除msg_obj参数
                    await processor.add_message(msg)  # 只传msg，不传msg_obj
                    
                    _global_consumer.stats["processed_messages"] += 1
                    processed_count += 1
                else:
                    # 如果没有全局消费者，直接处理（兼容模式）
                    await process_rabbitmq_message(msg)
                    processed_count += 1
                    
            except Exception as e:
                failed_count += 1
                if _global_consumer:
                    _global_consumer.stats["failed_messages"] += 1
                logger.error(f"❌ 分发消息 {msg_id} 到联系人 {from_wxid} 失败: {e}")
                
                # 🔧 改进：处理失败时，从去重缓存中移除，允许重试
                if _global_consumer:
                    try:
                        # 从已处理消息中移除，允许后续重试
                        if msg_key in _global_consumer.deduplicator.processed_messages:
                            del _global_consumer.deduplicator.processed_messages[msg_key]
                    except Exception as cleanup_error:
                        logger.error(f"清理失败消息缓存时出错: {cleanup_error}")
        
        # 记录处理结果
        if duplicate_count > 0:
            logger.info(f"📊 消息处理完成 - 处理: {processed_count}, 失败: {failed_count}, 重复: {duplicate_count}")
        elif processed_count > 0 or failed_count > 0:
            logger.debug(f"📊 消息处理完成 - 处理: {processed_count}, 失败: {failed_count}")
        
        # 只要有消息被处理就算成功
        return processed_count > 0 or failed_count == 0
        
    except Exception as e:
        logger.error(f"❌ 处理微信消息时出错: {e}")
        logger.error(f"错误堆栈: {traceback.format_exc()}")
        if _global_consumer:
            _global_consumer.stats["failed_messages"] += 1
        return False

# 登陆检测
login_status = None

async def login_check(callback_data):
    """异步登录检测"""
    global login_status
    
    current_message = callback_data.get('Message')
    
    tg_user_id = get_user_id()
    if current_message == "用户可能退出":
        # 只有当上一次状态不是离线时才发送离线提示
        if login_status != "offline":
            await telegram_sender.send_text(tg_user_id, locale.common("offline"))
            login_status = "offline"
        return {"success": True, "message": "用户可能退出"}
    
    else:
        # 当前不是离线状态
        # 如果上一次是离线状态，发送上线提示
        if login_status == "offline":
            await telegram_sender.send_text(tg_user_id, locale.common("online"))
        login_status = "online"
        return {"success": True, "message": "正常状态"}
# =============================================================================
# 配置和启动
# =============================================================================

def get_config():
    """获取配置"""
    return {
        # 从环境变量或配置文件读取，如果没有则使用您提供的默认值
        'rabbitmq_url': config.RABBITMQ_URL,
        
        # 定义要消费的队列和对应的处理函数
        'queue_configs': {
            'wxapi': handle_wechat_message,    # 微信消息队列
        }
    }

async def main():
    """主函数"""
    global _global_consumer
    
    config = get_config()
    
    # 创建消费者
    consumer = WeChatRabbitMQConsumer(
        rabbitmq_url=config['rabbitmq_url'],
        max_retries=10
    )
    
    # 设置全局消费者引用
    _global_consumer = consumer
    
    # 设置信号处理（优雅关闭）
    def signal_handler():
        logger.info("📡 收到停止信号")
        asyncio.create_task(consumer.stop_consuming())
    
    loop = asyncio.get_event_loop()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows不支持信号处理
            pass
    
    # 开始消费消息
    try:
        await consumer.start_consuming(config['queue_configs'])
    except Exception as e:
        logger.error(f"❌ 消费者错误: {e}")
        logger.error(f"错误堆栈: {traceback.format_exc()}")
    finally:
        await consumer.stop_consuming()

if __name__ == "__main__":
    try:
        # 运行异步主函数
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🔴 服务被用户中断")
    except Exception as e:
        logger.error(f"❌ 服务错误: {e}")
        sys.exit(1)
