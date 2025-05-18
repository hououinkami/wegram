#!/usr/bin/env python3
"""
微信消息接收服务
"""
import logging
# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

import http.server
import socketserver
import json
import os
import time
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional
from utils import message
import config

# 监控回调信息配置
PORT = config.PORT
API_KEY = config.API_KEY
SAVE_MESSAGES = False  # 是否保存消息到文件
SAVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

if SAVE_MESSAGES:
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)
        # 设置消息保存目录的权限
        try:
            subprocess.run(['chmod', '777', SAVE_DIR])
        except Exception as e:
            logger.warning(f"无法设置消息保存目录权限: {e}")
    else:
        # 确保已存在的目录也有正确权限
        try:
            subprocess.run(['chmod', '777', SAVE_DIR])
        except Exception as e:
            logger.warning(f"无法设置已存在消息目录的权限: {e}")

class WxMessageHandler(http.server.BaseHTTPRequestHandler):
    """处理微信消息的 HTTP 请求处理器"""
    
    def _send_response(self, status_code: int, message: Dict[str, Any]) -> None:
        """发送 JSON 响应"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(message).encode('utf-8'))
    
    def _read_request_body(self) -> bytes:
        """读取请求体"""
        content_length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(content_length)
    
    def _verify_auth(self) -> bool:
        """验证 API 密钥"""
        auth_header = self.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return False
        token = auth_header[7:]  # 去掉 'Bearer ' 前缀
        return token == API_KEY
    
    def _save_message_to_file(self, message_data: Dict[str, Any]) -> Optional[str]:
        """保存消息到日志文件中"""
        if not SAVE_MESSAGES:
            return None
        
        try:
            # 确保目录存在
            os.makedirs(SAVE_DIR, exist_ok=True)
            
            # 日志文件路径
            log_file = os.path.join(SAVE_DIR, "message.log")
            
            # 获取当前时间的格式化字符串
            current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            
            # 获取消息ID
            msg_id = message_data.get('MsgId', 'unknown')
            
            # 准备写入的内容
            header_line = f"[{current_time}] MsgId: {msg_id}\n"
            
            # 将消息内容格式化为字符串
            message_content = json.dumps(message_data, ensure_ascii=False, indent=2)
            
            # 写入日志文件（追加模式）
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(header_line)
                f.write(message_content)
                f.write("\n\n")  # 在每条消息之后添加空行，使日志更易读
                
            return log_file
        except Exception as e:
            logger.error(f"保存消息到日志文件失败: {e}")
            return None

    def _process_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理接收到的消息"""
        try:
            # 记录消息
            logger.info(f"收到消息: {json.dumps(message_data, ensure_ascii=False)}")
            
            # 保存消息到文件
            filename = self._save_message_to_file(message_data)
            if filename:
                logger.info(f"消息已保存到: {filename}")
            
            # 调用外部处理器
            message.process_message(message_data)
            
            # 根据消息类型处理
            msg_type = message_data.get('MsgType')
            sender = message_data.get('SenderNickName', message_data.get('SenderWxid', '未知用户'))
            
            if msg_type == 1:  # 文本消息
                content = message_data.get('Content', '')
                logger.info(f"处理文本消息: 来自 {sender}, 内容: {content}")
                # 在这里添加你的文本消息处理逻辑
                
            elif msg_type == 3:  # 图片消息
                logger.info(f"处理图片消息: 来自 {sender}")
                # 在这里添加你的图片消息处理逻辑
                
            elif msg_type == 34:  # 语音消息
                logger.info(f"处理语音消息: 来自 {sender}")
                # 在这里添加你的语音消息处理逻辑
                
            elif msg_type == 43:  # 视频消息
                logger.info(f"处理视频消息: 来自 {sender}")
                # 在这里添加你的视频消息处理逻辑
                
            elif msg_type == 49:  # 分享链接
                logger.info(f"处理分享链接: 来自 {sender}")
                # 在这里添加你的分享链接处理逻辑
                
            else:
                logger.info(f"收到其他类型消息: 类型={msg_type}, 来自={sender}")
                
            return {"success": True, "message": "消息已接收并处理"}
            
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            return {"success": False, "message": f"处理消息时出错: {str(e)}"}
    
    def do_POST(self):
        """处理 POST 请求"""
        if self.path == '/wx849/callback':
            # 验证 API 密钥
            if not self._verify_auth():
                logger.warning(f"认证失败: {self.headers.get('Authorization', '无认证头')}")
                self._send_response(401, {"success": False, "message": "认证失败"})
                return
            
            try:
                # 解析请求体
                request_body = self._read_request_body()
                message_data = json.loads(request_body.decode('utf-8'))
                
                # 处理消息
                result = self._process_message(message_data)
                
                # 发送响应
                status_code = 200 if result.get("success", False) else 500
                self._send_response(status_code, result)
                
            except json.JSONDecodeError:
                logger.error("无效的 JSON 数据")
                self._send_response(400, {"success": False, "message": "无效的 JSON 数据"})
            except Exception as e:
                logger.error(f"处理请求时出错: {e}")
                self._send_response(500, {"success": False, "message": f"服务器错误: {str(e)}"})
        else:
            self._send_response(404, {"success": False, "message": "未找到请求的资源"})
    
    def log_message(self, format, *args):
        """重写日志方法，使用我们自己的日志器"""
        logger.debug("%s - - [%s] %s" % (
            self.address_string(),
            self.log_date_time_string(),
            format % args
        ))

def run_server():
    """启动 HTTP 服务器"""
    try:
        # 使用 ThreadingTCPServer 支持多线程处理请求
        with socketserver.ThreadingTCPServer(("", PORT), WxMessageHandler) as httpd:
            logger.info(f"微信消息接收服务已启动，监听端口: {PORT}")
            logger.info(f"消息接收接口: http://localhost:{PORT}/wx849/callback")
            httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("服务器已停止")
    except Exception as e:
        logger.error(f"启动服务器时出错: {e}")


def main():
    """主函数，作为服务入口点"""
    logger.info("正在启动微信消息接收服务...")
    
    # 运行主服务
    run_server()

if __name__ == "__main__":
    main()