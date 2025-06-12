#!/usr/bin/env python3
"""
消息转发模块 - 处理从Telegram到微信的消息转发
"""

import logging
logger = logging.getLogger(__name__)

import requests
import base64
from typing import Dict, Any, Optional
from api import contact
from api.base import wechat_api, telegram_api
from utils.contact import contact_manager
from utils.msgid import msgid_mapping
from utils.sticker import get_sticker_info
from utils.locales import Locale
import config

locale = Locale(config.LANG)

# ==================== Telegram相关方法 ====================
# 处理Telegram更新中的消息
async def process_telegram_update(update: Dict[str, Any]) -> None:
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
            # 更新联系人信息
            if "/update" in message["text"]:
                to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
                if not to_wxid:
                    return False
                user_info = contact.get_user_info(to_wxid)
                contact.update_info(chat_id, user_info.name, user_info.avatar_url)
                return
            
            # 删除联系人数据
            if "/unbind" in message["text"]:
                to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
                await contact_manager.delete_contact(to_wxid)
                return
            
            # 撤回
            if "/rm" in message["text"] or "/revoke" in message["text"]:
                if "reply_to_message" in message:
                    _revoke_telegram(chat_id, message)
                    return

        # 转发消息
        wx_api_response = await forward_telegram_to_wx(chat_id, message)
        
        # 将消息添加进映射
        if wx_api_response:
            add_send_msgid(wx_api_response, message_id)  

# 转发函数
async def forward_telegram_to_wx(chat_id: str, message: dict) -> bool:
    to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
    
    if not to_wxid:
        logger.error(f"未找到chat_id {chat_id} 对应的微信ID")
        return False
    
    try:
        # 判断消息类型并处理
        if 'text' in message:      
            text = message.get('text', '')

            # 判断是否为单纯文本信息
            msg_entities = message.get('entities', [])
            is_url = False
            if msg_entities and len(msg_entities) > 0:
                entity = msg_entities[0]
                # 查找第一个链接实体
                for item in msg_entities:
                    if item.get('type') in ['text_link', 'url']:
                        entity = item
                        is_url = True
                        break
    
            if "reply_to_message" in message:
                # 回复消息
                return _send_telegram_reply(to_wxid, message)
            elif "entities" in message and is_url:
                # 链接消息
                return _send_telegram_link(to_wxid, message)
            elif "entities" in message and entity.get('type') == "expandable_blockquote":
                # 转发群聊消息时去除联系人
                text = text.split('\n', 1)[1] 
                return _send_telegram_text(to_wxid, text)
            else:
                # 纯文本消息
                return _send_telegram_text(to_wxid, text)
            
        elif 'photo' in message:
            # 发送附带文字
            if message.get("caption"):
                _send_telegram_text(to_wxid, message.get("caption"))
            # 图片消息
            return _send_telegram_photo(to_wxid, message['photo'])
            
        elif 'video' in message:
            # 发送附带文字
            if message.get("caption"):
                _send_telegram_text(to_wxid, message.get("caption"))
            # 视频消息
            return _send_telegram_video(to_wxid, message['video'])
        
        elif 'sticker' in message:
            # 贴纸消息
            return _send_telegram_sticker(to_wxid, message['sticker'])

        else:
            return False
            
    except Exception as e:
        logger.error(f"转发消息时出错: {e}")
        return False


def _send_telegram_text(to_wxid: str, text: str) -> bool:
    """发送文本消息到微信"""
    payload = {
        "At": "",
        "Content": text,
        "ToWxid": to_wxid,
        "Type": 1,
        "Wxid": config.MY_WXID
    }
    return wechat_api("/Msg/SendTxt", payload)


def _send_telegram_photo(to_wxid: str, photo: list) -> bool:
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


def _send_telegram_video(to_wxid: str, video: dict) -> bool:
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

def _send_telegram_sticker(to_wxid: str, sticker: dict) -> bool:
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

def _send_telegram_reply(to_wxid: str, message: dict):
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
            logger.warning(f"找不到TG消息ID {reply_to_message_id} 对应的微信消息映射")
            # 处理找不到映射的情况，可能需要跳过或使用默认值
            _send_telegram_text(to_wxid, send_text)
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

