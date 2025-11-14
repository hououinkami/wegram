import asyncio
import json
import logging
import time
from typing import Any, Dict, Set

from aiohttp import web

import config
from config import locale
from api.telegram_sender import telegram_sender
from service.telethon_client import get_user_id
from service.wechat_rabbitmq import MessageDeduplicator, ContactMessageProcessor
from utils.wechat_to_telegram import process_callback_message as process_rabbitmq_message

logger = logging.getLogger(__name__)

# 配置
PORT = config.PORT
WXID = config.MY_WXID

# 全局去重器和处理器管理
deduplicator = MessageDeduplicator(cache_size=1000, ttl=3600)  # 1小时过期
contact_processors: Dict[str, ContactMessageProcessor] = {}
processor_lock = asyncio.Lock()

# 统计信息
stats = {
  "total_messages": 0,
  "duplicate_messages": 0,
  "processed_messages": 0,
  "failed_messages": 0
}

async def get_or_create_processor(contact_id: str) -> ContactMessageProcessor:
    """获取或创建联系人处理器"""
    async with processor_lock:
        if contact_id not in contact_processors:
            processor = ContactMessageProcessor(contact_id)
            await processor.start()
            contact_processors[contact_id] = processor
            logger.debug(f"📝 为联系人 {contact_id} 创建新的处理器")
        return contact_processors[contact_id]

async def cleanup_idle_processors():
    """清理空闲的处理器"""
    while True:
        try:
            await asyncio.sleep(300)  # 每5分钟检查一次
            
            async with processor_lock:
                current_time = time.time()
                idle_contacts = []
                
                for contact_id, processor in contact_processors.items():
                    # 检查队列是否为空且最后活动时间超过10分钟
                    if (processor.message_queue.empty() and 
                        current_time - processor.last_activity > 600):  # 10分钟无活动
                        idle_contacts.append(contact_id)
                
                # 只清理长时间无活动的处理器，保留活跃的
                for contact_id in idle_contacts[:10]:  # 限制每次最多清理10个
                    processor = contact_processors.pop(contact_id)
                    await processor.stop()
                    logger.debug(f"🧹 清理空闲处理器: {contact_id}")
                    
        except Exception as e:
            logger.error(f"❌ 清理处理器时出错: {e}")

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

async def process_callback_data(callback_data: Dict[str, Any]) -> Dict[str, Any]:
    """异步处理回调数据 - 采用与RabbitMQ一致的处理方式"""
    try:
        # 检查是否在线
        await login_check(callback_data)
        
        # 检查是否无新消息
        if callback_data.get('Message') != "成功":
            return {"success": True, "message": "无新消息"}
        
        # 获取消息列表
        add_msgs = callback_data.get('Data', {}).get('AddMsgs', [])
        if not add_msgs:
            return {"success": True, "message": "无消息"}
        
        # 处理每条消息 - 改进去重逻辑
        processed_count = 0
        failed_count = 0
        duplicate_count = 0
        
        for msg in add_msgs:
            msg_id = msg.get('MsgId')
            from_wxid = msg.get('FromUserName', {}).get('string', '')
            
            if not msg_id or not from_wxid:
                continue
            
            stats["total_messages"] += 1
            
            # 使用复合键进行去重，包含消息ID
            msg_key = f"{msg_id}"
            
            # 先检查去重，立即标记为处理中
            if deduplicator.is_duplicate(msg_key):
                duplicate_count += 1
                stats["duplicate_messages"] += 1
                logger.warning(f"🔄 跳过重复消息: {msg_id} (来自: {from_wxid})")
                continue

            try:
                # 立即标记为已处理，防止竞态条件
                deduplicator.mark_processed(msg_key)
                
                # 获取或创建该联系人的处理器
                processor = await get_or_create_processor(from_wxid)
                # 只传递单个消息数据
                await processor.add_message(msg)
                
                stats["processed_messages"] += 1
                processed_count += 1
                    
            except Exception as e:
                failed_count += 1
                stats["failed_messages"] += 1
                logger.error(f"❌ 分发消息 {msg_id} 到联系人 {from_wxid} 失败: {e}")
                
                # 处理失败时，从去重缓存中移除，允许重试
                try:
                    # 从已处理消息中移除，允许后续重试
                    if msg_key in deduplicator.processed_messages:
                        del deduplicator.processed_messages[msg_key]
                except Exception as cleanup_error:
                    logger.error(f"清理失败消息缓存时出错: {cleanup_error}")
        
        # 记录处理结果
        if duplicate_count > 0:
            logger.info(f"📊 消息处理完成 - 处理: {processed_count}, 失败: {failed_count}, 重复: {duplicate_count}")
        elif processed_count > 0 or failed_count > 0:
            logger.debug(f"📊 消息处理完成 - 处理: {processed_count}, 失败: {failed_count}")
        
        return {
            "success": True,
            "message": f"处理 {processed_count} 条新消息，跳过 {duplicate_count} 条重复消息，失败 {failed_count} 条"
        }
        
    except Exception as e:
        logger.error(f"❌ 处理回调数据失败: {e}")
        stats["failed_messages"] += 1
        return {"success": False, "message": str(e)}

