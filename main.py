"""
WeGram服务
"""

import warnings
warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL 1.1.1+')

import asyncio
import importlib
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Dict, List

import config

class DailyRotatingHandler(RotatingFileHandler):
    """按天切换的日志处理器"""
    
    def __init__(self, log_dir, encoding='utf-8'):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        
        filename = self._get_filename()
        super().__init__(filename, mode='a', maxBytes=0, backupCount=0, encoding=encoding)
        self.current_date = datetime.now().strftime("%Y-%m-%d")
    
    def _get_filename(self):
        today = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"{today}.log")
    
    def shouldRollover(self, record):
        today = datetime.now().strftime("%Y-%m-%d")
        return today != self.current_date
    
    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.baseFilename = self._get_filename()
        
        if not self.delay:
            self.stream = self._open()

def setup_logging():
    """设置日志"""
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    
    # 清除现有处理器
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            DailyRotatingHandler(log_dir),
            logging.StreamHandler()
        ]
    )
    
    # 设置第三方库日志级别
    for logger_name in ['telethon', 'telethon.client.updates', 'telethon.network', 'httpx', 'aiohttp']:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    
    return logging.getLogger()

class ServiceManager:
    """服务管理器"""
    
    def __init__(self):
        self.logger = setup_logging()
        self.logger.info("服务管理器初始化完成")
        
        # 导入配置
        try:
            self.config = config
            self.logger.info("配置加载成功")
        except ImportError:
            self.logger.error("无法导入配置文件 config.py")
            sys.exit(1)
        
        self.service_threads = {}
        self.async_tasks = []
        self.shutdown_event = asyncio.Event()
        
        # 服务配置
        self.services_to_start = ["userbot", "login", "wx2tg", "tg2wx", "scheduler"]
        self.async_services = ["userbot", "login", "wx2tg", "tg2wx", "scheduler"]  # 需要异步运行的服务
    
    def start_file_monitor(self):
        """启动文件监控"""
        def monitor_task():
            config_path = os.path.join(os.path.dirname(__file__), "config.py")
            last_mtime = os.path.getmtime(config_path) if os.path.exists(config_path) else 0
            
            while True:
                try:
                    if os.path.exists(config_path):
                        current_mtime = os.path.getmtime(config_path)
                        if current_mtime > last_mtime:
                            importlib.reload(self.config)
                            self.logger.info("配置文件已重新加载")
                            last_mtime = current_mtime
                except Exception as e:
                    self.logger.error(f"监控配置文件出错: {e}")
                
                time.sleep(2)  # 2秒检查一次
        
        thread = threading.Thread(target=monitor_task, daemon=True)
        thread.start()
        return thread
    
    def get_available_services(self) -> List[str]:
        """获取可用服务列表"""
        service_dir = os.path.join(os.path.dirname(__file__), "service")
        if not os.path.exists(service_dir):
            self.logger.error(f"服务目录不存在: {service_dir}")
            return []
        
        services = []
        for file in os.listdir(service_dir):
            if file.endswith(".py") and not file.startswith("__"):
                services.append(file[:-3])
        
        return services
    
    def import_service(self, service_name):
        """导入服务模块"""
        try:
            module_name = f"service.{service_name}"
            if module_name in sys.modules:
                return sys.modules[module_name]
            return importlib.import_module(module_name)
        except Exception as e:
            self.logger.error(f"导入服务 {service_name} 失败: {e}")
            return None
    
    def start_sync_service(self, service_module, service_name):
        """启动同步服务"""
        def run_service():
            try:
                self.logger.info(f"启动同步服务: {service_name}")
                if asyncio.iscoroutinefunction(service_module.main):
                    # 异步函数在新事件循环中运行
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(service_module.main())
                else:
                    service_module.main()
            except Exception as e:
                self.logger.error(f"同步服务 {service_name} 出错: {e}")
        
        thread = threading.Thread(target=run_service, name=service_name, daemon=True)
        thread.start()
        return thread
    
    async def start_async_service(self, service_module, service_name):
        """启动异步服务"""
        try:
            self.logger.info(f"启动异步服务: {service_name}")
            if asyncio.iscoroutinefunction(service_module.main):
                await service_module.main()
            else:
                # 同步函数在线程池中运行
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, service_module.main)
        except asyncio.CancelledError:
            self.logger.info(f"异步服务 {service_name} 被取消")
            # 执行清理
            if hasattr(service_module, 'shutdown'):
                try:
                    if asyncio.iscoroutinefunction(service_module.shutdown):
                        await service_module.shutdown()
                    else:
                        service_module.shutdown()
                except Exception as e:
                    self.logger.error(f"关闭服务 {service_name} 时出错: {e}")
        except Exception as e:
            self.logger.error(f"异步服务 {service_name} 出错: {e}")
    
    def setup_signal_handlers(self):
        """设置信号处理器"""
        def signal_handler():
            self.logger.info("接收到终止信号，正在关闭服务...")
            self.shutdown_event.set()
        
        loop = asyncio.get_event_loop()
        try:
            for sig in [signal.SIGINT, signal.SIGTERM]:
                loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows 系统
            signal.signal(signal.SIGINT, lambda s, f: signal_handler())
            signal.signal(signal.SIGTERM, lambda s, f: signal_handler())
    
    async def run(self):
        """运行服务管理器"""
        self.logger.info("正在启动服务管理器...")
        
        # 启动文件监控
        self.start_file_monitor()
        
        # 获取可用服务
        available_services = self.get_available_services()
        self.logger.info(f"发现可用服务: {', '.join(available_services)}")
        
        # 启动服务
        sync_services = [s for s in self.services_to_start if s not in self.async_services]
        
        # 启动同步服务
        for service_name in sync_services:
            if service_name in available_services:
                service_module = self.import_service(service_name)
                if service_module:
                    thread = self.start_sync_service(service_module, service_name)
                    self.service_threads[service_name] = thread
        
        # 启动异步服务
        for service_name in self.async_services:
            if service_name in available_services:
                service_module = self.import_service(service_name)
                if service_module:
                    task = asyncio.create_task(
                        self.start_async_service(service_module, service_name),
                        name=service_name
                    )
                    self.async_tasks.append(task)
        
        if not self.service_threads and not self.async_tasks:
            self.logger.error("没有成功启动任何服务")
            return
        
        self.logger.info(f"已启动 {len(self.service_threads)} 个同步服务和 {len(self.async_tasks)} 个异步服务")
        
        # 设置信号处理
        self.setup_signal_handlers()
        
        # 监控服务状态
        try:
            await self.monitor_services()
        except KeyboardInterrupt:
            self.logger.info("接收到键盘中断")
        finally:
            await self.shutdown()
    
    async def monitor_services(self):
        """监控服务状态"""
        while not self.shutdown_event.is_set():
            # 检查异步任务
            for task in self.async_tasks[:]:
                if task.done():
                    if task.exception():
                        self.logger.error(f"异步服务 {task.get_name()} 异常退出: {task.exception()}")
                    else:
                        self.logger.warning(f"异步服务 {task.get_name()} 正常退出")
                    self.async_tasks.remove(task)
            
            # 检查同步服务
            for service_name, thread in list(self.service_threads.items()):
                if not thread.is_alive():
                    self.logger.warning(f"同步服务 {service_name} 已停止")
                    del self.service_threads[service_name]
            
            # 如果所有服务都停止了，退出
            if not self.async_tasks and not self.service_threads:
                self.logger.error("所有服务已停止")
                break
            
            await asyncio.sleep(1)
    
    async def shutdown(self):
        """关闭所有服务"""
        self.logger.info("正在关闭所有服务...")
        
        # 取消异步任务
        for task in self.async_tasks:
            task.cancel()
        
        if self.async_tasks:
            await asyncio.gather(*self.async_tasks, return_exceptions=True)
        
        # 等待同步服务
        for service_name, thread in self.service_threads.items():
            self.logger.info(f"等待服务 {service_name} 结束...")
            thread.join(timeout=5)
        
        self.logger.info("服务管理器已停止")

async def main():
    """主函数"""
    manager = ServiceManager()
    await manager.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")
    except Exception as e:
        print(f"程序运行出错: {e}")