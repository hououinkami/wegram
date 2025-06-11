#!/usr/bin/env python3
"""
微信消息接收服务
"""
import logging
logger = logging.getLogger(__name__)

import http.server
import socketserver
import json
import time
import threading
from typing import Dict, Any, Set
from api.base import telegram_api
from utils.message import process_message
from utils.locales import Locale
from service.tg2wx import get_user_id
import config

# 配置
PORT = config.PORT
WXID = config.MY_WXID

class MessageDeduplicator:
    """消息去重器"""
    
    def __init__(self):
        self.processed_msg_ids: Set[int] = set()
        self._lock = threading.RLock()
        self.last_cleanup = time.time()
    
    def is_duplicate(self, msg_id: int) -> bool:
        """检查消息是否重复"""
        with self._lock:
            # 每小时清理一次过期记录
            current_time = time.time()
            if current_time - self.last_cleanup > 3600:
                self._cleanup_old_records()
                self.last_cleanup = current_time
            
            if msg_id in self.processed_msg_ids:
                return True
            
            self.processed_msg_ids.add(msg_id)
            return False
    
    def _cleanup_old_records(self):
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
locale = Locale(config.LANG)

def login_check(callback_data):
    global login_status
    
    current_message = callback_data.get('Message')
    
    tg_user_id = get_user_id()
    if current_message == "用户可能退出":
        # 只有当上一次状态不是离线时才发送离线提示
        if login_status != "offline":
            telegram_api(tg_user_id, locale.common['offline'])
            login_status = "offline"
        return {"success": True, "message": "用户可能退出"}
    
    else:
        # 当前不是离线状态
        # 如果上一次是离线状态，发送上线提示
        if login_status == "offline":
            telegram_api(tg_user_id, locale.common['online'])
        login_status = "online"
        return {"success": True, "message": "正常状态"}

class WxMessageHandler(http.server.BaseHTTPRequestHandler):
    """微信消息处理器"""
    
    def _send_response(self, status_code: int, message: Dict[str, Any]) -> None:
        """发送响应"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(message, ensure_ascii=False).encode('utf-8'))
    
    def _read_request_body(self) -> bytes:
        """读取请求体"""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 5 * 1024 * 1024:  # 限制5MB
            raise ValueError("请求体过大")
        return self.rfile.read(content_length)
    
    def _process_callback_data(self, callback_data: Dict[str, Any]) -> Dict[str, Any]:
        # logger.warning(f"#####回调数据：{callback_data}")
        """处理回调数据"""
        try:
            # 检查是否在线
            login_check(callback_data)
            
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
                if deduplicator.is_duplicate(msg_id):
                    duplicate_count += 1
                    logger.warning(f"跳过重复消息: {msg_id}")
                    continue
                
                # 处理新消息
                try:
                    process_message(msg)
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
    
    def do_POST(self):
        """处理POST请求"""
        if self.path != f"/msg/SyncMessage/{WXID}":
            self._send_response(404, {"success": False, "message": "路径不存在"})
            return
        
        try:
            # 读取和解析请求
            request_body = self._read_request_body()
            if not request_body:
                self._send_response(400, {"success": False, "message": "请求体为空"})
                return
            
            callback_data = json.loads(request_body.decode('utf-8'))
            
            # 立即响应，避免重试
            self._send_response(200, {"success": True, "message": "已接收"})
            
            # 异步处理消息
            threading.Thread(
                target=self._async_process,
                args=(callback_data,),
                daemon=True
            ).start()
            
        except json.JSONDecodeError:
            self._send_response(400, {"success": False, "message": "JSON格式错误"})
        except Exception as e:
            logger.error(f"请求处理失败: {e}")
            self._send_response(500, {"success": False, "message": "服务器错误"})
    
    def _async_process(self, callback_data: Dict[str, Any]):
        """异步处理消息"""
        try:
            result = self._process_callback_data(callback_data)
            if not result.get("success"):
                logger.error(f"异步处理失败: {result}")
        except Exception as e:
            logger.error(f"异步处理出错: {e}")
    
    def do_OPTIONS(self):
        """处理OPTIONS请求"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def log_message(self, format, *args):
        """禁用默认HTTP日志"""
        pass

def run_server():
    """启动服务器"""
    try:
        with socketserver.ThreadingTCPServer(("", PORT), WxMessageHandler) as httpd:
            httpd.allow_reuse_address = True
            logger.info(f"微信消息服务启动: http://localhost:{PORT}/msg/SyncMessage/{WXID}")
            httpd.serve_forever()
            
    except OSError as e:
        if e.errno == 48:
            logger.error(f"端口 {PORT} 已被占用")
        else:
            logger.error(f"网络错误: {e}")
    except KeyboardInterrupt:
        logger.info("服务停止")
    except Exception as e:
        logger.error(f"服务器错误: {e}")

def main():
    """主函数"""
    logger.info("🚀 启动微信消息接收服务...")
    
    # 检查配置
    if not PORT or not WXID:
        logger.error("PORT 和 WXID 配置不能为空")
        return
    
    # 直接启动 HTTP 服务器（不创建事件循环）
    run_server()


if __name__ == "__main__":
    main()