async def handle_message(request):
    """处理微信消息的异步处理器"""
    try:
        # 检查请求体大小
        if request.content_length and request.content_length > 5 * 1024 * 1024:
            return web.json_response(
                {"success": False, "message": "请求体过大"}, 
                status=400
            )
        
        # 读取请求体
        try:
            callback_data = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "message": "JSON格式错误"}, 
                status=400
            )
        
        # 立即响应，避免重试
        response = web.json_response({"success": True, "message": "已接收"})
        
        # 异步处理消息（不等待结果）
        asyncio.create_task(async_process_message(callback_data))
        
        return response
        
    except Exception as e:
        logger.error(f"❌ 请求处理失败: {e}")
        return web.json_response(
            {"success": False, "message": "服务器错误"}, 
            status=500
        )

async def async_process_message(callback_data: Dict[str, Any]):
    """异步处理消息任务"""
    try:
        result = await process_callback_data(callback_data)
        if not result.get("success"):
            logger.error(f"❌ 异步处理失败: {result}")
    except Exception as e:
        logger.error(f"❌ 异步处理出错: {e}")

async def handle_options(request):
    """处理OPTIONS请求"""
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type'
    }
    return web.Response(headers=headers)

@web.middleware
async def cors_middleware(request, handler):
    """CORS 中间件"""
    try:
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
    except Exception as e:
        logger.error(f"❌ 中间件处理错误: {e}")
        return web.json_response(
            {"success": False, "message": "中间件错误"}, 
            status=500
        )

async def create_app():
    """创建aiohttp应用"""
    app = web.Application(middlewares=[cors_middleware])
    
    # 添加路由 - 移除路径检查，因为路由已经处理了
    app.router.add_post(f"/msg/SyncMessage/{WXID}", handle_message)
    app.router.add_options(f"/msg/SyncMessage/{WXID}", handle_options)
    
    # 添加健康检查路由
    async def health_check(request):
        return web.json_response({"status": "healthy", "service": "wx2tg"})
    
    app.router.add_get("/health", health_check)
    
    return app

async def run_server():
    """启动异步服务器"""
    try:
        # 启动清理任务
        cleanup_task = asyncio.create_task(cleanup_idle_processors())
        
        app = await create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        logger.info(f"✅ 微信消息服务启动, 端口: {PORT}, 路径: /msg/SyncMessage/{WXID}")
        
        # 保持服务运行
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("⚠️ 服务正在关闭...")
        finally:
            # 停止清理任务
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            
            # 停止所有联系人处理器
            async with processor_lock:
                for processor in contact_processors.values():
                    await processor.stop()
                contact_processors.clear()
            
            await runner.cleanup()
            
    except OSError as e:
        if e.errno == 48:
            logger.error(f"⚠️ 端口 {PORT} 已被占用")
        else:
            logger.error(f"❌ 网络错误: {e}")
    except Exception as e:
        logger.error(f"❌ 服务器错误: {e}")

async def main():
    """异步主函数"""    
    # 检查配置
    if not PORT or not WXID:
        logger.error("❌ PORT 和 WXID 配置不能为空")
        return
    
    # 启动异步服务器
    await run_server()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⚠️ 收到中断信号，正在关闭服务...")
    except Exception as e:
        logger.error(f"❌ 启动失败: {e}")
