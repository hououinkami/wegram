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
from api.telegram_sender import telegram_sender
from service.telethon_client import get_user_id
from utils.locales import Locale
from utils.wechat_to_telegram import process_rabbitmq_message

logger = logging.getLogger(__name__)
locale = Locale(config.LANG)

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
            
            # 开始消费 - 移除包装器，直接使用callback
            consumer_tag = await queue.consume(
                callback=lambda message: self._message_wrapper(message, callback),
                no_ack=False  # 手动确认
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
        queue_name = getattr(message, 'routing_key', None) or "未知"
        
        try:
            # 解码消息
            body = message.body.decode('utf-8')
            
            # 调用处理函数
            start_time = time.time()
            result = await callback(body, message)
            processing_time = time.time() - start_time
            
            if result:
                # 处理成功
                await message.ack()
            else:
                # 处理失败，但不是异常
                await message.nack(requeue=False)
                logger.warning(f"⚠️ 队列'{queue_name}'消息处理失败，消息已丢弃 (耗时: {processing_time:.2f}s)")
                
        except json.JSONDecodeError as e:
            # JSON解析错误
            await message.nack(requeue=False)
            logger.error(f"❌ 队列'{queue_name}'消息JSON格式错误: {e}")
            
        except Exception as e:
            # 其他异常
            try:
                await message.nack(requeue=False)
            except Exception as nack_error:
                logger.error(f"❌ 拒绝消息时出错: {nack_error}")
            
            logger.error(f"❌ 处理队列'{queue_name}'消息时出错: {e}")
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
            logger.info("✅ 所有消费者已启动，服务正在运行...")
            
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
        # 尝试解析JSON
        try:
            message_data = json.loads(message)
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON解析失败: {e}")
            return False

        # 检查是否在线
        await login_check(message_data)

        # 检查是否无新消息
        if message_data.get('Message') != "成功":
            return True
        
        # 获取消息列表
        add_msgs = message_data.get('Data', {}).get('AddMsgs', [])
        if not add_msgs:
            logger.debug("没有新消息")
            return True
        
        # 处理每条消息
        processed_count = 0
        failed_count = 0
        
        for msg in add_msgs:
            msg_id = msg.get('MsgId')
            if not msg_id:
                continue

            # 处理新消息
            try:
                await process_rabbitmq_message(msg)
                processed_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(f"❌ 处理消息 {msg_id} 失败: {e}")
        
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
    config = get_config()
    
    # 创建消费者
    consumer = WeChatRabbitMQConsumer(
        rabbitmq_url=config['rabbitmq_url'],
        max_retries=10
    )
    
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
