import asyncio
import logging
import os
import signal
import sys
from datetime import datetime
from typing import Optional, Callable, Any

import config
from api.wechat_api import wechat_api
from utils.moments import WeChatMomentsExtractor, process_moment_data

logger = logging.getLogger(__name__)

class WeChatMomentsMonitorService:
    """
    微信朋友圈异步监控服务
    每半小时检查一次增量更新
    """
    
    def __init__(self, 
                check_interval: int = 1800,  # 30分钟 = 1800秒
                storage_file: str = None):
        """
        初始化监控服务
        
        Args:
            check_interval: 检查间隔（秒），默认30分钟
            storage_file: 时间戳存储文件路径
        """
        self.check_interval = check_interval
        if storage_file is None:
            # 默认数据库路径
            self.storage_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "database", 
                "moments.txt"
            )
        else:
            self.storage_file = storage_file
        self.extractor = WeChatMomentsExtractor(self.storage_file)
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        # 缓存最后处理时间戳
        self._cached_last_create_time: Optional[int] = None
        self._cache_dirty = True  # 标记缓存是否需要更新
        
        # 关闭事件
        self.shutdown_event = asyncio.Event()

        try:
            os.makedirs(os.path.dirname(self.storage_file), exist_ok=True)
        except Exception as e:
            logger.warning(f"创建存储目录失败: {e}")
        
    def _load_cache(self):
        """从文件加载缓存"""
        self._cached_last_create_time = self.extractor.get_last_create_time()
        self._cache_dirty = False
       
    def _update_cache(self, new_create_time: int):
        """更新缓存"""
        if new_create_time != self._cached_last_create_time:
            self._cached_last_create_time = new_create_time
            self._cache_dirty = True

    async def _fetch_moments_data(self) -> Optional[dict]:
        """
        异步获取朋友圈数据
        
        Returns:
            API响应数据或None（如果出错）
        """
        try:
            payload = {
                "Fristpagemd5": "",
                "Maxid": 0,
                "Wxid": config.MY_WXID
            }
            
            logger.debug(f"正在获取朋友圈数据，WXID: {config.MY_WXID}")

            response = await wechat_api("MY_MOMENT", payload)

            # 检查API是否调用成功
            if response is False:
                logger.error("微信API调用失败")
                return None
                
            # 确保返回的是字典类型
            if not isinstance(response, dict):
                logger.error(f"API返回数据格式错误，期望dict，实际: {type(response)}")
                return None
                
            return response
            
        except Exception as e:
            logger.error(f"获取朋友圈数据失败: {e}")
            return None
    
    async def _process_new_data(self, new_data: list):
        """
        处理新的朋友圈数据
        
        Args:
            new_data: 新的朋友圈数据列表
        """
        if not new_data:
            return
        
        # 记录详细信息
        for item in new_data:
            await process_moment_data(item)
            logger.debug(
                f"新朋友圈 - ID: {item['Id']}, "
                f"用户: {item['Username']}, "
                f"时间: {item['CreateTime']}, "
                f"点赞数: {item['LikeCount']}"
            )
        
        # 这里处理增量数据
        await process_moment_data(new_data)
    
    async def _check_updates(self):
        """单次检查更新"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 显示当前状态
            last_time = self.extractor.get_last_create_time_formatted()
            
            # 获取朋友圈数据
            moment_list = await self._fetch_moments_data()
            if moment_list is None:
                logger.warning("获取朋友圈数据失败，跳过本次检查")
                return
            
            # 增量提取新数据
            new_data = self.extractor.extract_incremental_data(moment_list)
            
            if new_data:
                await self._process_new_data(new_data)
                updated_time = self.extractor.get_last_create_time_formatted()
                
        except Exception as e:
            logger.error(f"检查更新时发生错误: {e}")
    
    async def _monitor_loop(self):
        """主监控循环"""
        logger.info(f"🟢 朋友圈监控服务已启动，检查间隔: {self.check_interval}秒 ({self.check_interval//60}分钟)")
        
        while self.is_running:
            try:
                # 执行检查
                await self._check_updates()
                
                # 等待下次检查或关闭信号
                if self.is_running:
                    try:
                        await asyncio.wait_for(
                            self.shutdown_event.wait(), 
                            timeout=self.check_interval
                        )
                        # 如果收到关闭信号，退出循环
                        break
                    except asyncio.TimeoutError:
                        # 超时是正常的，继续下次检查
                        continue
                    
            except asyncio.CancelledError:
                logger.info("监控任务被取消")
                break
            except Exception as e:
                logger.error(f"监控循环发生错误: {e}")
                # 发生错误时等待较短时间后重试
                if self.is_running:
                    try:
                        await asyncio.wait_for(self.shutdown_event.wait(), timeout=60)
                        break
                    except asyncio.TimeoutError:
                        continue
    
    def _setup_signal_handlers(self):
        """设置信号处理器"""
        def signal_handler():
            print("\n收到停止信号，正在优雅关闭服务...")
            self.shutdown_event.set()
            
        if sys.platform != 'win32':
            # Unix系统信号处理
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, signal_handler)
    
    async def start(self):
        """启动监控服务"""
        if self.is_running:
            logger.warning("监控服务已在运行中")
            return
            
        self.is_running = True
        self.shutdown_event.clear()
        
        # 设置信号处理器
        self._setup_signal_handlers()
        
        self.task = asyncio.create_task(self._monitor_loop())
        
        try:
            await self.task
        except asyncio.CancelledError:
            logger.info("监控服务被停止")
        except KeyboardInterrupt:
            print("\n收到键盘中断，正在停止服务...")
        finally:
            self.is_running = False
    
    async def stop(self):
        """停止监控服务"""
        if not self.is_running:
            logger.warning("监控服务未在运行")
            return
            
        logger.info("正在停止监控服务...")
        self.is_running = False
        self.shutdown_event.set()
        
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        logger.info("监控服务已停止")
    
    def get_status(self) -> dict:
        """
        获取服务状态
        
        Returns:
            包含服务状态信息的字典
        """
        return {
            "is_running": self.is_running,
            "check_interval_minutes": self.check_interval // 60,
            "last_create_time": self.extractor.get_last_create_time_formatted()
        }

async def main():
    """主函数"""
    # 创建监控服务
    service = WeChatMomentsMonitorService()
    
    # 添加数据处理器

    
    # 启动服务
    await service.start()


if __name__ == "__main__":
    # 运行服务
    asyncio.run(main())