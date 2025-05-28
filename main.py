#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
微信消息接收服务
"""

import os
import logging
from datetime import datetime

def setup_logging():
    """设置按天生成日志文件的配置"""
    # 获取当前脚本目录并创建 logs 文件夹
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 按天生成日志文件名
    today_date = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(log_dir, f"{today_date}.log")

    # 移除所有现有处理器
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 配置根日志记录器
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, mode='a', encoding='utf-8'),  # mode='a' 表示追加内容
            logging.StreamHandler()
        ]
    )

    # 确保所有现有的记录器都正确配置
    for logger_name in logging.root.manager.loggerDict:
        logger = logging.getLogger(logger_name)
        logger.propagate = True

    return logging.getLogger()

# 在导入任何模块前初始化日志
logger = setup_logging()
logger.info("微信消息接收服务日志系统初始化完成")

import sys
import time
import threading
import importlib
import signal
from typing import Dict, Any, Optional

# 导入配置
try:
    import config
    logger.info("配置加载成功")
except ImportError:
    logger.error("无法导入配置文件 config.py")
    sys.exit(1)

# 监控配置文件变更
def module_monitor_task():
    """
    监控 config.py 文件、api 文件夹和 utils 文件夹下的所有 Python 文件，
    并在文件更新时重新加载
    """
    
    # 当前目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # config.py 文件路径
    config_path = os.path.join(current_dir, "config.py")
    
    # 需要监控的文件夹
    folders = {
        "api": os.path.join(current_dir, "api"),
        "utils": os.path.join(current_dir, "utils")
    }
    
    # 存储所有需要监控的文件及其最后修改时间的字典
    file_mtimes: Dict[str, float] = {}
    
    # 初始化记录 config.py 文件的最后修改时间
    if os.path.exists(config_path):
        file_mtimes[config_path] = os.path.getmtime(config_path)
        logger.info("开始监控文件: config.py")
    else:
        logger.warning("config.py 文件不存在，无法监控")
    
    # 初始化监控文件夹中的 Python 文件
    for folder_name, folder_path in folders.items():
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            for filename in os.listdir(folder_path):
                if filename.endswith('.py'):
                    filepath = os.path.join(folder_path, filename)
                    file_mtimes[filepath] = os.path.getmtime(filepath)
                    module_name = f"{folder_name}.{filename[:-3]}"
                    logger.info(f"开始监控模块: {module_name}")
        else:
            logger.warning(f"{folder_name} 文件夹不存在，无法监控: {folder_path}")
    
    while True:
        try:
            # 检查所有监控的文件是否有更新
            for filepath, last_mtime in list(file_mtimes.items()):
                if os.path.exists(filepath):  # 确保文件仍然存在
                    current_mtime = os.path.getmtime(filepath)
                    if current_mtime > last_mtime:
                        # 文件已更新，尝试重新加载
                        if filepath == config_path:
                            # 处理 config.py
                            try:
                                importlib.reload(config)
                                logger.info("检测到 config.py 变更，已自动重新加载")
                            except Exception as e:
                                logger.error(f"重新加载 config.py 时出错: {e}")
                        else:
                            # 处理文件夹下的文件
                            for folder_name, folder_path in folders.items():
                                if filepath.startswith(folder_path):
                                    filename = os.path.basename(filepath)
                                    module_name = f"{folder_name}.{filename[:-3]}"
                                    
                                    # 检查模块是否已导入
                                    if module_name in sys.modules:
                                        try:
                                            importlib.reload(sys.modules[module_name])
                                            logger.info(f"检测到模块 {module_name} 变更，已自动重新加载")
                                        except Exception as e:
                                            logger.error(f"重新加载模块 {module_name} 时出错: {e}")
                                    break
                        
                        # 更新最后修改时间
                        file_mtimes[filepath] = current_mtime
                else:
                    # 文件已被删除，从监控列表中移除
                    logger.info(f"文件 {filepath} 已被删除，停止监控")
                    file_mtimes.pop(filepath)
            
            # 检查文件夹中是否有新增的 Python 文件
            for folder_name, folder_path in folders.items():
                if os.path.exists(folder_path) and os.path.isdir(folder_path):
                    for filename in os.listdir(folder_path):
                        if filename.endswith('.py'):
                            filepath = os.path.join(folder_path, filename)
                            if filepath not in file_mtimes:
                                # 新文件，添加到监控列表
                                file_mtimes[filepath] = os.path.getmtime(filepath)
                                logger.info(f"检测到 {folder_name} 文件夹中的新文件 {filename}，已添加到监控列表")
            
            # 检查 config.py 是否被重新创建
            if config_path not in file_mtimes and os.path.exists(config_path):
                file_mtimes[config_path] = os.path.getmtime(config_path)
                logger.info("检测到 config.py 已被创建，已添加到监控列表")
                
        except Exception as e:
            logger.error(f"监控文件时出错: {e}")
        
        time.sleep(1)  # 每秒检查一次

def start_module_monitor():
    """启动模块监控线程"""
    monitor_thread = threading.Thread(target=module_monitor_task, daemon=True)
    monitor_thread.start()
    return monitor_thread

# 动态导入服务模块
def import_service_module(module_name):
    """动态导入服务模块，不检测文件更新"""
    try:
        # 检查是否需要添加前缀
        if not module_name.startswith("service."):
            full_module_name = f"service.{module_name}"
        else:
            full_module_name = module_name
        
        # 导入模块
        if full_module_name in sys.modules:
            return sys.modules[full_module_name]
        else:
            module = importlib.import_module(full_module_name)
            logger.info(f"成功导入模块: {full_module_name}")
            return module
            
    except ImportError as e:
        logger.error(f"导入模块 {module_name} 失败: {e}")
        return None
    except Exception as e:
        logger.error(f"处理模块 {module_name} 时发生错误: {e}")
        return None

# 启动服务
def start_service(service_module, service_name):
    """启动服务"""
    if not service_module:
        logger.error(f"无法启动服务 {service_name}: 模块为空")
        return None
    
    # 检查模块是否有main函数
    if not hasattr(service_module, "main"):
        logger.error(f"服务模块 {service_name} 没有main函数")
        return None
    
    def _run_service():
        try:
            logger.info(f"正在启动服务: {service_name}")
            service_module.main()
        except Exception as e:
            logger.error(f"服务 {service_name} 运行出错: {e}")
    
    service_thread = threading.Thread(target=_run_service, name=service_name, daemon=True)
    service_thread.start()
    return service_thread

# 获取服务文件夹中所有可用的服务模块
def get_available_services():
    """获取service文件夹中所有的Python文件"""
    service_dir = os.path.join(os.path.dirname(__file__), "service")
    services = []
    
    if not os.path.exists(service_dir):
        logger.error(f"服务目录不存在: {service_dir}")
        return services
    
    for file in os.listdir(service_dir):
        if file.endswith(".py") and not file.startswith("__"):
            service_name = file[:-3]  # 去掉.py后缀
            services.append(service_name)
    
    return services

# 主函数
def main():
    """主函数"""
    logger.info("正在启动服务管理器...")
    
    # 启动配置监控
    monitor_thread = start_module_monitor()
    
    # 获取所有可用服务
    available_services = get_available_services()
    logger.info(f"发现可用服务: {', '.join(available_services)}")
    
    # 需要启动的服务
    services_to_start = ["wx2tg", "tg2wx", "login"]
    
    # 检查服务是否可用
    for service_name in services_to_start:
        if service_name not in available_services:
            logger.warning(f"服务 {service_name} 不在可用服务列表中")
    
    # 导入并启动服务
    service_threads = {}
    for service_name in services_to_start:
        service_module = import_service_module(service_name)
        if service_module:
            thread = start_service(service_module, service_name)
            if thread:
                service_threads[service_name] = thread
    
    if not service_threads:
        logger.error("没有成功启动任何服务")
        return
    
    logger.info(f"已启动 {len(service_threads)} 个服务")
    
    # 设置信号处理
    def signal_handler(sig, frame):
        logger.info("接收到终止信号，正在关闭服务...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 等待所有服务线程结束
    try:
        while True:
            # 检查服务线程是否存活
            for service_name, thread in list(service_threads.items()):
                if not thread.is_alive():
                    logger.warning(f"服务 {service_name} 已停止运行")
                    del service_threads[service_name]
            
            # 如果所有服务都停止了，退出主循环
            if not service_threads:
                logger.error("所有服务已停止运行")
                break
            
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("接收到键盘中断，正在关闭服务...")
    
    logger.info("服务管理器已停止")

if __name__ == "__main__":
    main()
