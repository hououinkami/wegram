import asyncio
import json
import logging
import os
import sys
import signal
import time
import traceback
from typing import Dict, Any, Callable, Optional

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

import config

logger = logging.getLogger(__name__)

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
        
    async def connect(self) -> bool:
        """连接到RabbitMQ服务器"""
        for attempt in range(self.max_retries):
            try:                
                # 使用robust连接，自动重连
                self.connection = await aio_pika.connect_robust(
                    url=self.rabbitmq_url,
                    heartbeat=600,
                    blocked_connection_timeout=300,
                    connection_attempts=3,
                    retry_delay=2
                )
                
                # 创建通道
                self.channel = await self.connection.channel()
                
                # 设置QoS，控制并发处理数量
                await self.channel.set_qos(prefetch_count=10)
                
                logger.info("🟢 Connected to RabbitMQ successfully")
                return True
                
            except Exception as e:
                logger.error(f"❌ Connection attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    wait_time = min(2 ** attempt, 30)  # 指数退避，最大30秒
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("❌ All connection attempts failed")
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
                logger.debug(f"⚠️ RabbitMQ not ready yet: {e}")
                await asyncio.sleep(2)
        
        logger.error(f"❌ RabbitMQ service not available after {timeout} seconds")
        return False
    
    async def consume_queue(self, queue_name: str, callback: Callable):
        """消费指定队列的消息"""
        try:
            if not self.channel:
                logger.error("Channel is not available")
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
                no_ack=False  # 手动确认
            )
            
            self.consumer_tags[queue_name] = consumer_tag
            logger.info(f"🚀 Started consuming from queue: {queue_name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error setting up consumer for queue '{queue_name}': {e}")
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            return False
    
    async def _message_wrapper(self, message: AbstractIncomingMessage, callback: Callable):
        """消息处理包装器"""
        async with message.process():
            try:
                # 解码消息
                body = message.body.decode('utf-8')
                queue_name = message.routing_key or "unknown"
                
                # 调用处理函数
                result = await callback(body, message)
                
                if not result:
                    logger.warning(f"⚠️ Message processing failed from queue '{queue_name}'")
                    raise Exception("❌ Message processing failed")
                    
            except Exception as e:
                logger.error(f"❌ Error processing message: {e}")
                logger.debug(f"Traceback: {traceback.format_exc()}")
                raise
    
    async def start_consuming(self, queue_configs: Dict[str, Callable]):
        """
        开始消费多个队列
        
        Args:
            queue_configs: 队列名称到处理函数的映射
                例如: {"wxapi_messages": handle_wechat_message}
        """
        # 等待RabbitMQ服务可用
        if not await self.wait_for_rabbitmq():
            logger.error("❌ RabbitMQ service is not available")
            return False
        
        # 连接到RabbitMQ
        if not await self.connect():
            logger.error("❌ Failed to connect to RabbitMQ")
            return False
        
        try:
            # 设置所有队列的消费者
            success_count = 0
            for queue_name, callback in queue_configs.items():
                if await self.consume_queue(queue_name, callback):
                    success_count += 1
                else:
                    logger.error(f"❌ Failed to setup consumer for queue: {queue_name}")
            
            if success_count == 0:
                logger.error("❌ No consumers were set up successfully")
                return False
            
            self.is_running = True
            logger.info("✅ All consumers started. Service is running...")
            
            # 保持服务运行
            while self.is_running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Error during consumption setup: {e}")
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            return False
    
    async def stop_consuming(self):
        """停止消费消息"""
        self.is_running = False
        
        for queue_name, consumer_tag in self.consumer_tags.items():
            try:
                if self.channel and not self.channel.is_closed:
                    await self.channel.basic_cancel(consumer_tag)
            except Exception as e:
                logger.error(f"❌ Error stopping consumer for queue '{queue_name}': {e}")
        
        if self.connection and not self.connection.is_closed:
            await self.connection.close()
        
        logger.info("🔴 All consumers stopped")


# =============================================================================
# 消息处理函数
# =============================================================================

async def handle_wechat_message(message: str, msg_obj: AbstractIncomingMessage) -> bool:
    """
    处理微信消息
    
    Args:
        message: 消息内容
        msg_obj: 原始消息对象
        
    Returns:
        bool: 处理是否成功
    """
    try:
        logger.info(f"🔄 Processing WeChat message: {message[:200]}...")
        
        # 尝试解析JSON
        try:
            data = json.loads(message)
            logger.info(f"📋 Parsed JSON data keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            
            # 这里添加您的微信消息处理逻辑
            # 例如：
            # - 解析消息类型
            # - 处理文本消息、图片消息等
            # - 调用相应的业务逻辑
            # - 可能需要回复消息等
            
            await asyncio.sleep(0.1)  # 模拟处理时间
            
        except json.JSONDecodeError:
            logger.info(f"📝 Processing plain text message: {message}")
            # 处理纯文本消息
            await asyncio.sleep(0.05)

        return True
        
    except Exception as e:
        logger.error(f"❌ Error handling WeChat message: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False


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
    config = get_config()
    
    # 创建消费者
    consumer = WeChatRabbitMQConsumer(
        rabbitmq_url=config['rabbitmq_url'],
        max_retries=10
    )
    
    # 设置信号处理（优雅关闭）
    def signal_handler():
        logger.info("📡 Received signal to stop")
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
        logger.error(f"❌ Consumer error: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    finally:
        await consumer.stop_consuming()

if __name__ == "__main__":
    try:
        # 运行异步主函数
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🔴 Service interrupted by user")
    except Exception as e:
        logger.error(f"❌ Service error: {e}")
        sys.exit(1)