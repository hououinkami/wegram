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
      
      # 初始化
      print(f"✅ 初始化 MappingManager 单例 (ID: {id(self)})")
      self.base_path = "./msgid"
      # 测试数据：tgmsgid -> wxmsg映射
      self.mapping = {
          "123456": {
              "msgid": 789012,
              "fromwxid": "test_wx_user_1",
              "content": "测试消息1"
          },
          "123457": {
              "msgid": 789013,
              "fromwxid": "test_wx_user_2", 
              "content": "测试消息2"
          }
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
          with open(file_path, "w", encoding='utf-8') as f:
              json.dump({"tgToWxMapping": {}}, f, ensure_ascii=False)

  def add(self, tg_msg_id, wx_msg_id, from_wx_id, content):
      """
      添加TG消息ID到微信消息的映射到缓冲区
      :param tg_msg_id: Telegram 消息ID（数字）
      :param wx_msg_id: 微信消息ID（数字）
      :param from_wx_id: 发送者微信ID（字符串）
      :param content: 消息内容（字符串）
      """
      today = datetime.now().strftime("%Y-%m-%d")
      self._ensure_file_exists(today)
      
      if today not in self.buffer:
          self.buffer[today] = {"tgToWxMapping": {}}
      
      # 将tg_msg_id转为字符串作为key
      tg_key = str(tg_msg_id)
      self.buffer[today]["tgToWxMapping"][tg_key] = {
          "msgid": int(wx_msg_id),
          "fromwxid": str(from_wx_id),
          "content": str(content)
      }

  def _save_to_file(self):
      """
      将缓冲区中的数据保存到文件
      """
      for date, data in self.buffer.items():
          file_path = self._get_file_path(date)
          self._ensure_file_exists(date)
          
          # 读取现有数据
          with open(file_path, "r", encoding='utf-8') as f:
              existing_data = json.load(f)
          
          # 确保存在tgToWxMapping键
          if "tgToWxMapping" not in existing_data:
              existing_data["tgToWxMapping"] = {}
          
          # 更新映射数据
          existing_data["tgToWxMapping"].update(data["tgToWxMapping"])
          
          # 写回文件
          with open(file_path, "w", encoding='utf-8') as f:
              json.dump(existing_data, f, indent=4, ensure_ascii=False)
      
      self.buffer.clear()

  def tg_to_wx(self, tg_msg_id):
      """
      根据TG消息ID获取对应的微信消息信息
      :param tg_msg_id: Telegram 消息ID（数字或字符串）
      :return: 微信消息字典 {"msgid": int, "fromwxid": str, "content": str} 或 None
      """
      tg_key = str(tg_msg_id)
      today = datetime.now()
      
      for i in range(3):  # 搜索范围为当前日期往前 3 天
          date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
          
          # 首先检查缓冲区
          if (date in self.buffer and 
              "tgToWxMapping" in self.buffer[date] and 
              tg_key in self.buffer[date]["tgToWxMapping"]):
              return self.buffer[date]["tgToWxMapping"][tg_key]
          
          # 然后检查文件
          file_path = self._get_file_path(date)
          if os.path.exists(file_path):
              with open(file_path, "r", encoding='utf-8') as f:
                  data = json.load(f)
              if ("tgToWxMapping" in data and 
                  tg_key in data["tgToWxMapping"]):
                  return data["tgToWxMapping"][tg_key]
      
      return None

  def wx_to_tg(self, wx_msg_id):
      """
      根据微信消息ID反向查找TG消息ID
      :param wx_msg_id: 微信消息ID（数字或字符串）
      :return: TG消息ID（字符串）或 None
      """
      wx_id_int = int(wx_msg_id)
      today = datetime.now()
      
      for i in range(3):  # 搜索范围为当前日期往前 3 天
          date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
          
          # 首先检查缓冲区
          if date in self.buffer and "tgToWxMapping" in self.buffer[date]:
              for tg_key, wx_msg in self.buffer[date]["tgToWxMapping"].items():
                  if wx_msg["msgid"] == wx_id_int:
                      return tg_key
          
          # 然后检查文件
          file_path = self._get_file_path(date)
          if os.path.exists(file_path):
              with open(file_path, "r", encoding='utf-8') as f:
                  data = json.load(f)
              if "tgToWxMapping" in data:
                  for tg_key, wx_msg in data["tgToWxMapping"].items():
                      if wx_msg["msgid"] == wx_id_int:
                          return tg_key
      
      return None

  def get_tg_id_by_wx_user(self, from_wx_id):
      """
      根据微信用户ID查找相关的TG消息ID列表
      :param from_wx_id: 微信用户ID
      :return: TG消息ID列表
      """
      result = []
      today = datetime.now()
      
      for i in range(3):  # 搜索范围为当前日期往前 3 天
          date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
          
          # 首先检查缓冲区
          if date in self.buffer and "tgToWxMapping" in self.buffer[date]:
              for tg_key, wx_msg in self.buffer[date]["tgToWxMapping"].items():
                  if wx_msg["fromwxid"] == from_wx_id:
                      result.append(tg_key)
          
          # 然后检查文件
          file_path = self._get_file_path(date)
          if os.path.exists(file_path):
              with open(file_path, "r", encoding='utf-8') as f:
                  data = json.load(f)
              if "tgToWxMapping" in data:
                  for tg_key, wx_msg in data["tgToWxMapping"].items():
                      if wx_msg["fromwxid"] == from_wx_id:
                          result.append(tg_key)
      
      return result

  def force_save(self):
      """
      手动触发保存（可选功能）
      """
      self._save_to_file()

  def get_stats(self):
      """
      获取映射统计信息
      :return: 统计信息字典
      """
      total_mappings = 0
      buffer_count = 0
      
      # 统计缓冲区
      for date_data in self.buffer.values():
          if "tgToWxMapping" in date_data:
              buffer_count += len(date_data["tgToWxMapping"])
      
      # 统计文件中的数据
      today = datetime.now()
      for i in range(7):  # 统计最近7天
          date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
          file_path = self._get_file_path(date)
          if os.path.exists(file_path):
              with open(file_path, "r", encoding='utf-8') as f:
                  data = json.load(f)
              if "tgToWxMapping" in data:
                  total_mappings += len(data["tgToWxMapping"])
      
      return {
          "total_mappings": total_mappings,
          "buffer_count": buffer_count,
          "total_with_buffer": total_mappings + buffer_count
      }

# 创建映射管理器实例
msgid_mapping = MappingManager()

# 使用示例
# msgid_mapping.add(
#     tg_msg_id=123458,
#     wx_msg_id=789014,
#     from_wx_id="user_001",
#     content="这是一条测试消息"
# )

# # 查询映射
# wx_msg = msgid_mapping.get_wx_msg_by_tg_id(123458)
# print(f"查询结果: {wx_msg}")

# # 反向查询
# tg_id = msgid_mapping.get_tg_id_by_wx_msg_id(789014)
# print(f"反向查询结果: {tg_id}")

# # 获取统计信息
# stats = msgid_mapping.get_stats()
# print(f"统计信息: {stats}")