import asyncio
import json
import logging
import time
from typing import Any, Dict, Set

from aiohttp import web

import config
from api.bot import telegram_sender
from service.telethon_client import get_user_id
from utils.locales import Locale
from utils.message import process_message

logger = logging.getLogger(__name__)

# 配置
PORT = config.PORT
WXID = config.MY_WXID
locale = Locale(config.LANG)

class MessageDeduplicator:
    """消息去重器 - 线程安全版本"""
    
    def __init__(self):
        self.processed_msg_ids: Set[int] = set()
        self._lock = asyncio.Lock()
        self.last_cleanup = time.time()
    
    async def is_duplicate(self, msg_id: int) -> bool:
        """检查消息是否重复"""
        async with self._lock:
            # 每小时清理一次过期记录
            current_time = time.time()
            if current_time - self.last_cleanup > 3600:
                await self._cleanup_old_records()
                self.last_cleanup = current_time
            
            if msg_id in self.processed_msg_ids:
                return True
            
            self.processed_msg_ids.add(msg_id)
            return False
    
    async def _cleanup_old_records(self):
        """清理过期记录，保持缓存大小合理"""
        if len(self.processed_msg_ids) > 5000:
            # 清理一半记录
            keep_count = len(self.processed_msg_ids) // 2
            self.processed_msg_ids = set(list(self.processed_msg_ids)[-keep_count:])
            logger.info(f"清理缓存，保留 {keep_count} 条记录")

# 全局去重器
deduplicator = MessageDeduplicator()

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
    """异步处理回调数据"""
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
        
        processed_count = 0
        duplicate_count = 0
        
        # 处理每条消息
        for msg in add_msgs:
            msg_id = msg.get('MsgId')
            if not msg_id:
                continue
            
            # 检查重复
            if await deduplicator.is_duplicate(msg_id):
                duplicate_count += 1
                logger.warning(f"跳过重复消息: {msg_id}")
                continue
            
            # 处理新消息
            try:
                await process_message(msg)
                processed_count += 1
            except Exception as e:
                logger.error(f"处理消息 {msg_id} 失败: {e}")
        
        return {
            "success": True,
            "message": f"处理 {processed_count} 条新消息，跳过 {duplicate_count} 条重复消息"
        }
        
    except Exception as e:
        logger.error(f"处理回调数据失败: {e}")
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
        logger.error(f"请求处理失败: {e}")
        return web.json_response(
            {"success": False, "message": "服务器错误"}, 
            status=500
        )

async def async_process_message(callback_data: Dict[str, Any]):
    """异步处理消息任务"""
    try:
        result = await process_callback_data(callback_data)
        if not result.get("success"):
            logger.error(f"异步处理失败: {result}")
    except Exception as e:
        logger.error(f"异步处理出错: {e}")

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
        logger.error(f"中间件处理错误: {e}")
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
        app = await create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        logger.info(f"微信消息服务启动, 端口: {PORT}, 路径: /msg/SyncMessage/{WXID}")
        
        # 保持服务运行
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("服务正在关闭...")
        finally:
            await runner.cleanup()
            
    except OSError as e:
        if e.errno == 48:
            logger.error(f"端口 {PORT} 已被占用")
        else:
            logger.error(f"网络错误: {e}")
    except Exception as e:
        logger.error(f"服务器错误: {e}")

async def main():
    """异步主函数"""
    logger.info("🚀 启动异步微信消息接收服务...")
    
    # 检查配置
    if not PORT or not WXID:
        logger.error("PORT 和 WXID 配置不能为空")
        return
    
    # 启动异步服务器
    await run_server()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭服务...")
    except Exception as e:
        logger.error(f"启动失败: {e}")
