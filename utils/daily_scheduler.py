import asyncio
import logging
import random
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class DailyRandomScheduler:
    """每日随机时间调度器"""
    
    def __init__(self, start_time, end_time, callback, run_once=False):
        self.original_start_time = self._parse_time(start_time)  # 保存原始开始时间
        self.original_end_time = self._parse_time(end_time)      # 保存原始结束时间
        self.start_time = self.original_start_time
        self.end_time = self.original_end_time
        self.callback = callback
        self.run_once = run_once  # 是否只执行一次
        self.is_running = False
        self.scheduler_task = None
        self.last_run_date = None
        self.has_executed = False  # 是否已执行过
        self.last_run_date = None
        
        if self.start_time >= self.end_time:
            raise ValueError("开始时间必须早于结束时间")
    
    def _parse_time(self, time_str):
        """解析时间字符串为秒数"""
        try:
            if time_str.count(':') == 1:  # HH:MM
                hours, minutes = map(int, time_str.split(':'))
                seconds = 0
            elif time_str.count(':') == 2:  # HH:MM:SS
                hours, minutes, seconds = map(int, time_str.split(':'))
            else:
                raise ValueError("时间格式错误")
            
            if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
                raise ValueError("时间值超出范围")
            
            return hours * 3600 + minutes * 60 + seconds
            
        except Exception as e:
            raise ValueError(f"时间格式错误: {time_str}，应为 'HH:MM' 或 'HH:MM:SS' 格式")
    
    def _format_time(self, total_seconds):
        """将秒数格式化为时间字符串"""
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def get_random_time_today(self):
        """获取今天指定范围内的随机时间"""
        today = datetime.now().date()
        
        # 在指定范围内生成随机秒数
        random_seconds = random.randint(self.start_time, self.end_time)
        
        # 转换为具体时间
        hours = random_seconds // 3600
        minutes = (random_seconds % 3600) // 60
        seconds = random_seconds % 60
        
        target_time = datetime.combine(today, datetime.min.time().replace(
            hour=hours, minute=minutes, second=seconds
        ))
        
        return target_time
    
    def adjust_time_range(self, hours_delay=1):
        """调整时间范围，往后推迟指定小时数"""
        self.start_time += hours_delay * 3600
        self.end_time += hours_delay * 3600
        
        # 如果超过了一天，重置为第二天的原始时间范围
        if self.end_time >= 24 * 3600:
            # 重置为明天的原始时间范围
            original_start = (self.start_time - hours_delay * 3600) % (24 * 3600)
            original_end = (self.end_time - hours_delay * 3600) % (24 * 3600)
            self.start_time = original_start
            self.end_time = original_end
            # 标记需要等到明天
            return True
        return False

    async def execute_task(self):
        """执行回调任务"""
        try:            
            if asyncio.iscoroutinefunction(self.callback):
                result = await self.callback()
            else:
                result = self.callback()
                
            # 如果是一次性任务且执行成功，标记为已执行并停止调度器
            if self.run_once and result is not False:
                self.has_executed = True
                logger.info("✅ 一次性任务执行完成，调度器将停止")
                self.is_running = False
                return
                
            # 对于一次性任务，如果执行失败也不调整时间范围，而是等待明天
            if self.run_once:
                if result is False:
                    logger.info("⚠️ 一次性任务执行失败，将在明天同一时间段重试")
                    self.last_run_date = datetime.now().date()
                else:
                    self.last_run_date = datetime.now().date()
                return
                
            # 普通循环任务的原有逻辑
            if result is False:
                need_wait_tomorrow = self.adjust_time_range(1)
                if need_wait_tomorrow:
                    logger.info(f"⏰ 时间范围已超过今天，等待明天重试")
                    self.last_run_date = datetime.now().date()
                else:
                    logger.info(f"⏰ 时间范围已调整为 {self._format_time(self.start_time)} - {self._format_time(self.end_time)}，稍后重试")
                    # 不设置last_run_date，让调度器继续在今天重试
            else:
                # 任务成功执行，记录今天已经执行过任务
                self.last_run_date = datetime.now().date()
            
        except Exception as e:
            logger.error(f"❌ 执行任务时发生错误: {e}")
            if self.run_once:
                # 一次性任务出错也等待明天重试
                self.last_run_date = datetime.now().date()

    async def _wait_with_cancellation(self, total_seconds):
        """可取消的等待函数"""
        while total_seconds > 0 and self.is_running:
            sleep_time = min(60, total_seconds)
            await asyncio.sleep(sleep_time)
            total_seconds -= sleep_time
    
    async def scheduler_loop(self):
        """调度器主循环"""
        while self.is_running:
            try:
                # 如果是一次性任务且已执行过，停止循环
                if self.run_once and self.has_executed:
                    logger.info("🎯 一次性任务已完成，调度器停止")
                    break
                    
                current_time = datetime.now()
                current_date = current_time.date()
                
                # 检查是否需要执行任务
                if self.last_run_date != current_date:
                    # 新的一天开始，重置时间范围为原始值
                    self.start_time = self.original_start_time
                    self.end_time = self.original_end_time
                    
                    target_time = self.get_random_time_today()
                    
                    if current_time >= target_time:
                        # 无论是一次性还是循环任务，错过时间都等待明天
                        task_type = "一次性任务" if self.run_once else "任务"
                        logger.info(f"⏰ {task_type}的执行时间 {target_time.strftime('%H:%M:%S')} 已过，等待明天在相同时间段执行")
                        self.last_run_date = current_date
                    else:
                        # 等待到目标时间
                        wait_seconds = (target_time - current_time).total_seconds()
                        task_type = "一次性任务" if self.run_once else "任务"
                        logger.info(f"⏰ 等待 {wait_seconds:.0f} 秒后执行{task_type} (目标时间: {target_time.strftime('%H:%M:%S')})")
                        
                        # 分段等待
                        await self._wait_with_cancellation(wait_seconds)
                        
                        # 执行任务
                        if self.is_running:
                            await self.execute_task()
                
                # 每分钟检查一次
                await self._wait_with_cancellation(60)
                    
            except asyncio.CancelledError:
                logger.info("⚠️ 调度器任务被取消")
                return
            except Exception as e:
                logger.error(f"❌ 调度器循环中发生错误: {e}")
                await self._wait_with_cancellation(60)
    
    async def start(self):
        """启动调度器"""
        if self.is_running:
            logger.warning("⚠️ 调度器已经在运行中")
            return
        
        self.is_running = True
        self.scheduler_task = asyncio.create_task(self.scheduler_loop())
        logger.info("✅ 每日随机调度器已启动")
    
    async def stop(self):
        """停止调度器"""
        if not self.is_running:
            return
        
        self.is_running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
        logger.info("🔴 每日随机调度器已停止")