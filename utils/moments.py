import base64
import json
import logging
import os
from datetime import datetime
from io import BytesIO
from typing import List, Dict, Optional, Any

from telegram import InputMediaPhoto, InputMediaVideo

import config
from config import locale
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
    
    def extract_incremental_data(self, api_response: Dict[str, Any], cached_last_time: int = None) -> tuple:
        """
        增量提取朋友圈数据（优化版本）
        
        Args:
            api_response: API返回的完整响应数据
            cached_last_time: 缓存的最后时间戳，如果提供则使用此值而不读取文件
            
        Returns:
            tuple: (new_data_list, max_create_time)
        """
        if not api_response.get("Success", False):
            raise ValueError("API响应不成功")
        
        object_list = api_response.get("Data", {}).get("ObjectList", [])
        if not object_list:
            # 没有数据时也要返回元组
            last_create_time = cached_last_time if cached_last_time is not None else self.last_create_time
            return [], last_create_time
        
        # 使用缓存时间戳或从文件读取
        last_create_time = cached_last_time if cached_last_time is not None else self.last_create_time
        
        # 提取新数据
        new_data = []
        max_create_time = last_create_time
        
        for item in object_list:
            create_time = item.get("CreateTime", 0)
            
            # 只提取比当前存储的最新时间更新的数据
            if create_time > last_create_time:
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
        
        # 如果有新数据且没有使用缓存参数，更新存储的最新时间
        if new_data and cached_last_time is None:
            self._save_last_create_time(max_create_time)
            self.last_create_time = max_create_time
        
        return new_data, max_create_time
    
    def update_last_create_time(self, create_time: int):
        """更新最新的CreateTime"""
        self._save_last_create_time(create_time)
        self.last_create_time = create_time