def _send_telegram_link(to_wxid: str, message: dict):
    """处理链接信息"""    
    text = message.get('text', '')

    msg_entities = message.get('entities', [])
    if msg_entities and len(msg_entities) > 0:
        entity = msg_entities[0]
        # 查找第一个链接实体
        for item in msg_entities:
            if item.get('type') in ['text_link', 'url']:
                entity = item
                break

        if entity.get('type') == 'text_link' and entity.get('url'):
            link_title = message.get('text', '')
            link_url = entity.get('url')
            link_desc = ''
        elif entity.get('type') == 'url':
            link_title = '非公众号链接'
            offset = entity.get('offset', 0)
            length = entity.get('length', 0)
            link_url = message.get('text', '')[offset:offset + length]
            link_desc = link_url
        
        if link_title and link_url:
            text = f"<appmsg><title>{link_title}</title><des>{link_desc}</des><type>5</type><url>{link_url}</url><thumburl></thumburl></appmsg>"

        playload = {
            "ToWxid": to_wxid,
            "Type": 49,
            "Wxid": config.MY_WXID,
            "Xml": text
        }
        return wechat_api('/Msg/SendApp', playload)

def _revoke_telegram(chat_id, message: dict):

    try:
        delete_message = message["reply_to_message"]
        delete_message_id = delete_message["message_id"]
        delete_wx_msgid = msgid_mapping.tg_to_wx(delete_message_id)

        if not delete_wx_msgid:
            return telegram_api(chat_id, locale.common('revoke'))
        
        to_wxid = delete_wx_msgid["towxid"]
        new_msg_id = delete_wx_msgid["msgid"]
        client_msg_id = delete_wx_msgid["clientmsgid"]
        create_time = delete_wx_msgid["createtime"]
        
        playload = {
            "ClientMsgId": client_msg_id,
            "CreateTime": create_time,
            "NewMsgId": new_msg_id,
            "ToUserName": to_wxid,
            "Wxid": config.MY_WXID
        }
        wechat_api("/Msg/Revoke", playload)
        
    except Exception as e:
        logger.error(f"处理消息删除逻辑时出错: {e}")

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
        response_data = msg_list[0]
    else:
        response_data = data

    if response_data:
        to_wx_id = multi_get(response_data, 'ToUsetName.string', 'toUserName')
        new_msg_id = multi_get(response_data, 'NewMsgId', 'Newmsgid', 'newMsgId')
        client_msg_id = multi_get(response_data, 'ClientMsgid', 'ClientImgId.string', 'clientmsgid', 'clientMsgId')
        create_time = multi_get(response_data, 'Createtime', 'createtime', 'createTime')
        if new_msg_id:
            msgid_mapping.add(
                tg_msg_id=tg_msgid,
                from_wx_id=config.MY_WXID,
                to_wx_id=to_wx_id,
                wx_msg_id=new_msg_id,
                client_msg_id=client_msg_id,
                create_time=create_time,
                content=""
            )
        else:
            logger.info("NewMsgId 不存在")
    else:
        logger.info("消息列表为空")

def multi_get(data, *keys, default=''):
    """从多个键中获取第一个有效值"""
    for key in keys:
        if '.' in key:
            # 处理嵌套键如 'ToUserName.string'
            parts = key.split('.')
            value = data
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part, {})
                else:
                    value = {}
                    break
            if value and value != {}:
                return value
        else:
            value = data.get(key)
            if value:
                return value
    return default

# ==================== Telethon相关方法 ====================
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
import hashlib
import asyncio
import tempfile
import os
# 处理Telegram更新中的消息
async def process_telethon_update(message, chat=None, client=None) -> None:
    """
    处理telethon消息对象 (添加client参数)
    
    Args:
        message: telethon消息对象
        chat: telethon聊天对象
        client: telethon客户端实例
    """
    try:
        # 适配telethon消息对象
        message_id = message.id
        chat_id = str(-chat.id if chat else -message.chat_id)
        
        # 检查是否为机器人消息
        if message.sender and hasattr(message.sender, 'bot') and message.sender.bot:
            logger.info(f"忽略来自机器人的消息")
            return
        
        # 处理特殊命令
        if message.text and "/update" in message.text:
            to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
            if not to_wxid:
                return False
            user_info = contact.get_user_info(to_wxid)
            contact.update_info(chat_id, user_info.name, user_info.avatar_url)
            return
        
        # 转发消息 (传递client参数)
        wx_api_response = await forward_telethon_to_wx(chat_id, message, client)

        # 将消息添加进映射
        if wx_api_response:
            add_send_msgid(wx_api_response, message_id)
            
    except Exception as e:
        logger.error(f"处理telegram消息时出错: {e}")

