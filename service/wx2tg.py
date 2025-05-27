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
WXID = config.MY_WXID
SAVE_MESSAGES = False  # 是否保存消息到文件
SAVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

def setup_save_directory():
    """设置消息保存目录"""
    if not SAVE_MESSAGES:
        return
        
    try:
        if not os.path.exists(SAVE_DIR):
            os.makedirs(SAVE_DIR, mode=0o755)  # 使用更安全的权限
            logger.info(f"创建消息保存目录: {SAVE_DIR}")
        else:
            # 确保目录权限正确
            os.chmod(SAVE_DIR, 0o755)
            logger.debug(f"设置消息目录权限: {SAVE_DIR}")
    except PermissionError:
        logger.error(f"没有权限创建或修改目录: {SAVE_DIR}")
    except Exception as e:
        logger.warning(f"设置消息保存目录时出错: {e}")

class WxMessageHandler(http.server.BaseHTTPRequestHandler):
    """处理微信消息的 HTTP 请求处理器"""
    
    def _send_response(self, status_code: int, message: Dict[str, Any]) -> None:
        """发送 JSON 响应"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')  # 允许跨域
        self.end_headers()
        self.wfile.write(json.dumps(message, ensure_ascii=False).encode('utf-8'))
    
    def _read_request_body(self) -> bytes:
        """读取请求体"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 10 * 1024 * 1024:  # 限制请求体大小为10MB
                raise ValueError("请求体过大")
            return self.rfile.read(content_length)
        except (ValueError, TypeError) as e:
            logger.error(f"读取请求体失败: {e}")
            raise
    
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
                f.write("\n" + "="*50 + "\n\n")  # 添加分隔线
                
            return log_file
        except IOError as e:
            logger.error(f"文件操作失败: {e}")
            return None
        except Exception as e:
            logger.error(f"保存消息到日志文件失败: {e}")
            return None

    def _process_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理接收到的消息"""
        try:
            # 验证消息数据
            if not isinstance(message_data, dict):
                raise ValueError("消息数据格式无效")
            
            # 记录消息
            logger.info(f"收到消息: {json.dumps(message_data, ensure_ascii=False)}")
            
            # 保存消息到文件
            filename = self._save_message_to_file(message_data)
            if filename:
                logger.info(f"消息已保存到: {filename}")
            
            # 调用外部处理器
            try:
                message.process_message(message_data)
            except Exception as e:
                logger.error(f"外部消息处理器出错: {e}")            
        except ValueError as e:
            logger.error(f"消息数据验证失败: {e}")
            return {"success": False, "message": f"消息数据无效: {str(e)}"}
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
            return {"success": False, "message": f"处理消息时出错: {str(e)}"}
    
    def do_POST(self):
        """处理 POST 请求"""
        if self.path == f"/msg/SyncMessage/{WXID}":
            try:
                # 解析请求体
                request_body = self._read_request_body()
                if not request_body:
                    self._send_response(400, {"success": False, "message": "请求体为空"})
                    return
                
                message_data = json.loads(request_body.decode('utf-8'))
                
                # 处理消息
                result = self._process_message(message_data)
                
                # 发送响应
                status_code = 200 if result.get("success", False) else 500
                self._send_response(status_code, result)
                
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析失败: {e}")
                self._send_response(400, {"success": False, "message": "无效的 JSON 数据"})
            except ValueError as e:
                logger.error(f"请求数据错误: {e}")
                self._send_response(400, {"success": False, "message": str(e)})
            except Exception as e:
                logger.error(f"处理请求时出错: {e}")
                self._send_response(500, {"success": False, "message": f"服务器错误: {str(e)}"})
        else:
            logger.warning(f"收到未知路径的请求: {self.path}")
            self._send_response(404, {"success": False, "message": "未找到请求的资源"})
    
    def do_OPTIONS(self):
        """处理 OPTIONS 请求（用于CORS预检）"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
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
        # 设置保存目录
        setup_save_directory()
        
        # 使用 ThreadingTCPServer 支持多线程处理请求
        with socketserver.ThreadingTCPServer(("", PORT), WxMessageHandler) as httpd:
            # 允许地址重用
            httpd.allow_reuse_address = True
            
            logger.info(f"微信消息接收服务已启动，监听端口: {PORT}")
            logger.info(f"消息接收接口: http://localhost:{PORT}/msg/SyncMessage/{WXID}")
            logger.info(f"消息保存功能: {'开启' if SAVE_MESSAGES else '关闭'}")
            if SAVE_MESSAGES:
                logger.info(f"消息保存目录: {SAVE_DIR}")
            
            httpd.serve_forever()
            
    except OSError as e:
        if e.errno == 48:  # Address already in use
            logger.error(f"端口 {PORT} 已被占用，请检查是否有其他服务在运行")
        else:
            logger.error(f"网络错误: {e}")
    except KeyboardInterrupt:
        logger.info("收到中断信号，服务器正在停止...")
    except Exception as e:
        logger.error(f"启动服务器时出错: {e}")

def main():
    """主函数，作为服务入口点"""
    logger.info("正在启动微信消息接收服务...")
    
    # 检查必要的配置
    try:
        if not PORT or not WXID:
            raise ValueError("PORT 和 WXID 配置不能为空")
        
        logger.info(f"配置检查通过: PORT={PORT}, WXID={WXID}")
        
        # 运行主服务
        run_server()
        
    except ValueError as e:
        logger.error(f"配置错误: {e}")
    except Exception as e:
        logger.error(f"服务启动失败: {e}")

if __name__ == "__main__":
    main()