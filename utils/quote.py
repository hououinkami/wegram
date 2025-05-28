import os
import json
from datetime import datetime, timedelta
import threading

class MappingManager:
    def __init__(self, save_interval=60):
        """
        初始化映射管理器
        :param save_interval: 保存文件的时间间隔（单位：秒）
        """
        self.base_path = "./msgid"
        self.mapping = {
            "test_wx_id_1": "test_tg_id_1",
            "test_wx_id_2": "test_tg_id_2"
        }
        self.buffer = {}
        self.save_interval = save_interval
        self._start_auto_save()

    def _start_auto_save(self):
        """
        启动定时保存线程
        """
        def auto_save():
            while True:
                self._save_to_file()
                threading.Event().wait(self.save_interval)

        threading.Thread(target=auto_save, daemon=True).start()

    def _get_file_path(self, date):
        """
        获取指定日期的文件路径
        :param date: 日期字符串（格式：YYYY-MM-DD）
        :return: 文件路径
        """
        return os.path.join(self.base_path, f"{date}.json")

    def _ensure_file_exists(self, date):
        """
        确保指定日期的文件存在，不存在则新建
        :param date: 日期字符串（格式：YYYY-MM-DD）
        """
        file_path = self._get_file_path(date)
        if not os.path.exists(self.base_path):
            os.makedirs(self.base_path)
        if not os.path.exists(file_path):
            with open(file_path, "w") as f:
                json.dump({}, f)

    def add(self, wx_id, tg_id):
        """
        添加映射到缓冲区
        :param wx_id: 微信 ID
        :param tg_id: Telegram ID
        """
        today = datetime.now().strftime("%Y-%m-%d")
        self._ensure_file_exists(today)
        if today not in self.buffer:
            self.buffer[today] = {}
        self.buffer[today][wx_id] = tg_id

    def _save_to_file(self):
        """
        将缓冲区中的数据保存到文件
        """
        for date, data in self.buffer.items():
            file_path = self._get_file_path(date)
            self._ensure_file_exists(date)
            with open(file_path, "r") as f:
                existing_data = json.load(f)
            existing_data.update(data)
            with open(file_path, "w") as f:
                json.dump(existing_data, f, indent=4)
        self.buffer.clear()

    def tg_to_wx(self, tg_id):
        """
        映射 Telegram ID 到微信 ID（包含缓冲区搜索）
        :param tg_id: Telegram ID
        :return: 微信 ID 或 None
        """
        today = datetime.now()
        for i in range(3):  # 搜索范围为当前日期往前 3 天
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            
            # 首先检查缓冲区
            if date in self.buffer:
                for wx_id, mapped_tg_id in self.buffer[date].items():
                    if mapped_tg_id == tg_id:
                        return wx_id
            
            # 然后检查文件
            file_path = self._get_file_path(date)
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    data = json.load(f)
                for wx_id, mapped_tg_id in data.items():
                    if mapped_tg_id == tg_id:
                        return wx_id
        return None

    def wx_to_tg(self, wx_id):
        """
        映射微信 ID 到 Telegram ID（包含缓冲区搜索）
        :param wx_id: 微信 ID
        :return: Telegram ID 或 None
        """
        today = datetime.now()
        for i in range(3):  # 搜索范围为当前日期往前 3 天
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            
            # 首先检查缓冲区
            if date in self.buffer and wx_id in self.buffer[date]:
                return self.buffer[date][wx_id]
            
            # 然后检查文件
            file_path = self._get_file_path(date)
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    data = json.load(f)
                if wx_id in data:
                    return data[wx_id]
        return None

    def force_save(self):
        """
        手动触发保存（可选功能）
        """
        self._save_to_file()