# 转发函数 (修改为telethon格式)
async def forward_telethon_to_wx(chat_id: str, message, client) -> bool:
    """
    转发消息到微信 (添加client参数)
    
    Args:
        chat_id: 聊天ID
        message: telethon消息对象
        client: telethon客户端实例
    """
    to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
    
    if not to_wxid:
        logger.error(f"未找到chat_id {chat_id} 对应的微信ID")
        return False
    
    try:
        # 判断消息类型并处理 (telethon格式)
        if message.text and not message.reply_to:
            # 纯文本消息
            return _send_telethon_text(to_wxid, message.text)
            
        elif message.media:
            # 媒体消息
            media = message.media
            
            if isinstance(media, MessageMediaPhoto):
                # 图片消息
                if message.text:  # caption
                    _send_telethon_text(to_wxid, message.text)
                return await _send_telethon_photo(to_wxid, message, client)
                
            elif isinstance(media, MessageMediaDocument):
                # 文档消息（包括视频、贴纸等）
                if message.text:  # caption
                    _send_telethon_text(to_wxid, message.text)
                
                # 检查文档类型
                if (hasattr(media.document, 'mime_type')):
                    mime_type = media.document.mime_type
                    if mime_type.startswith('video/'):
                        return await _send_telethon_video(to_wxid, message, client)
                    elif 'sticker' in mime_type:
                        return await _send_telethon_sticker(to_wxid, message, client)
                    else:
                        logger.warning(f"不支持的文档类型: {mime_type}")
                        return False
                else:
                    logger.warning("文档没有mime_type信息")
                    return False
                
        elif message.reply_to:
            # 回复消息
            return await _send_telethon_reply(to_wxid, message, client)
            
        else:
            logger.warning(f"不支持的消息类型")
            return False
            
    except Exception as e:
        logger.error(f"转发消息时出错: {e}")
        return False

def _send_telethon_text(to_wxid: str, text: str) -> bool:
    """发送文本消息到微信"""
    if not text or not text.strip():
        return True  # 空文本不发送，但返回成功
        
    payload = {
        "At": "",
        "Content": text,
        "ToWxid": to_wxid,
        "Type": 1,
        "Wxid": config.MY_WXID
    }
    return wechat_api("/Msg/SendTxt", payload)

async def _send_telethon_photo(to_wxid: str, photo, client) -> bool:
    """发送图片消息到微信 (添加client参数)"""
    try:
        if not client:
            logger.error("客户端实例为空")
            return False
            
        # telethon格式：photo是message对象
        if not isinstance(photo.media, MessageMediaPhoto):
            logger.error("消息不包含图片")
            return False
        
        # 下载图片到内存
        photo_downloaded = await client.download_media(photo, file=bytes)
        
        if not photo_downloaded:
            logger.error("下载图片失败")
            return False
        
        # 转换为Base64
        image_base64 = base64.b64encode(photo_downloaded).decode('utf-8')
        
        payload = {
            "Base64": image_base64,
            "ToWxid": to_wxid,
            "Wxid": config.MY_WXID
        }
        
        return wechat_api("/Msg/UploadImg", payload)
        
    except Exception as e:
        logger.error(f"处理图片时出错: {e}")
        return False