async def process_moment_data(data):
    """
    处理朋友圈数据，增强错误处理
    """
    try:
        # 1. 统一处理输入数据格式
        if isinstance(data, list):
            if not data:
                raise ValueError("传入的数据列表为空")
            actual_data = data[0]
        elif isinstance(data, dict):
            actual_data = data
        else:
            raise TypeError(f"不支持的数据类型: {type(data)}")
        
        # 2. 安全获取基本数据
        user_wxid = actual_data.get("Username", "")
        buffer_data = actual_data.get("buffer", "")
        
        if not user_wxid or not buffer_data:
            logger.error(f"缺少必要数据: Username={user_wxid}, buffer存在={bool(buffer_data)}")
            return False
        
        # 3. 安全解析JSON
        try:
            content_json = message_formatter.xml_to_json(buffer_data)
            if not isinstance(content_json, dict):
                logger.error(f"XML解析结果不是字典: {type(content_json)}")
                return False
        except Exception as e:
            logger.error(f"XML解析失败: {e}")
            return False
        
        # 获取用户名
        contact = await contact_manager.get_contact(user_wxid)
        user_name = contact.name if contact else "未知用户"
        
        # 4. 安全获取值的函数（增强版）
        def safe_get_value(data, key, default=""):
            if not isinstance(data, dict):
                return default
            value = data.get(key, default)
            if isinstance(value, dict) and not value:
                return default
            return value
        
        def safe_get_dict(data, key, default=None):
            if default is None:
                default = {}
            if not isinstance(data, dict):
                return default
            value = data.get(key, default)
            return value if isinstance(value, dict) else default
        
        # 5. 提取基本信息
        timeline_obj = safe_get_dict(content_json, "TimelineObject")
        if not timeline_obj:
            logger.error("TimelineObject 不存在或为空")
            return False
            
        content_desc = safe_get_value(timeline_obj, "contentDesc")
        
        content_obj = safe_get_dict(timeline_obj, "ContentObject")
        content_style_str = safe_get_value(content_obj, "contentStyle", "1")
        
        # 安全转换为整数
        try:
            content_style = int(content_style_str) if content_style_str else 1
        except (ValueError, TypeError):
            content_style = 1
            logger.warning(f"无法解析 contentStyle: {content_style_str}")
        
        media_list_data = safe_get_dict(content_obj, "mediaList")
        
        # 6. 提取定位信息
        location_data = safe_get_dict(timeline_obj, "location")
        location_info = None
        
        if location_data:
            city = safe_get_value(location_data, "city")
            poi_name = safe_get_value(location_data, "poiName")
            poi_address = safe_get_value(location_data, "poiAddress")
            latitude = safe_get_value(location_data, "latitude")
            longitude = safe_get_value(location_data, "longitude")
            
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
        
        # 7. 处理媒体数据
        media_list = []
        caption_parts = []

        # 发送者信息
        if user_name:
            caption_parts.append(f"<blockquote>{user_name}</blockquote>")
        
        # 添加文本内容
        if content_desc:
            caption_parts.append(content_desc)

        # 添加定位信息
        if location_info:
            location_text = format_location_text(location_info)
            if location_text:
                caption_parts.append(f"<blockquote>{location_text}</blockquote>")
        
        # 8. 根据content_style处理不同类型的内容
        if content_style in [1, 15] and media_list_data and "media" in media_list_data:
            # 图片类型
            media_data = media_list_data["media"]
            
            # 安全处理媒体数据
            media_items = []
            if isinstance(media_data, dict):
                media_items = [media_data]
            elif isinstance(media_data, list):
                media_items = [item for item in media_data if isinstance(item, dict)]
            else:
                logger.warning(f"未知的媒体数据类型: {type(media_data)}")
            
            # 处理每张图片
            for i, media_item in enumerate(media_items):
                if not isinstance(media_item, dict):
                    continue
                    
                item_type = safe_get_value(media_item, "type")
                if item_type == "2":  # type=2表示图片
                    # 安全获取图片URL
                    img_url = None
                    for url_key in ["uhd", "hd", "url", "thumb"]:
                        url_obj = safe_get_dict(media_item, url_key)
                        if url_obj:
                            img_url = safe_get_value(url_obj, "_text")
                            if img_url:
                                break
                    
                    if img_url:
                        try:
                            bytes_io_data, _ = await tools.get_file_from_url(img_url)
                            caption = "\n".join(caption_parts) if i == 0 and caption_parts else ""
                            input_media = InputMediaPhoto(media=bytes_io_data, caption=caption)
                            media_list.append(input_media)
                        except Exception as e:
                            logger.error(f"处理图片失败: {img_url}, 错误: {e}")
                            continue
                elif item_type == "6":  # type=6表示微信小视频
                    video_url = None
                    url_obj = safe_get_dict(media_item, "url")
                    if url_obj:
                        video_url = safe_get_value(url_obj, "_text")
                    
                    if video_url:
                        try:
                            url_base64 = base64.b64encode(video_url.encode()).decode('utf-8')

                            payload = {
                                "Url": url_base64,
                                "Key": "0",
                                "Wxid": config.MY_WXID
                            }
                            video_data = await wechat_api("GET_MOMENT_VIDEO", payload)
                            
                            if not video_data.get("Success", True):
                                return
                            
                            video_base64 = video_data.get("Message", "")
                            # 解码为bytes
                            video_bytes = base64.b64decode(video_base64)

                            # 转换为BytesIO
                            video_io = BytesIO(video_bytes)
                            caption = "\n".join(caption_parts) if i == 0 and caption_parts else ""
                            input_media = InputMediaVideo(media=video_io, caption=caption)
                            media_list.append(input_media)
                        except Exception as e:
                            caption_parts.append(f"<blockquote>[{locale.type(43)}: {finder_nickname}]</blockquote>")
                            logger.error(f"处理小视频失败: {video_url}, 错误: {e}")
                            continue
            
        else:
            # 其他分享内容类型
            share_title = safe_get_value(content_obj, "title")
            share_url = safe_get_value(content_obj, "contentUrl")
            
            app_info = safe_get_dict(timeline_obj, "appInfo")
            share_name = (safe_get_value(timeline_obj, "sourceNickName") or 
                         safe_get_value(app_info, "appName"))
            
            if share_title and "当前微信版本不支持展示该内容" not in share_title:
                if share_url:
                    caption_parts.append(f'<a href="{share_url}">{share_title}</a>')
                if share_name:
                    caption_parts.append(f'<blockquote>{share_name}</blockquote>')
            else:
                logger.warning("不支持的分享内容")
                logger.debug(content_json)

            # 转发视频号信息
            finder_feed = safe_get_dict(content_obj, "finderFeed")
            if finder_feed:
                finder_nickname = safe_get_value(finder_feed, "nickname")
                finder_desc = safe_get_value(finder_feed, "desc")
                if finder_nickname:
                    caption_parts.append(f"<blockquote>[{locale.type(51)}: {finder_nickname}]</blockquote>")
                if finder_desc:
                    caption_parts.append(finder_desc)
        
        # 9. 统一合并caption
        full_caption = "\n".join(caption_parts) if caption_parts else ""

        # 10. 发送消息
        chat_id = await _get_or_create_chat("wechat_moments", locale.common("moments"), "")
        if not chat_id:
            return False
            
        if media_list:
            await telegram_sender.send_media_group(chat_id, media_list)
        elif full_caption:
            await telegram_sender.send_text(chat_id, full_caption)
        else:
            await telegram_sender.send_text(chat_id, f'<blockquote>{locale.common("moments")}</blockquote>')

        return True
        
    except Exception as e:
        logger.error(f"处理朋友圈数据时发生错误: {e}")
        logger.error(f"错误数据: {data}")
        return False

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
