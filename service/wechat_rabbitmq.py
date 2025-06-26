import asyncio
import json
import logging
import sys
import signal
import time
import traceback
from typing import Dict, Any, Callable, Optional

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

import config
from api.telegram_sender import telegram_sender
from service.telethon_client import get_user_id
from utils.locales import Locale
from utils.wechat_to_telegram import process_rabbitmq_message

logger = logging.getLogger(__name__)
locale = Locale(config.LANG)

class ContactMessageProcessor:
    """单个联系人的消息处理器"""
    
    def __init__(self, contact_id: str):
        self.contact_id = contact_id
        self.message_queue = asyncio.Queue()
        self.processing_task = None
        self.is_running = False
        
    async def start(self):
        """启动处理任务"""
        if not self.is_running:
            self.is_running = True
            self.processing_task = asyncio.create_task(self._process_messages())
            logger.debug(f"🚀 启动联系人 {self.contact_id} 的消息处理器")
    
    async def stop(self):
        """停止处理任务"""
        self.is_running = False
        if self.processing_task and not self.processing_task.done():
            self.processing_task.cancel()
            try:
                await self.processing_task
            except asyncio.CancelledError:
                pass
        logger.debug(f"🔴 停止联系人 {self.contact_id} 的消息处理器")
    
    async def add_message(self, message_data: dict, msg_obj: AbstractIncomingMessage):
        """添加消息到队列"""
        await self.message_queue.put((message_data, msg_obj))
    
    async def _process_messages(self):
        """处理消息的主循环"""
        while self.is_running:
            try:
                # 等待消息，设置超时以便能够响应停止信号
                message_data, msg_obj = await asyncio.wait_for(
                    self.message_queue.get(), 
                    timeout=1.0
                )
                
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
                    idle_contacts = []
                    for contact_id, processor in self.contact_processors.items():
                        # 如果队列为空且没有正在处理的消息，标记为空闲
                        if processor.message_queue.empty():
                            idle_contacts.append(contact_id)
                    
                    # 移除空闲的处理器（保留最近活跃的）
                    if len(idle_contacts) > 10:  # 只有超过10个空闲时才清理
                        for contact_id in idle_contacts[:-5]:  # 保留最后5个
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
            
            # 开始消费
            consumer_tag = await queue.consume(
                callback=lambda message: self._message_wrapper(message, callback),
                no_ack=False
            )
            
            self.consumer_tags[queue_name] = consumer_tag
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
                    await self.channel.basic_cancel(consumer_tag)
            except Exception as e:
                logger.error(f"❌ 停止队列'{queue_name}'消费者时出错: {e}")
        
        if self.connection and not self.connection.is_closed:
            await self.connection.close()
        
        logger.info("🔴 所有消费者已停止")


# =============================================================================
# 消息处理函数
# =============================================================================

# 全局消费者实例，用于在处理函数中访问
_global_consumer: Optional[WeChatRabbitMQConsumer] = None

async def handle_wechat_message(message: str, msg_obj: AbstractIncomingMessage) -> bool:
    """
    处理微信消息
    
    Args:
        message: 消息内容
        msg_obj: 原始消息对象
        
    Returns:
        bool: 处理是否成功
    """
    global _global_consumer
    
    try:        
        # 尝试解析JSON
        try:
            message_data = json.loads(message)
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON解析失败: {e}")
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
        
        # 处理每条消息 - 按联系人分发到不同的处理器
        processed_count = 0
        failed_count = 0
        
        for msg in add_msgs:
            msg_id = msg.get('MsgId')
            from_wxid = msg.get('FromUserName', {}).get('string', '')
            
            if not msg_id or not from_wxid:
                continue

            try:
                # 获取或创建该联系人的处理器
                if _global_consumer:
                    processor = await _global_consumer.get_or_create_processor(from_wxid)
                    # 将消息添加到该联系人的处理队列
                    await processor.add_message(msg, msg_obj)
                    processed_count += 1
                else:
                    # 如果没有全局消费者，直接处理（兼容模式）
                    await process_rabbitmq_message(msg)
                    processed_count += 1
                    
            except Exception as e:
                failed_count += 1
                logger.error(f"❌ 分发消息 {msg_id} 到联系人 {from_wxid} 失败: {e}")
        
        # 只要有消息被处理就算成功
        return processed_count > 0 or failed_count == 0
        
    except Exception as e:
        logger.error(f"❌ 处理微信消息时出错: {e}")
        logger.error(f"错误堆栈: {traceback.format_exc()}")
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