async def _send_telethon_video(to_wxid: str, video, client) -> bool:
    """发送视频消息到微信"""    
    temp_path = None
    try:
        if not client:
            logger.error("客户端实例为空")
            return False
            
        if not isinstance(video.media, MessageMediaDocument):
            logger.error("消息不包含视频")
            return False
        
        # 检查文件大小
        document = video.media.document
        file_size = getattr(document, 'size', 0)
        
        # 设置大小限制 (100MB)
        # max_size = 100 * 1024 * 1024
        # if file_size > max_size:
        #     logger.warning(f"视频文件过大 ({file_size/1024/1024:.1f}MB > 100MB)，跳过发送")
        #     return False
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
            temp_path = temp_file.name
        
        # 下载到临时文件
        downloaded_path = await client.download_media(video, file=temp_path)
        if not downloaded_path or not os.path.exists(downloaded_path):
            logger.error("下载视频失败")
            return False
        
        # 读取文件
        with open(downloaded_path, 'rb') as f:
            video_bytes = f.read()
        
        # 转换为Base64
        video_base64 = base64.b64encode(video_bytes).decode('utf-8')
        
        # 获取视频时长
        duration = 0
        if hasattr(video.media, 'document') and hasattr(video.media.document, 'attributes'):
            for attr in video.media.document.attributes:
                if hasattr(attr, 'duration'):
                    duration = attr.duration
                    break
        
        # 黑色缩略图
        thumb_base64 = "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAFAALQDASIAAhEBAxEB/8QAGAABAQEBAQAAAAAAAAAAAAAAAAECAwj/xAAgEAEBAQADAAICAwAAAAAAAAAAAREhMUECUSJhcYGR/8QAFgEBAQEAAAAAAAAAAAAAAAAAAAEC/8QAGBEBAQEBAQAAAAAAAAAAAAAAABEBMSH/2gAMAwEAAhEDEQA/APG9E1RrcAAqXmqkqiQAEgEAhSAESrAoCVUwazSFWJQ31AaEjItQMBoGqAtGcQCikAgFFqAAXoAAAATQE0MUJcPlZQpQBWVhQFEAUFzjRMQFgqBSAAAFnAAFCgbxhRL2C1MJeVEiZypKBACgmlKUKS4GgVQAwJD4rgqKiwEvYvqYBYQvKzoEFqQAvJQAKABSCaQEoiA0CaIA0LMxKAT+AotGmavgqXsL2AAQAvZYACxAA8KCWmkUTRloEToq1KCAA2UhQQoAAvxSiUXSq0hIAFoHXAJVX/UvIytRdQDABSpCoItRpKCAA3O0rXH2zQAAGpfWRnQ+V5KUpQBc40q1PQClJQFpSgKgBaCVGigRMWpoILoDVKGgheydroJFqeWjOh6XfoWVBDzCNAyAALc8QAPDzVoFIXtrBDtcwBk5+mqlBRkBqi+IAAABE3gBBkIuliUACAAQguftCgAUMC9JixK2GFVKCAA6eJU+gA9IAAsTeCL2gyLv5U1AACgALQBIbtFDZ9CAlUbEqNVkF0QBrwOyABD0CTKUATDFE0DQrMD0vB6AJVKAVPNWgkMUBLMVKijVSlRoAAaKGaAHoAAALn0nVA/sACgJuALUvCQLpC3E7qCzASgVaAJhSo2AAN1FjMGtISEWjIAAUhAABYFv6SkEW9ltrLUTRKi0qCeCxQTxFhKCxlq3GWgvNABqXAzkFoCUFGWgSKFCAkWAl7IvoAQAgSFTP2EUqQxIhTFgsGTwPAAAa0tCgkWJFFTpfEqilSF5qgkX0ATVElwFTtUwDTVjIALREWHaAB4CAANJVqUXCFWFBIasBU1Uw6BWV0gKy1GQWKmFAixkAaSFAqCzsEFqCAARe1vRChjLVSnYqpEWAoVNBUqALUFssAwpDQRYICxFhQQACgAAA0y1UomI0mKKkMWsgAsBFhFgMi+lBFpQC1ABYUiAAAFEAFwBYqYUZWJSoKAsFQWoA0yugXEXEAAAAAAAAADAAAGqlJcWiZjIAosSL0CAsBBe1wEMqLQQKmLooCAACxAASqEAAH//2Q=="
        
        # 构建发送数据
        payload = {
            "Base64": video_base64,
            "ImageBase64": thumb_base64,
            "PlayLength": int(duration),
            "ToWxid": to_wxid,
            "Wxid": config.MY_WXID
        }
        
        # 发送到微信
        return wechat_api("/Msg/SendVideo", payload)
        
    except Exception as e:
        logger.error(f"处理视频失败: {e}")
        return False
    finally:
        # 清理临时文件
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

