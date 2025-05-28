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
from utils.msgid import msgid_mapping
from utils.sticker import get_sticker_info
import config

# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

# 处理Telegram更新中的消息
def process_telegram_update(update: Dict[str, Any]) -> None:
    # 处理消息
    if "message" in update:
        message = update["message"]
        message_id = message["message_id"]
        chat_id = str(message["chat"]["id"])
        user_id = message["from"]["id"]
        is_bot = message["from"].get("is_bot", False)
        
        # 判断是否为机器人消息
        if is_bot:
            logger.info(f"忽略来自机器人的消息")
            return
        
        # 判断消息类型并处理
        if "text" in message:        
            # 处理特殊命令
            if "/update" in message["text"]:
                to_wxid = contact_manager.get_wxid_by_chatid(chat_id)
                if not to_wxid:
                    return False
                user_info = contact.get_user_info(to_wxid)
                contact.update_info(chat_id, user_info.name, user_info.avatar_url)
                return
            
        # 转发消息
        wx_api_response = forward_message_to_wx(chat_id, message)

        # 将消息添加进映射
        if wx_api_response:
            add_send_msgid(wx_api_response, message_id)  

# 转发函数
def forward_message_to_wx(chat_id: str, message: dict) -> bool:
    to_wxid = contact_manager.get_wxid_by_chatid(chat_id)
    
    if not to_wxid:
        logger.error(f"未找到chat_id {chat_id} 对应的微信ID")
        return False
    
    try:
        # 判断消息类型并处理
        if 'text' in message and not "reply_to_message" in message:
            # 文本消息
            return _send_text_message(to_wxid, message['text'])
            
        elif 'photo' in message:
            # 发送附带文字
            _send_text_message(to_wxid, message.get("caption", ""))
            # 图片消息
            return _send_photo_message(to_wxid, message['photo'])
            
        elif 'video' in message:
            # 发送附带文字
            _send_text_message(to_wxid, message.get("caption", ""))
            # 视频消息
            return _send_video_message(to_wxid, message['video'])
        
        elif 'sticker' in message:
            # 贴纸消息
            return _send_sticker_message(to_wxid, message['sticker'])

        elif "reply_to_message" in message:
            # 回复消息
            return _send_reply_message(to_wxid, message)
            
        else:
            logger.warning(f"不支持的消息类型: {list(message.keys())}")
            return False
            
    except Exception as e:
        logger.error(f"转发消息时出错: {e}")
        return False


def _send_text_message(to_wxid: str, text: str) -> bool:
    """发送文本消息到微信"""
    payload = {
        "At": "",
        "Content": text,
        "ToWxid": to_wxid,
        "Type": 1,
        "Wxid": config.MY_WXID
    }
    return wechat_api("/Msg/SendTxt", payload)


def _send_photo_message(to_wxid: str, photo: list) -> bool:
    """发送图片消息到微信"""
    if not photo:
        logger.error("未收到照片数据")
        return False
    
    # 获取最大尺寸的照片文件ID
    file_id = photo[-1]["file_id"]  # 最后一个通常是最大尺寸
    
    try:
        image_base64 = get_file_base64(file_id)
        
        payload = {
            "Base64": image_base64,
            "ToWxid": to_wxid,
            "Wxid": config.MY_WXID
        }
        
        return wechat_api("/Msg/UploadImg", payload)
    except Exception as e:
        logger.error(f"处理图片时出错: {e}")
        return False


def _send_video_message(to_wxid: str, video: dict) -> bool:
    """发送视频消息到微信"""
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

def _send_sticker_message(to_wxid: str, sticker: dict) -> bool:
    """发送贴纸消息到微信"""
    if not sticker:
        logger.error("未收到贴纸数据")
        return False
            
    # 提取贴纸的file_unique_id
    file_unique_id = sticker.get("file_unique_id", "")
    logger.info(f"贴纸file_unique_id: {file_unique_id}")
    try:       
        sticker_info = get_sticker_info(file_unique_id)

        if sticker_info:
            md5 = sticker_info.get("md5", "")
            len = int(sticker_info.get("size", 0))
            name = sticker_info.get("name", "")
            logger.info(f"匹配到贴纸: {name}, md5: {md5}, size: {len}")
        
        payload = {
            "Md5": md5,
            "ToWxid": to_wxid,
            "TotalLen": len,
            "Wxid": config.MY_WXID
        }
        return wechat_api("/Msg/SendEmoji", payload)
    except Exception as e:
        logger.error(f"处理贴纸时出错: {e}")
        return False

def _send_reply_message(to_wxid: str, message: dict):
    """发送回复消息到微信"""
    if not "reply_to_message" in message:
        logger.error("未收到回复信息数据")
        return False
    try:
        send_text = message["text"]
        reply_to_message = message["reply_to_message"]
        reply_to_message_id = reply_to_message["message_id"]
        reply_to_wx_msgid = msgid_mapping.tg_to_wx(reply_to_message_id)
        if reply_to_wx_msgid is None:
            logger.warning(f"警告：找不到TG消息ID {reply_to_message_id} 对应的微信消息映射")
            # 处理找不到映射的情况，可能需要跳过或使用默认值
            _send_text_message(to_wxid, send_text)
        reply_to_text = reply_to_message.get("text", "")
        reply_xml = f"""<appmsg appid="" sdkver="0"><title>{send_text}</title><des /><action /><type>57</type><showtype>0</showtype><soundtype>0</soundtype><mediatagname /><messageext /><messageaction /><content /><contentattr>0</contentattr><url /><lowurl /><dataurl /><lowdataurl /><songalbumurl /><songlyric /><appattach><totallen>0</totallen><attachid /><emoticonmd5 /><fileext /><aeskey /></appattach><extinfo /><sourceusername /><sourcedisplayname /><thumburl /><md5 /><statextstr /><refermsg><content>{reply_to_text}</content><type>1</type><svrid>{int(reply_to_wx_msgid["msgid"])}</svrid><chatusr>{reply_to_wx_msgid["fromwxid"]}</chatusr><fromusr>${to_wxid}</fromusr></refermsg></appmsg>"""
        payload = {
            "ToWxid": to_wxid,
            "Type": 49,
            "Wxid": config.MY_WXID,
            "Xml": reply_xml
        }
        return wechat_api("/Msg/SendApp", payload)
    except Exception as e:
        logger.error(f"处理回复消息时出错: {e}")
        return False

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


# 添加msgid映射
def add_send_msgid(wx_api_response, tg_msgid):
    data = wx_api_response.get("Data", {})
    msg_list = data.get("List", [])
    if msg_list == []:
        # 查找第一个非空列表
        for value in data.values():
            if isinstance(value, list) and value:
                msg_list = value
    if msg_list:
        new_msg_id = (msg_list[0].get("NewMsgId") or msg_list[0].get("newMsgId"))
        if new_msg_id:
            msgid_mapping.add(
                tg_msg_id=tg_msgid,
                wx_msg_id=new_msg_id,
                from_wx_id=config.MY_WXID,
                content=""
            )
        else:
            logger.info("NewMsgId 不存在")
    else:
        logger.info("消息列表为空")
        