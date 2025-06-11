import logging
import random
import time
import threading
from datetime import datetime, timedelta
from api.base import wechat_api, telegram_api
from service.tg2wx import get_user_id
from utils.plugin import get_60s
import config

logger = logging.getLogger(__name__)

class DailyRandomScheduler:
    """每日随机时间调度器
    
    在指定的时间范围内随机选择一个时间执行回调函数
    """
    
    def __init__(self, start_time, end_time, callback):
        """
        初始化调度器
        
        Args:
            start_time (str): 开始时间，格式 "HH:MM" 或 "HH:MM:SS"
            end_time (str): 结束时间，格式 "HH:MM" 或 "HH:MM:SS"
            callback (callable): 回调函数，无参数
            
        Examples:
            # 在7:55到8:05之间随机执行
            scheduler = DailyRandomScheduler("07:55", "08:05", my_function)
            
            # 在9:00:00到9:30:30之间随机执行
            scheduler = DailyRandomScheduler("09:00:00", "09:30:30", my_function)
        """
        self.start_time = self._parse_time(start_time)
        self.end_time = self._parse_time(end_time)
        self.callback = callback
        self.is_running = False
        self.scheduler_thread = None
        self.last_run_date = None
        
        # 验证时间范围
        if self.start_time >= self.end_time:
            raise ValueError("开始时间必须早于结束时间")
        
        logger.info(f"📅 调度器初始化完成: {self._format_time(self.start_time)} - {self._format_time(self.end_time)}")
    
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
            
            # 验证时间范围
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
    
    def execute_task(self):
        """执行回调任务"""
        try:
            logger.info("🚀 开始执行调度任务")
            
            if callable(self.callback):
                self.callback()
                logger.info("✅ 任务执行成功")
            else:
                logger.error("❌ 回调函数不可调用")
                
            # 记录今天已经执行过任务
            self.last_run_date = datetime.now().date()
            
        except Exception as e:
            logger.error(f"❌ 执行任务时发生错误: {e}")
    
    def scheduler_loop(self):
        """调度器主循环"""        
        while self.is_running:
            try:
                current_time = datetime.now()
                current_date = current_time.date()
                
                # 检查是否需要执行任务
                if self.last_run_date != current_date:
                    # 获取今天的随机执行时间
                    target_time = self.get_random_time_today()
                    
                    logger.info(f"📅 今日执行时间: {target_time.strftime('%H:%M:%S')}")
                    
                    # 如果目标时间已经过了，立即执行
                    if current_time >= target_time:
                        self.execute_task()
                    else:
                        # 等待到目标时间
                        wait_seconds = (target_time - current_time).total_seconds()
                        logger.info(f"⏰ 等待 {wait_seconds:.0f} 秒后执行任务")
                        
                        # 分段等待，以便能够及时响应停止信号
                        while wait_seconds > 0 and self.is_running:
                            sleep_time = min(60, wait_seconds)  # 每次最多等待60秒
                            time.sleep(sleep_time)
                            wait_seconds -= sleep_time
                        
                        # 如果还在运行，执行任务
                        if self.is_running:
                            self.execute_task()
                
                # 每分钟检查一次
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"❌ 调度器循环中发生错误: {e}")
                time.sleep(60)  # 出错后等待1分钟再继续
    
    def start(self):
        """启动调度器"""
        if self.is_running:
            logger.warning("⚠️ 调度器已经在运行中")
            return
        
        self.is_running = True
        self.scheduler_thread = threading.Thread(target=self.scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        logger.info("✅ 每日随机调度器已启动")
    
    def stop(self):
        """停止调度器"""
        if not self.is_running:
            logger.warning("⚠️ 调度器未在运行")
            return
        
        self.is_running = False
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        logger.info("🛑 每日随机调度器已停止")
    
    def get_next_run_time(self):
        """获取下次运行时间信息"""
        if self.last_run_date == datetime.now().date():
            # 今天已经运行过，返回明天的时间
            tomorrow = datetime.now().date() + timedelta(days=1)
            return f"明天 {self._format_time(self.start_time)} - {self._format_time(self.end_time)} 之间"
        else:
            # 今天还没运行，返回今天的时间
            return f"今天 {self._format_time(self.start_time)} - {self._format_time(self.end_time)} 之间"
    
    def get_time_range(self):
        """获取时间范围"""
        return {
            'start_time': self._format_time(self.start_time),
            'end_time': self._format_time(self.end_time),
            'range_seconds': self.end_time - self.start_time
        }
    
    def is_in_time_range(self):
        """检查当前时间是否在执行范围内"""
        now = datetime.now()
        current_seconds = now.hour * 3600 + now.minute * 60 + now.second
        return self.start_time <= current_seconds <= self.end_time
    
    def execute_now(self):
        """立即执行一次任务（不影响正常调度）"""
        logger.info("🔧 手动执行任务")
        try:
            if callable(self.callback):
                self.callback()
                logger.info("✅ 手动执行成功")
            else:
                logger.error("❌ 回调函数不可调用")
        except Exception as e:
            logger.error(f"❌ 手动执行失败: {e}")

# 便捷函数
def create_daily_scheduler(start_time, end_time, callback):
    """创建每日随机调度器的便捷函数
    
    Args:
        start_time (str): 开始时间，格式 "HH:MM" 或 "HH:MM:SS"
        end_time (str): 结束时间，格式 "HH:MM" 或 "HH:MM:SS"
        callback (callable): 回调函数
        
    Returns:
        DailyRandomScheduler: 调度器实例
    """
    return DailyRandomScheduler(start_time, end_time, callback)

def start_daily_scheduler(start_time, end_time, callback):
    """创建并启动每日随机调度器的便捷函数
    
    Args:
        start_time (str): 开始时间，格式 "HH:MM" 或 "HH:MM:SS"
        end_time (str): 结束时间，格式 "HH:MM" 或 "HH:MM:SS"
        callback (callable): 回调函数
        
    Returns:
        DailyRandomScheduler: 已启动的调度器实例
    """
    scheduler = DailyRandomScheduler(start_time, end_time, callback)
    scheduler.start()
    return scheduler

def main():
    # 示例回调函数
    def get_news():
        """获取60s新闻"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        news = get_60s("both")

        payload = {
            "At": "",
            "Content": news['text'],
            "ToWxid": "ocean446",
            "Type": 1,
            "Wxid": config.MY_WXID
        }
        wechat_api("/Msg/SendTxt", payload)

        tg_user_id = get_user_id()
        telegram_api(tg_user_id, news['html'])

    # 在7:55到8:05之间随机执行
    scheduler = DailyRandomScheduler("07:55", "08:05", get_news)
    scheduler.start()
    
    try:
        # 保持程序运行
        while True:
            time.sleep(10)
            
    except KeyboardInterrupt:
        logger.info("\n🛑 正在停止调度器...")
        scheduler.stop()
        logger.info("✅ 调度器已停止")

if __name__ == "__main__":
    main()
    