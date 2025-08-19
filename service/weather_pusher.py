import asyncio
import logging
import signal
import sys
from datetime import datetime
from typing import Optional

from utils import weather

logger = logging.getLogger(__name__)

class WeatherWarningService:
    """天气预警服务类"""
    
    def __init__(self):
        """初始化服务"""
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        
    async def run_weather_check(self):
        """执行一次天气检查"""
        try:            
            # 调用预警函数
            await weather.get_and_send_alert()
            
        except Exception as e:
            logger.error(f"执行天气检查时发生错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    async def service_loop_old(self, interval_minutes: int = 30):
        """原有服务主循环（备用）"""
        interval_seconds = interval_minutes * 60
        logger.info(f"☀️ 天气预警服务启动，每 {interval_seconds//60} 分钟检查一次")
        
        # 启动时立即执行一次
        await self.run_weather_check()
        
        while self.is_running:
            try:
                # 等待指定时间间隔
                await asyncio.sleep(interval_seconds)
                
                if self.is_running:  # 再次检查是否还在运行
                    await self.run_weather_check()
                    
            except asyncio.CancelledError:
                logger.info("服务循环被取消")
                break
            except Exception as e:
                logger.error(f"服务循环中发生错误: {e}")
                # 发生错误时等待一段时间再继续
                await asyncio.sleep(60)
    
    async def service_loop(self):
        """服务主循环"""
        logger.info("☀️ 天气预警服务启动，每小时的00、10、20、30、40、50分检查一次")
        
        while self.is_running:
            try:
                # 计算下次执行时间
                now = datetime.now()
                current_minute = now.minute
                
                # 找到下一个检查时间点
                check_minutes = [0, 10, 20, 30, 40, 50]
                next_minute = None
                
                for minute in check_minutes:
                    if minute > current_minute:
                        next_minute = minute
                        break
                
                # 如果当前时间已过50分，则等到下小时的00分
                if next_minute is None:
                    next_minute = 60  # 下小时的00分
                
                # 计算等待秒数
                wait_seconds = (next_minute - current_minute) * 60 - now.second
                if wait_seconds <= 0:
                    wait_seconds += 3600  # 加一小时
                
                await asyncio.sleep(wait_seconds)
                
                if self.is_running:
                    await self.run_weather_check()
                    
            except asyncio.CancelledError:
                logger.info("服务循环被取消")
                break
            except Exception as e:
                logger.error(f"服务循环中发生错误: {e}")
                # 发生错误时等待一段时间再继续
                await asyncio.sleep(60)
    
    async def start(self):
        """启动服务"""
        if self.is_running:
            logger.warning("服务已在运行中")
            return
            
        self.is_running = True
        self.task = asyncio.create_task(self.service_loop())
        
        try:
            await self.task
        except asyncio.CancelledError:
            logger.info("服务被取消")
        finally:
            self.is_running = False
    
    async def stop(self):
        """停止服务"""
        if not self.is_running:
            logger.warning("服务未在运行")
            return
            
        logger.info("正在停止天气预警服务...")
        self.is_running = False
        
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        logger.info("天气预警服务已停止")

class WeatherServiceManager:
    """服务管理器，处理信号和优雅关闭"""
    
    def __init__(self, service: WeatherWarningService):
        self.service = service
        self.shutdown_event = asyncio.Event()
    
    def signal_handler(self, signum, frame):
        """信号处理器"""
        logger.info(f"收到信号 {signum}，准备关闭服务...")
        self.shutdown_event.set()
    
    async def run(self):
        """运行服务管理器"""
        # 设置信号处理
        if sys.platform != 'win32':
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
        
        # 创建服务任务
        service_task = asyncio.create_task(self.service.start())
        
        try:
            # 等待关闭信号或服务完成
            done, pending = await asyncio.wait(
                [service_task, asyncio.create_task(self.shutdown_event.wait())],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # 如果是关闭信号触发的
            if self.shutdown_event.is_set():
                logger.info("收到关闭信号，正在停止服务...")
                await self.service.stop()
                
                # 取消服务任务
                if not service_task.done():
                    service_task.cancel()
                    try:
                        await service_task
                    except asyncio.CancelledError:
                        pass
            
            # 清理其他待处理的任务
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                    
        except KeyboardInterrupt:
            logger.info("收到键盘中断，正在停止服务...")
            await self.service.stop()

# 便捷函数
async def run_weather_service():
    """运行天气预警服务"""
    service = WeatherWarningService()
    manager = WeatherServiceManager(service)
    
    try:
        await manager.run()
    except Exception as e:
        logger.error(f"服务运行时发生错误: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        logger.info("天气预警服务已完全停止")

# 主函数
async def main():
    """主函数"""
    await run_weather_service()

if __name__ == "__main__":
    try:      
        # 运行服务
        asyncio.run(main())
        
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序启动失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
