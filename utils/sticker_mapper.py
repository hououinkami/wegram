import json
import logging
import os
import time

logger = logging.getLogger(__name__)

class StickerManager:
    def __init__(self, json_path):
        self.json_path = json_path
        self.sticker_data = {}
        self.last_modified_time = 0
        self.last_check_time = 0
        self.check_interval = 60  # 检查文件更新的间隔时间（秒）
        self.load_sticker_data()
    
    def load_sticker_data(self):
        """加载贴纸数据"""
        try:
            if os.path.exists(self.json_path):
                current_modified_time = os.path.getmtime(self.json_path)
                
                # 如果文件被修改，则重新加载
                if current_modified_time > self.last_modified_time:
                    with open(self.json_path, 'r', encoding='utf-8') as f:
                        self.sticker_data = json.load(f)
                    
                    self.last_modified_time = current_modified_time
                    logger.info(f"已加载贴纸数据，共 {len(self.sticker_data.get('stickerToEmojiMap', {}))} 个贴纸")
                    return True
                return False
            else:
                logger.warning(f"贴纸配置文件不存在: {self.json_path}")
                return False
        except Exception as e:
            logger.error(f"加载贴纸数据失败: {str(e)}")
            return False
    
    def check_and_reload(self):
        """检查文件是否更新，如果更新则重新加载"""
        current_time = time.time()
        
        # 检查间隔时间是否已过
        if current_time - self.last_check_time >= self.check_interval:
            self.last_check_time = current_time
            return self.load_sticker_data()
        return False
    
    def get_sticker_info(self, file_unique_id):
        """获取贴纸信息"""
        # 检查是否需要重新加载数据
        self.check_and_reload()
        
        # 从数据中查找匹配的贴纸
        sticker_map = self.sticker_data.get("stickerToEmojiMap", {})
        return sticker_map.get(file_unique_id)
    
    def get_sticker_id_by_md5(self, md5_hash):
        """
        根据MD5值查找贴纸的unique_id
        
        Args:
            md5_hash (str): 要查找的MD5值
        
        Returns:
            str or None: 找到返回file_unique_id，未找到返回None
        """
        # 检查是否需要重新加载数据
        self.check_and_reload()
        
        sticker_map = self.sticker_data.get("stickerToEmojiMap", {})
        
        for file_unique_id, sticker_info in sticker_map.items():
            if sticker_info.get("md5") == md5_hash:
                return file_unique_id
        
        return None

    def add_sticker(self, file_unique_id, md5, size, name = ""):
        """
        添加新的贴纸信息到JSON文件
        
        Args:
            file_unique_id (str): 贴纸的唯一ID
            md5 (str): 贴纸文件的MD5值
            size (int): 贴纸文件大小（字节）
            name (str): 贴纸名称
        
        Returns:
            bool: 添加成功返回True，失败返回False
        """
        try:
            # 确保数据结构存在
            if "stickerToEmojiMap" not in self.sticker_data:
                self.sticker_data["stickerToEmojiMap"] = {}

            sticker_map = self.sticker_data["stickerToEmojiMap"]
            
            # 检查MD5是否已存在
            exists, existing_id = self.sticker_exists_by_md5(md5)

            if exists:
                existing_data = sticker_map[existing_id]

                if existing_id != file_unique_id and existing_data.get('name') == "":
                    # 删除旧的，添加新的
                    del sticker_map[existing_id]
                    sticker_map[file_unique_id] = {
                        "md5": md5,
                        "size": size,
                        "name": name
                    }
                    return self._save_to_file()
                # 其他情况都认为已存在，不添加
                return existing_id == file_unique_id  # 相同ID返回True，不同ID返回False
            
            # MD5不存在，添加新数据
            sticker_map[file_unique_id] = {
                "md5": md5,
                "size": size,
                "name": name
            }
            
            # 保存到文件
            return self._save_to_file()
            
        except Exception as e:
            logger.error(f"添加贴纸数据失败: {str(e)}")
            return False

    def _save_to_file(self):
        """
        保存数据到JSON文件
        
        Returns:
            bool: 保存成功返回True，失败返回False
        """
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
            
            # 保存到文件
            with open(self.json_path, 'w', encoding='utf-8') as f:
                json.dump(self.sticker_data, f, ensure_ascii=False, indent=2)
            
            # 更新修改时间
            self.last_modified_time = os.path.getmtime(self.json_path)
            
            logger.info(f"贴纸数据已保存到文件: {self.json_path}")
            return True
            
        except Exception as e:
            logger.error(f"保存贴纸数据到文件失败: {str(e)}")
            return False

    def sticker_exists_by_md5(self, md5):
        """
        通过MD5检查贴纸是否已存在
        
        Args:
            md5 (str): 要检查的MD5值
        
        Returns:
            tuple: (是否存在, 存在时的file_unique_id) 或 (False, None)
        """
        # 检查是否需要重新加载数据
        self.check_and_reload()
        
        sticker_map = self.sticker_data.get("stickerToEmojiMap", {})
        
        for file_unique_id, sticker_info in sticker_map.items():
            if sticker_info.get("md5") == md5:
                return True, file_unique_id
        
        return False, None

# 创建一个全局的StickerManager实例
sticker_manager = StickerManager(os.path.join(os.path.dirname(os.path.dirname(__file__)), "database", "sticker.json"))

async def get_sticker_info(file_unique_id):
    """获取贴纸信息的便捷函数"""
    return sticker_manager.get_sticker_info(file_unique_id)

async def get_sticker_id_by_md5(md5_hash):
    """根据MD5查找贴纸unique_id的便捷函数"""
    return sticker_manager.get_sticker_id_by_md5(md5_hash)

# 如果需要手动重新加载数据
async def reload_sticker_data():
    """手动重新加载贴纸数据"""
    return sticker_manager.load_sticker_data()

async def add_sticker(file_unique_id, md5, size, name = ""):
    """添加贴纸信息的便捷函数"""
    return sticker_manager.add_sticker(file_unique_id, md5, size, name)

async def add_multiple_stickers(stickers_dict):
    """批量添加贴纸信息的便捷函数"""
    return sticker_manager.add_multiple_stickers(stickers_dict)
