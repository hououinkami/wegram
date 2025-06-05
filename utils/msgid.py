import logging
logger = logging.getLogger(__name__)

import os
import json
from datetime import datetime, timedelta
import threading

class MappingManager:
    def __init__(self):
        """
        初始化映射管理器 - 实时存储版本
        """
        
        # 初始化
        print(f"✅ 初始化 MappingManager 实时存储版本 (ID: {id(self)})")
        self.base_path = "./msgid"
        # 内存缓存，用于快速查询最近的数据
        self.memory_cache = {}
        self.cache_lock = threading.RLock()  # 使用可重入锁
        
        # 确保目录存在
        if not os.path.exists(self.base_path):
            os.makedirs(self.base_path)
            
        # 加载今天的数据到内存缓存
        self._load_today_to_cache()

    def _load_today_to_cache(self):
        """
        将今天的数据加载到内存缓存中
        """
        today = datetime.now().strftime("%Y-%m-%d")
        file_path = self._get_file_path(today)
        
        with self.cache_lock:
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding='utf-8') as f:
                        data = json.load(f)
                    if "tgToWxMapping" in data:
                        self.memory_cache = data["tgToWxMapping"].copy()
                        logger.info(f"加载了 {len(self.memory_cache)} 条今日映射到内存缓存")
                except Exception as e:
                    logger.error(f"加载今日数据到缓存失败: {e}")
                    self.memory_cache = {}
            else:
                self.memory_cache = {}

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
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump({"tgToWxMapping": {}}, f, ensure_ascii=False)

    def add(self, tg_msg_id, from_wx_id, to_wx_id, wx_msg_id, client_msg_id, create_time, content):
        """
        添加TG消息ID到微信消息的映射 - 实时存储
        :param tg_msg_id: Telegram 消息ID（数字）
        :param wx_msg_id: 微信消息ID（数字）
        :param from_wx_id: 发送者微信ID（字符串）
        :param content: 消息内容（字符串）
        """
        today = datetime.now().strftime("%Y-%m-%d")
        tg_key = str(tg_msg_id)
        
        mapping_data = {
            "fromwxid": str(from_wx_id),
            "towxid": str(to_wx_id),
            "msgid": int(wx_msg_id),
            "clientmsgid": int(client_msg_id),
            "createtime": int(create_time),
            "content": str(content)
        }
        
        try:
            # 1. 先更新内存缓存
            with self.cache_lock:
                self.memory_cache[tg_key] = mapping_data
            
            # 2. 立即写入文件
            self._save_to_file_immediately(today, tg_key, mapping_data)
            
            logger.debug(f"成功添加映射: TG({tg_msg_id}) -> WX({wx_msg_id})")
            
        except Exception as e:
            logger.error(f"添加映射失败: {e}")
            # 如果文件写入失败，从缓存中移除
            with self.cache_lock:
                if tg_key in self.memory_cache:
                    del self.memory_cache[tg_key]

    def _save_to_file_immediately(self, date, tg_key, mapping_data):
        """
        立即将单条映射数据保存到文件
        :param date: 日期字符串
        :param tg_key: TG消息ID（字符串）
        :param mapping_data: 映射数据字典
        """
        file_path = self._get_file_path(date)
        self._ensure_file_exists(date)
        
        # 使用文件锁防止并发写入冲突
        lock_file = file_path + ".lock"
        
        # 简单的文件锁机制
        max_retries = 5
        for attempt in range(max_retries):
            try:
                # 检查锁文件
                if os.path.exists(lock_file):
                    if attempt < max_retries - 1:
                        threading.Event().wait(0.1)  # 等待100ms
                        continue
                    else:
                        logger.warning(f"文件锁超时，强制写入: {file_path}")
                
                # 创建锁文件
                with open(lock_file, 'w') as f:
                    f.write(str(os.getpid()))
                
                # 读取现有数据
                with open(file_path, "r", encoding='utf-8') as f:
                    existing_data = json.load(f)
                
                # 确保存在tgToWxMapping键
                if "tgToWxMapping" not in existing_data:
                    existing_data["tgToWxMapping"] = {}
                
                # 更新映射数据
                existing_data["tgToWxMapping"][tg_key] = mapping_data
                
                # 写回文件
                with open(file_path, "w", encoding='utf-8') as f:
                    json.dump(existing_data, f, indent=4, ensure_ascii=False)
                
                # 删除锁文件
                if os.path.exists(lock_file):
                    os.remove(lock_file)
                
                break  # 成功写入，退出重试循环
                
            except Exception as e:
                # 清理锁文件
                if os.path.exists(lock_file):
                    try:
                        os.remove(lock_file)
                    except:
                        pass
                
                if attempt == max_retries - 1:
                    raise e
                else:
                    logger.warning(f"文件写入失败，重试 {attempt + 1}/{max_retries}: {e}")
                    threading.Event().wait(0.1)

    def tg_to_wx(self, tg_msg_id):
        """
        根据TG消息ID获取对应的微信消息信息
        :param tg_msg_id: Telegram 消息ID（数字或字符串）
        :return: 微信消息字典 {"msgid": int, "fromwxid": str, "content": str} 或 None
        """
        tg_key = str(tg_msg_id)
        
        # 1. 首先检查内存缓存（最快）
        with self.cache_lock:
            if tg_key in self.memory_cache:
                return self.memory_cache[tg_key]
        
        # 2. 检查最近几天的文件
        today = datetime.now()
        for i in range(3):  # 搜索范围为当前日期往前 3 天
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            
            # 跳过今天（已经在缓存中检查过了）
            if i == 0:
                continue
                
            file_path = self._get_file_path(date)
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding='utf-8') as f:
                        data = json.load(f)
                    if ("tgToWxMapping" in data and 
                        tg_key in data["tgToWxMapping"]):
                        result = data["tgToWxMapping"][tg_key]
                        
                        # 将历史数据也加入缓存（可选优化）
                        with self.cache_lock:
                            self.memory_cache[tg_key] = result
                        
                        return result
                except Exception as e:
                    logger.error(f"读取文件 {file_path} 失败: {e}")
        
        return None

    def wx_to_tg(self, wx_msg_id):
        """
        根据微信消息ID反向查找TG消息ID
        :param wx_msg_id: 微信消息ID（数字或字符串）
        :return: TG消息ID（字符串）或 None
        """
        wx_id_int = int(wx_msg_id)
        
        # 1. 首先检查内存缓存
        with self.cache_lock:
            for tg_key, wx_msg in self.memory_cache.items():
                if wx_msg["msgid"] == wx_id_int:
                    return tg_key
        
        # 2. 检查文件
        today = datetime.now()
        for i in range(3):  # 搜索范围为当前日期往前 3 天
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            
            # 跳过今天（已经在缓存中检查过了）
            if i == 0:
                continue
                
            file_path = self._get_file_path(date)
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding='utf-8') as f:
                        data = json.load(f)
                    if "tgToWxMapping" in data:
                        for tg_key, wx_msg in data["tgToWxMapping"].items():
                            if wx_msg["msgid"] == wx_id_int:
                                return tg_key
                except Exception as e:
                    logger.error(f"读取文件 {file_path} 失败: {e}")
        
        return None

    def get_tg_id_by_wx_user(self, from_wx_id):
        """
        根据微信用户ID查找相关的TG消息ID列表
        :param from_wx_id: 微信用户ID
        :return: TG消息ID列表
        """
        result = []
        
        # 1. 检查内存缓存
        with self.cache_lock:
            for tg_key, wx_msg in self.memory_cache.items():
                if wx_msg["fromwxid"] == from_wx_id:
                    result.append(tg_key)
        
        # 2. 检查文件
        today = datetime.now()
        for i in range(3):  # 搜索范围为当前日期往前 3 天
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            
            # 跳过今天（已经在缓存中检查过了）
            if i == 0:
                continue
                
            file_path = self._get_file_path(date)
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding='utf-8') as f:
                        data = json.load(f)
                    if "tgToWxMapping" in data:
                        for tg_key, wx_msg in data["tgToWxMapping"].items():
                            if wx_msg["fromwxid"] == from_wx_id and tg_key not in result:
                                result.append(tg_key)
                except Exception as e:
                    logger.error(f"读取文件 {file_path} 失败: {e}")
        
        return result

    def get_stats(self):
        """
        获取映射统计信息
        :return: 统计信息字典
        """
        total_mappings = 0
        cache_count = 0
        
        # 统计内存缓存
        with self.cache_lock:
            cache_count = len(self.memory_cache)
        
        # 统计文件中的数据
        today = datetime.now()
        for i in range(7):  # 统计最近7天
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            file_path = self._get_file_path(date)
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding='utf-8') as f:
                        data = json.load(f)
                    if "tgToWxMapping" in data:
                        total_mappings += len(data["tgToWxMapping"])
                except Exception as e:
                    logger.error(f"读取统计文件 {file_path} 失败: {e}")
        
        return {
            "total_mappings": total_mappings,
            "cache_count": cache_count,
            "cache_hit_rate": f"{cache_count}/{total_mappings}" if total_mappings > 0 else "0/0"
        }

    def clear_old_cache(self, days_to_keep=1):
        """
        清理旧的缓存数据（可选功能）
        :param days_to_keep: 保留多少天的缓存
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")
        
        with self.cache_lock:
            # 这里可以根据需要实现更复杂的缓存清理逻辑
            # 目前只是简单地重新加载今天的数据
            if days_to_keep == 1:
                self._load_today_to_cache()

# 创建映射管理器实例
msgid_mapping = MappingManager()