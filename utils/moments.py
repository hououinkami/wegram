import json
import logging
import os
from datetime import datetime
from io import BytesIO
from typing import List, Dict, Optional, Any

from telegram import InputMediaPhoto

import config
from config import LOCALE as locale
from utils import message_formatter
from utils import tools
from api.wechat_api import wechat_api
from api.telegram_sender import telegram_sender
from utils.contact_manager import contact_manager
from utils.wechat_to_telegram import _get_or_create_chat

logger = logging.getLogger(__name__)

class WeChatMomentsExtractor:
    """微信朋友圈增量提取器"""
    
    def __init__(self, storage_file: str = None):
        """
        初始化提取器
        
        Args:
            storage_file: 存储最新CreateTime的文件路径
        """
        if storage_file is None:
            # 默认数据库路径
            self.storage_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "database", 
                "moments.txt"
            )
        else:
            self.storage_file = storage_file
        self.last_create_time = self._load_last_create_time()
    
    def _load_last_create_time(self) -> int:
        """从文件加载最新的CreateTime"""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    return int(f.read().strip())
            except (ValueError, FileNotFoundError):
                return 0
        return 0
    
    def _save_last_create_time(self, create_time: int):
        """保存最新的CreateTime到文件"""
        with open(self.storage_file, 'w') as f:
            f.write(str(create_time))
    
    def _timestamp_to_datetime(self, timestamp: int) -> str:
        """将时间戳转换为指定格式的日期时间字符串"""
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M")
    
    def get_last_create_time(self) -> int:
        """获取当前存储的最新CreateTime"""
        return self.last_create_time
    
    def reset_last_create_time(self):
        """重置最新CreateTime（将提取所有数据）"""
        self.last_create_time = 0
        if os.path.exists(self.storage_file):
            os.remove(self.storage_file)
    
    def get_last_create_time_formatted(self) -> str:
        """获取格式化的最新CreateTime"""
        if self.last_create_time == 0:
            return "无记录"
        return self._timestamp_to_datetime(self.last_create_time)
    
    def extract_incremental_data(self, api_response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        增量提取朋友圈数据
        
        Args:
            api_response: API返回的完整响应数据
            
        Returns:
            提取的新朋友圈数据列表
        """
        if not api_response.get("Success", False):
            raise ValueError("API响应不成功")
        
        object_list = api_response.get("Data", {}).get("ObjectList", [])
        if not object_list:
            return []
        
        # 提取新数据
        new_data = []
        max_create_time = self.last_create_time
        
        for item in object_list:
            create_time = item.get("CreateTime", 0)
            
            # 只提取比当前存储的最新时间更新的数据
            if create_time > self.last_create_time:
                extracted_item = {
                    "Id": item.get("Id"),
                    "Username": item.get("Username"),
                    "CreateTime": self._timestamp_to_datetime(create_time),
                    "CreateTimeTimestamp": create_time,  # 保留原始时间戳用于比较
                    "buffer": item.get("ObjectDesc", {}).get("buffer", ""),
                    "LikeFlag": item.get("LikeFlag", 0),
                    "LikeCount": item.get("LikeCount", 0)
                }
                new_data.append(extracted_item)
                
                # 更新最大时间戳
                if create_time > max_create_time:
                    max_create_time = create_time
        
        # 如果有新数据，更新存储的最新时间
        if new_data:
            self._save_last_create_time(max_create_time)
            self.last_create_time = max_create_time
        
        return new_data

async def process_moment_data(data: list):
    user_wxid = data["Username"]
    content_json = message_formatter.xml_to_json(data["buffer"])
    
    # 获取用户名
    contact = await contact_manager.get_contact(user_wxid)
    user_name = contact.name
    
    # 提取基本信息
    timeline_obj = content_json.get("TimelineObject", {})
    content_desc = timeline_obj.get("contentDesc", "")
    
    # 处理contentDesc为空字典的情况
    if isinstance(content_desc, dict) and not content_desc:
        content_desc = ""
    
    content_obj = timeline_obj.get("ContentObject", {})
    content_style = content_obj.get("contentStyle", 1)
    media_list_data = content_obj.get("mediaList", {})
    
    # 提取定位信息
    location_data = timeline_obj.get("location", {})
    location_info = None
    
    if location_data and not (isinstance(location_data, dict) and not location_data):
        # 安全获取值的函数
        def safe_get_value(data, key):
            value = data.get(key, "")
            if isinstance(value, dict) and not value:
                return ""
            return value
        
        city = safe_get_value(location_data, "city")
        poi_name = safe_get_value(location_data, "poiName")
        poi_address = safe_get_value(location_data, "poiAddress")
        latitude = safe_get_value(location_data, "latitude")
        longitude = safe_get_value(location_data, "longitude")
        
        # 检查是否有有效的定位信息
        if any([city, poi_name, poi_address, latitude, longitude]):
            location_info = {
                "city": city,
                "poi_name": poi_name,
                "poi_address": poi_address,
                "latitude": latitude,
                "longitude": longitude,
                "country": safe_get_value(location_data, "country"),
                "poi_address_name": safe_get_value(location_data, "poiAddressName"),
                "poi_classify_id": safe_get_value(location_data, "poiClassifyId"),
                "poi_classify_type": safe_get_value(location_data, "poiClassifyType")
            }
    
    # 处理媒体数据
    media_list = []
    # 先准备caption内容
    caption_parts = []

    # 发送者信息
    if user_name:
        sender_name = f"<blockquote>{user_name}</blockquote>"
        caption_parts.append(sender_name)
    
    # 添加文本内容
    if content_desc:
        caption_parts.append(content_desc)

    # 添加定位信息
    if location_info:
        location_text = format_location_text(location_info)
        if location_text:
            caption_parts.append(f"<blockquote>{location_text}</blockquote>")
    
    # 检查媒体数据结构
    if int(content_style) == 1 and "media" in media_list_data:
        media_data = media_list_data["media"]
        
        # 如果是单张图片，media是字典
        if isinstance(media_data, dict):
            media_items = [media_data]
        # 如果是多张图片，media是列表
        elif isinstance(media_data, list):
            media_items = media_data
        else:
            media_items = []
        
        # 合并caption
        full_caption = "\n".join(caption_parts) if caption_parts else ""
        
        # 处理每张图片
        for i, media_item in enumerate(media_items):
            if media_item.get("type") == "2":  # type=2表示图片
                # 获取最高分辨率的图片链接 - 合并到主函数中
                img_url = (
                    media_item.get("uhd", {}).get("_text") or
                    media_item.get("hd", {}).get("_text") or
                    media_item.get("url", {}).get("_text") or
                    media_item.get("thumb", {}).get("_text")
                )
                
                if img_url:
                    try:
                        # 使用tools.get_image_from_url转换为BytesIO数据
                        bytes_io_data = await tools.get_image_from_url(img_url)
                        
                        # 只有第一张图片设置caption
                        caption = full_caption if i == 0 else ""

                        # 创建InputMediaPhoto对象
                        input_media = InputMediaPhoto(media=bytes_io_data, caption=caption)
                        media_list.append(input_media)
                        
                    except Exception as e:
                        print(f"处理图片失败: {img_url}, 错误: {e}")
                        continue
    elif int(content_style) == 15:
        logger.warning(content_json)
        caption_parts.append(f'<blockquote>WeChatビデオ</blockquote>')
    else:
        # 转发公众号或App消息
        share_title = content_obj.get("title", "")
        share_url = content_obj.get("contentUrl", "")
        share_name = content_obj.get("sourceNickName") or timeline_obj.get("appInfo", {}).get("appName") or ""
        
        if not "当前微信版本不支持展示该内容" in share_title:
            caption_parts.append(f'<a href="{share_url}">{share_title}</a>\n<blockquote>{share_name}</blockquote>')
        else:
            logger.warning(content_json)

        # 转发视频号信息
        finder_feed = content_obj.get("finderFeed", {})
        if finder_feed:
            finder_nickname = finder_feed.get("nickname", "")
            finder_desc = finder_feed.get("desc", "")
            if finder_nickname:
                caption_parts.append(f"<blockquote>[{locale.type(51)}: {finder_nickname}]</blockquote>\n{finder_desc}")
        
    # 合并caption
    full_caption = "\n".join(caption_parts) if caption_parts else ""

    # 构建返回集合
    moments_content =  {
        "user_name": user_name,
        "content_desc": content_desc,
        "media_list": media_list,
        "location_info": location_info,
        "timeline_id": timeline_obj.get("id", ""),
        "username": timeline_obj.get("username", ""),
        "create_time": timeline_obj.get("createTime", "")
    }

    # 发送
    chat_id = await _get_or_create_chat("wechat_moments", "モーメンツ", "")
    if not chat_id:
        return
    if media_list:
        await telegram_sender.send_media_group(chat_id, media_list)
    else:
        await telegram_sender.send_text(chat_id, full_caption)

    return True

def format_location_text(location_info):
    """
    格式化定位信息为可读文本（辅助函数）
    """
    if not location_info:
        return ""
    
    location_parts = []
    location_shown = []
    
    # 添加城市信息
    if location_info.get("city"):
        location_parts.append(f"📍 {location_info['city']}")
    
    # 添加具体位置
    if location_info.get("poi_name"):
        location_parts.append(f"🏢 {location_info['poi_name']}")
        location_shown.append(f"📍 {location_info['poi_name']}")

    elif location_info.get("poi_address_name"):
        location_parts.append(f"🏢 {location_info['poi_address_name']}")
    
    # 添加地址
    if location_info.get("poi_address"):
        location_parts.append(f"📮 {location_info['poi_address']}")
        location_shown.append(f"🏢 {location_info['poi_address']}")
    
    # 添加坐标（如果需要）
    if location_info.get("latitude") and location_info.get("longitude"):
        location_parts.append(f"🗺️ {location_info['latitude']}, {location_info['longitude']}")
    
    return "\n".join(location_shown) 