async def _send_telethon_sticker(to_wxid: str, sticker, client) -> bool:
    """发送贴纸消息到微信 (添加client参数)"""
    try:
        if not client:
            logger.error("客户端实例为空")
            return False
            
        # telethon格式：sticker是message对象
        if not (isinstance(sticker.media, MessageMediaDocument) and 
        hasattr(sticker.media.document, 'mime_type') and 
        'sticker' in sticker.media.document.mime_type):
            logger.error("消息不包含贴纸")
            return False
        
        # 获取文档对象
        document = sticker.media.document
        
        # 使用document ID作为file_unique_id
        file_unique_id = str(document.id)
        logger.info(f"贴纸document_id: {file_unique_id}")
        
        try:       
            sticker_info = get_sticker_info(file_unique_id)
            
            if sticker_info:
                md5 = sticker_info.get("md5", "")
                size = int(sticker_info.get("size", 0))
                name = sticker_info.get("name", "")
                logger.info(f"匹配到贴纸: {name}, md5: {md5}, size: {size}")
            else:
                # 如果没有匹配到，尝试下载贴纸并计算MD5
                logger.info("未找到贴纸信息，尝试下载计算")
                try:
                    sticker_bytes = await client.download_media(sticker, file=bytes)
                    if sticker_bytes:
                        md5 = hashlib.md5(sticker_bytes).hexdigest()
                        size = len(sticker_bytes)
                        logger.info(f"计算得到贴纸: md5: {md5}, size: {size}")
                    else:
                        logger.error("无法下载贴纸")
                        return False
                except Exception as e:
                    logger.error(f"下载贴纸失败: {e}")
                    return False
        
            payload = {
                "Md5": md5,
                "ToWxid": to_wxid,
                "TotalLen": size,
                "Wxid": config.MY_WXID
            }
            return wechat_api("/Msg/SendEmoji", payload)
            
        except Exception as e:
            logger.error(f"处理贴纸信息时出错: {e}")
            return False
            
    except Exception as e:
        logger.error(f"处理贴纸时出错: {e}")
        return False

async def _send_telethon_reply(to_wxid: str, message, client):
    """发送回复消息到微信 (添加client参数)"""
    try:
        if not client:
            logger.error("客户端实例为空")
            return False
            
        # telethon格式：message是message对象
        if not message.reply_to:
            logger.error("消息不包含回复信息")
            return False
        
        send_text = message.text or ""
        reply_to_message_id = message.reply_to.reply_to_msg_id
        
        # 查找对应的微信消息ID
        reply_to_wx_msgid = msgid_mapping.tg_to_wx(reply_to_message_id)
        if reply_to_wx_msgid is None:
            logger.warning(f"警告：找不到TG消息ID {reply_to_message_id} 对应的微信消息映射")
            # 处理找不到映射的情况，直接发送文本
            return _send_telethon_text(to_wxid, send_text)
        
        # 获取被回复的消息内容
        reply_to_text = ""
        try:
            # 尝试获取被回复的消息
            replied_message = await client.get_messages(message.chat_id, ids=reply_to_message_id)
            if replied_message and replied_message.text:
                reply_to_text = replied_message.text
            else:
                reply_to_text = "[原消息]"
                
        except Exception as e:
            logger.warning(f"无法获取被回复的消息内容: {e}")
            reply_to_text = "[原消息]"
        
        # 构建回复XML
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

async def revoke_message(event):
    """处理消息删除的具体逻辑"""
    try:
        for deleted_id in event.deleted_ids:
            wx_msg = msgid_mapping.tg_to_wx(deleted_id)
            if not wx_msg:
                return
            to_wxid = wx_msg["towxid"]
            new_msg_id = wx_msg["msgid"]
            client_msg_id = wx_msg["clientmsgid"]
            create_time = wx_msg["createtime"]
            # 这里实现具体的删除处理逻辑
            playload = {
                "ClientMsgId": client_msg_id,
                "CreateTime": create_time,
                "NewMsgId": new_msg_id,
                "ToUserName": to_wxid,
                "Wxid": config.MY_WXID
            }
            wechat_api("/Msg/Revoke", playload)
        
    except Exception as e:
        logger.error(f"处理消息删除逻辑时出错: {e}")