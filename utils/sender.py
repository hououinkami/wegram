#!/usr/bin/env python3
"""
消息转发模块 - 处理从Telegram到微信的消息转发
"""

import requests
import logging
import base64
from typing import Dict, Any, Optional, Union
from utils.contact import contact_manager
from api import contact
from api.base import wechat_api, telegram_api
from utils.sticker import get_sticker_info
import config

# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

# 从Telegram转发文本消息到微信API
def forward_text_to_wx(chat_id: str, message_text: str) -> bool:
    to_wxid = contact_manager.get_wxid_by_chatid(chat_id)
    
    if not to_wxid:
        return False
        
    # 准备API请求数据
    payload = {
        "At": "",
        "Content": message_text,
        "ToWxid": to_wxid,
        "Type": 1,
        "Wxid": config.MY_WXID
    }
    
    return wechat_api("/Msg/SendTxt", payload)

# 从Telegram获取图片并转发到微信API
def forward_photo_to_wx(chat_id: str, photo: list) -> bool:
    to_wxid = contact_manager.get_wxid_by_chatid(chat_id)
    
    if not to_wxid:
        return False
        
    # 从照片列表中获取最大尺寸的照片
    if not photo:
        logger.error("未收到照片数据")
        return False
        
    # 获取最大尺寸的照片文件ID
    file_id = photo[-1]["file_id"]  # 最后一个通常是最大尺寸
    
    try:
        image_base64 = get_file_base64(file_id)
        
        # 准备API请求数据
        payload = {
            "Base64": image_base64,
            "ToWxid": to_wxid,
            "Wxid": config.MY_WXID
        }
        
        return wechat_api("/Msg/UploadImg", payload)
    except Exception as e:
        logger.error(f"处理图片时出错: {e}")
        return False

# 从Telegram获取视频并转发到微信API
def forward_video_to_wx(chat_id: str, video) -> bool:
    to_wxid = contact_manager.get_wxid_by_chatid(chat_id)
    
    if not to_wxid:
        return False
        
    # 获取视频
    if not video:
        logger.error("未收到视频数据")
        return False
        
    # 获取视频与缩略图文件ID
    file_id = video["file_id"]
    thumb_file_id = video["thumb"]["file_id"]
    duration = video["duration"]
    
    
    try:
        video_base64 = get_file_base64(file_id)
        thumb_base64 = get_file_base64(thumb_file_id)
        
        # 准备API请求数据
        payload = {
            "Base64": video_base64,
            "ImageBase64": thumb_base64,
            "PlayLength": int(duration),
            "ToWxid": to_wxid,
            "Wxid": config.MY_WXID
        }        
        return wechat_api("/Msg/SendVideo", payload)
    except Exception as e:
        logger.error(f"处理视频时出错: {e}")
        return False

# 从Telegram转发贴纸消息到微信API
def forward_sticker_to_wx(chat_id: str, md5: str, len: int) -> bool:
    to_wxid = contact_manager.get_wxid_by_chatid(chat_id)
    
    if not to_wxid:
        return False
        
    # 准备API请求数据
    payload = {
            "Md5": md5,
            "ToWxid": to_wxid,
            "TotalLen": len,
            "Wxid": config.MY_WXID
        }
    
    return wechat_api("/Msg/SendEmoji", payload)

# 处理Telegram更新中的消息
def process_telegram_update(update: Dict[str, Any]) -> None:
    # 处理消息
    if "message" in update:
        message = update["message"]
        chat_id = str(message["chat"]["id"])
        user_id = message["from"]["id"]
        is_bot = message["from"].get("is_bot", False)
        
        if is_bot:
            logger.info(f"忽略来自机器人的消息")
            return
        
        # 判断消息类型并处理
        if "text" in message:
            message_text = message["text"]
            
            # 处理特殊命令
            if "/update" in message_text:
                to_wxid = contact_manager.get_wxid_by_chatid(chat_id)
    
                if not to_wxid:
                    return False
                payload = {
                    "Toxids": to_wxid,
                    "Wxid": config.MY_WXID,
                    "ChatRoom": ""
                }
                contact_info = wechat_api("/Friend/GetContractDetail", payload)
                user_info = contact.get_user_info(to_wxid)
                contact.update_info(chat_id, user_info.name, user_info.avatar_url)
                return
            logger.info(f"收到来自用户 {user_id} 在群组 {chat_id} 的文本消息: {message_text}")
            forward_text_to_wx(chat_id, message_text)
            
        elif "photo" in message:
            logger.info(f"收到来自用户 {user_id} 在群组 {chat_id} 的图片消息")
            photo = message["photo"]
            # 如果有图片说明，也一并转发
            caption = message.get("caption", "")
            
            # 先转发图片
            success = forward_photo_to_wx(chat_id, photo)
            
            # 如果有说明文字，也转发文字
            if success and caption:
                forward_text_to_wx(chat_id, caption)

        elif "video" in message:
            logger.info(f"收到来自用户 {user_id} 在群组 {chat_id} 的视频消息")
            video = message["video"]
            # 如果有视频说明，也一并转发
            caption = message.get("caption", "")
            
            # 先转发视频
            success = forward_video_to_wx(chat_id, video)
            
            # 如果有说明文字，也转发文字
            if success and caption:
                forward_text_to_wx(chat_id, caption)

        elif "sticker" in message:
            logger.info(f"收到来自用户 {user_id} 在群组 {chat_id} 的贴纸消息")
            sticker = message["sticker"]
            
            # 提取贴纸的file_unique_id
            file_unique_id = sticker.get("file_unique_id", "")
            logger.info(f"贴纸file_unique_id: {file_unique_id}")         
            
            sticker_info = get_sticker_info(file_unique_id)
            if sticker_info:
                md5 = sticker_info.get("md5", "")
                size = int(sticker_info.get("size", 0))
                name = sticker_info.get("name", "")
                logger.info(f"匹配到贴纸: {name}, md5: {md5}, size: {size}")
                
                forward_sticker_to_wx(chat_id, md5, size)
                
            else:
                logger.info(f"未找到匹配的贴纸信息: {file_unique_id}")
                
        # 可以在这里添加其他类型消息的处理逻辑，例如：
        # elif "document" in message:
        #     # 处理文档消息
        #     pass
        else:
            # 不支持的消息类型
            logger.info(f"收到不支持的消息类型，来自用户 {user_id} 在群组 {chat_id}")

# 获取文件的 Base64 编码
def get_file_base64(file_id):
    # Step 1: 获取文件路径
    file_path_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/getFile?file_id={file_id}"
    file_path_response = requests.get(file_path_url)
    file_path_data = file_path_response.json()
    
    if not file_path_data['ok']:
        logger.error(f"获取文件路径失败: {file_path_data}")
    
    file_path = file_path_data['result']['file_path']
    
    # Step 2: 下载文件
    file_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
    file_response = requests.get(file_url)
    
    # Step 3: 转换为 Base64
    if file_response.status_code != 200:
        logger.error(f"下载文件失败，状态码: {file_response.status_code}")
        return False
            
    # 将图片转换为Base64
    file_base64 = base64.b64encode(file_response.content).decode('utf-8')
    return file_base64
