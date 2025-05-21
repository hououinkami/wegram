import json
import os
import time
import logging

# 配置日志
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

# 创建一个全局的StickerManager实例
sticker_manager = StickerManager(os.path.join(os.path.dirname(os.path.dirname(__file__)), "sticker.json"))

def get_sticker_info(file_unique_id):
    """获取贴纸信息的便捷函数"""
    return sticker_manager.get_sticker_info(file_unique_id)

# 如果需要手动重新加载数据
def reload_sticker_data():
    """手动重新加载贴纸数据"""
    return sticker_manager.load_sticker_data()
