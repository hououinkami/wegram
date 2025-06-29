import asyncio
import base64
import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ffmpeg
import pilk
from telethon.events import NewMessage
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl, MessageEntityBlockquote

import config
from config import LOCALE as locale
from api import wechat_contacts, wechat_login
from api.wechat_api import wechat_api
from api.telegram_sender import telegram_sender
from service.telethon_client import get_client
from utils.contact_manager import contact_manager
from utils.message_mapper import msgid_mapping
from utils.sticker_mapper import get_sticker_info
from utils.telegram_to_wechat import add_send_msgid

logger = logging.getLogger(__name__)

# ==================== Telethon相关方法 ====================
# 处理Telethon更新中的消息
async def process_telethon_update(event: NewMessage.Event) -> None:
    client = get_client()
    message = event.message
    
    # 处理消息
    if message:
        message_id = message.id
        message_date = message.date
        
        # 获取chat_id
        if hasattr(message.peer_id, 'user_id'):
            chat_id = str(message.peer_id.user_id)
        elif hasattr(message.peer_id, 'channel_id'):
            chat_id = str(-100 + message.peer_id.channel_id)  # 转换为负数格式
        elif hasattr(message.peer_id, 'chat_id'):
            chat_id = str(-message.peer_id.chat_id)
        else:
            chat_id = str(message.peer_id)
            
        user_id = message.from_id.user_id if message.from_id else None
        sender = await message.get_sender()
        is_bot = sender.bot if sender and hasattr(sender, 'bot') else False
        
        # 判断是否为机器人消息
        if is_bot:
            return
        
        # 判断消息类型并处理
        if message.text:
            message_text = message.text
            # 更新联系人信息
            if message_text.startswith('/update'):
                to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
                if not to_wxid:
                    return False
                user_info = wechat_contacts.get_user_info(to_wxid)
                # 更新TG群组
                await wechat_contacts.update_info(chat_id, user_info.name, user_info.avatar_url)
                # 更新映射文件
                await contact_manager.update_contact_by_chatid(chat_id, {
                    "name": user_info.name,
                    "avatarLink": user_info.avatar_url
                })
                return
            
            # 删除联系人数据
            if message_text.startswith("/unbind"):
                to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
                unbind_result = await contact_manager.delete_contact(to_wxid)
                if unbind_result:
                    await telegram_sender.send_text(chat_id, locale.common("unbind"))
                return
            
            # 撤回
            if message_text.startswith("/rm") or message_text.startswith("/revoke"):
                if message.reply_to_msg_id:
                    await revoke_telethon(chat_id, message, client)
                    return
            
            # 是否接受信息
            if message_text.startswith("/message"):
                await contact_manager.update_contact_by_chatid(chat_id, {"isReceive": "toggle"})
                contact_now = await contact_manager.get_contact_by_chatid(chat_id)
                if contact_now["isReceive"]:
                    await telegram_sender.send_text(chat_id, locale.common("receive_on"))
                else:
                    await telegram_sender.send_text(chat_id, locale.common("receive_off"))
                return
            
            # 执行二次登录
            if message_text.startswith("/login"):
                relogin = wechat_login.twice_login(config.MY_WXID)
                if relogin.get('Message') == "登录成功":
                    await telegram_sender.send_text(chat_id, locale.common("twice_login_success"))
                else:
                    await telegram_sender.send_text(chat_id, locale.common("twice_login_fail"))
                return
            
            # 发送微信emoji
            if message_text.startswith('/'):
                emoji_text = '[' + message_text[1:] + ']'
                to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
                return await _send_telethon_text(to_wxid, emoji_text)

            if message_text in ["微笑", "撇嘴", "色", "发呆", "得意", "流泪", "害羞", "闭嘴", "睡", "大哭", "尴尬", "发怒", "调皮", "呲牙", "惊讶", "难过", "囧", "抓狂", "吐", "偷笑", "愉快", "白眼", "傲慢", "困", "惊恐", "憨笑", "悠闲", "咒骂", "疑问", "嘘", "晕", "衰", "骷髅", "敲打", "再见", "擦汗", "抠鼻", "鼓掌", "坏笑", "右哼哼", "鄙视", "委屈", "快哭了", "阴险", "亲亲", "可怜", "笑脸", "生病", "脸红", "破涕为笑", "恐惧", "失望", "无语", "嘿哈", "捂脸", "奸笑", "机智", "皱眉", "耶", "吃瓜", "加油", "汗", "天啊", "Emm", "社会社会", "旺柴", "好的", "打脸", "哇", "翻白眼", "666", "让我看看", "叹气", "苦涩", "裂开", "嘴唇", "爱心", "心碎", "拥抱", "强", "弱", "握手", "胜利", "抱拳", "勾引", "拳头", "OK", "合十", "啤酒", "咖啡", "蛋糕", "玫瑰", "凋谢", "菜刀", "炸弹", "便便", "月亮", "太阳", "庆祝", "礼物", "红包", "发", "福", "烟花", "爆竹", "猪头", "跳跳", "发抖", "转圈", "Smile", "Grimace", "Drool", "Scowl", "Chill", "Sob", "Shy", "Shutup", "Sleep", "Cry", "Awkward", "Pout", "Wink", "Grin", "Surprised", "Frown", "Tension", "Scream", "Puke", "Chuckle", "Joyful", "Slight", "Smug", "Drowsy", "Panic", "Laugh", "Loafer", "Scold", "Doubt", "Shhh", "Dizzy", "BadLuck", "Skull", "Hammer", "Bye", "Relief", "DigNose", "Clap", "Trick", "Bah！R", "Lookdown", "Wronged", "Puling", "Sly", "Kiss", "Whimper", "Happy", "Sick", "Flushed", "Lol", "Terror", "Let Down", "Duh", "Hey", "Facepalm", "Smirk", "Smart", "Concerned", "Yeah!", "Onlooker", "GoForIt", "Sweats", "OMG", "Respect", "Doge", "NoProb", "MyBad", "Wow", "Boring", "Awesome", "LetMeSee", "Sigh", "Hurt", "Broken", "Lip", "Heart", "BrokenHeart", "Hug", "Strong", "Weak", "Shake", "Victory", "Salute", "Beckon", "Fist", "Worship", "Beer", "Coffee", "Cake", "Rose", "Wilt", "Cleaver", "Bomb", "Poop", "Moon", "Sun", "Party", "Gift", "Packet", "Rich", "Blessing", "Fireworks", "Firecracker", "Pig", "Waddle", "Tremble", "Twirl"]:
                to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
                return await _send_telethon_text(to_wxid, f"[{message_text}]")

        # 转发消息
        wx_api_response = await forward_telethon_to_wx(chat_id, message, client)
        
        # 将消息添加进映射
        if wx_api_response:
            # 获取自己发送的消息对应Telethon的MsgID
            # telethon_msg_id = await get_telethon_msg_id(client, chat_id, 'me', message.text, message_date)
            telethon_msg_id = message_id

            add_send_msgid(wx_api_response, message_id, telethon_msg_id)

# 转发函数
async def forward_telethon_to_wx(chat_id: str, message, client) -> bool:
    to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
    
    if not to_wxid:
        logger.error(f"未找到chat_id {chat_id} 对应的微信ID")
        return False
    
    try:
        # 判断消息类型并处理
        if message.photo:
            # 发送附带文字
            if message.text:
                await _send_telethon_text(to_wxid, message.text)
            # 图片消息
            return await _send_telethon_photo(to_wxid, message, client)
            
        elif message.video:
            # 发送附带文字
            if message.text:
                await _send_telethon_text(to_wxid, message.text)
            # 视频消息
            return await _send_telethon_video(to_wxid, message, client)
        
        elif message.sticker:
            # 贴纸消息
            return await _send_telethon_sticker(to_wxid, message, client)
        
        elif message.voice:
            # 语音消息
            return await _send_telethon_voice(to_wxid, message, client)
        
        elif message.geo:
            # 定位消息
            return await _send_telethon_location(to_wxid, message)
        
        elif message.text:
            text = message.text

            # 判断是否为单纯文本信息
            msg_entities = message.entities or []
            is_url = False
            entity = None
            if msg_entities and len(msg_entities) > 0:
                entity = msg_entities[0]
                # 查找第一个链接实体
                for item in msg_entities:
                    if isinstance(item, (MessageEntityTextUrl, MessageEntityUrl)):
                        entity = item
                        is_url = True
                        break
    
            if message.reply_to_msg_id:
                # 回复消息
                return await _send_telethon_reply(to_wxid, message, client)
            elif msg_entities and is_url:
                # 链接消息
                return await _send_telethon_link(to_wxid, message)
            elif msg_entities and entity and isinstance(entity, MessageEntityBlockquote):
                # 转发群聊消息时去除联系人
                text = text.split('\n', 1)[1]
                return await _send_telethon_text(to_wxid, text)
            else:
                # 纯文本消息
                return await _send_telethon_text(to_wxid, text)
          
        else:
            return False
            
    except Exception as e:
        logger.error(f"转发消息时出错: {e}")
        return False


async def _send_telethon_text(to_wxid: str, text: str) -> bool:
    """发送文本消息到微信"""
    payload = {
        "At": "",
        "Content": text,
        "ToWxid": to_wxid,
        "Type": 1,
        "Wxid": config.MY_WXID
    }
    return await wechat_api("/Msg/SendTxt", payload)


async def _send_telethon_photo(to_wxid: str, message, client) -> bool:
    """发送图片消息到微信"""
    if not message.photo:
        logger.error("未收到照片数据")
        return False
    
    try:
        image_bytes = await client.download_media(message, file=bytes)
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        payload = {
            "Base64": image_base64,
            "ToWxid": to_wxid,
            "Wxid": config.MY_WXID
        }
        
        return await wechat_api("/Msg/UploadImg", payload)
    except Exception as e:
        logger.error(f"处理图片时出错: {e}")
        return False


async def _send_telethon_video(to_wxid: str, message, client) -> bool:
    """发送视频消息到微信"""
    if not message.video:
        logger.error("未收到视频数据")
        return False
    
    try:
        video_bytes = await client.download_media(message, file=bytes)
        video_base64 = base64.b64encode(video_bytes).decode('utf-8')
        duration = getattr(message.video, 'duration', 0)

        # 获取视频缩略图
        # thumb_base64 = ""
        # if hasattr(message.video, 'thumbs') and message.video.thumbs:
        #     thumb_bytes = await client.download_media(message.video.thumbs[-1], file=bytes)
        #     thumb_base64 = base64.b64encode(thumb_bytes).decode('utf-8')
        
        # 黑色缩略图
        thumb_base64 = "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAFAALQDASIAAhEBAxEB/8QAGAABAQEBAQAAAAAAAAAAAAAAAAECAwj/xAAgEAEBAQADAAICAwAAAAAAAAAAAREhMUECUSJhcYGR/8QAFgEBAQEAAAAAAAAAAAAAAAAAAAEC/8QAGBEBAQEBAQAAAAAAAAAAAAAAABEBMSH/2gAMAwEAAhEDEQA/APG9E1RrcAAqXmqkqiQAEgEAhSAESrAoCVUwazSFWJQ31AaEjItQMBoGqAtGcQCikAgFFqAAXoAAAATQE0MUJcPlZQpQBWVhQFEAUFzjRMQFgqBSAAAFnAAFCgbxhRL2C1MJeVEiZypKBACgmlKUKS4GgVQAwJD4rgqKiwEvYvqYBYQvKzoEFqQAvJQAKABSCaQEoiA0CaIA0LMxKAT+AotGmavgqXsL2AAQAvZYACxAA8KCWmkUTRloEToq1KCAA2UhQQoAAvxSiUXSq0hIAFoHXAJVX/UvIytRdQDABSpCoItRpKCAA3O0rXH2zQAAGpfWRnQ+V5KUpQBc40q1PQClJQFpSgKgBaCVGigRMWpoILoDVKGgheydroJFqeWjOh6XfoWVBDzCNAyAALc8QAPDzVoFIXtrBDtcwBk5+mqlBRkBqi+IAAABE3gBBkIuliUACAAQguftCgAUMC9JixK2GFVKCAA6eJU+gA9IAAsTeCL2gyLv5U1AACgALQBIbtFDZ9CAlUbEqNVkF0QBrwOyABD0CTKUATDFE0DQrMD0vB6AJVKAVPNWgkMUBLMVKijVSlRoAAaKGaAHoAAALn0nVA/sACgJuALUvCQLpC3E7qCzASgVaAJhSo2AAN1FjMGtISEWjIAAUhAABYFv6SkEW9ltrLUTRKi0qCeCxQTxFhKCxlq3GWgvNABqXAzkFoCUFGWgSKFCAkWAl7IvoAQAgSFTP2EUqQxIhTFgsGTwPAAAa0tCgkWJFFTpfEqilSF5qgkX0ATVElwFTtUwDTVjIALREWHaAB4CAANJVqUXCFWFBIasBU1Uw6BWV0gKy1GQWKmFAixkAaSFAqCzsEFqCAARe1vRChjLVSnYqpEWAoVNBUqALUFssAwpDQRYICxFhQQACgAAA0y1UomI0mKKkMWsgAsBFhFgMi+lBFpQC1ABYUiAAAFEAFwBYqYUZWJSoKAsFQWoA0yugXEXEAAAAAAAAADAAAGqlJcWiZjIAosSL0CAsBBe1wEMqLQQKmLooCAACxAASqEAAH//2Q=="
        
        payload = {
            "Base64": video_base64,
            "ImageBase64": thumb_base64,
            "PlayLength": int(duration),
            "ToWxid": to_wxid,
            "Wxid": config.MY_WXID
        }
        
        return await wechat_api("/Msg/SendVideo", payload)
    except Exception as e:
        logger.error(f"处理视频时出错: {e}")
        return False

async def _send_telethon_sticker(to_wxid: str, message, client) -> bool:
    """发送贴纸消息到微信"""
    if not message.sticker:
        logger.error("未收到贴纸数据")
        return False
            
    try:       
        # 下载贴纸文件用于获取信息
        sticker_bytes = await client.download_media(message, file=bytes)
        
        # 计算MD5和大小
        import hashlib
        md5_hash = hashlib.md5(sticker_bytes).hexdigest()
        file_size = len(sticker_bytes)
        
        # 尝试从缓存获取贴纸信息
        file_unique_id = f"{message.sticker.id}_{md5_hash[:8]}"
        sticker_info = get_sticker_info(file_unique_id)

        if sticker_info:
            md5 = sticker_info.get("md5", md5_hash)
            size = int(sticker_info.get("size", file_size))
            name = sticker_info.get("name", "")
        else:
            md5 = md5_hash
            size = file_size
            name = ""
        
        payload = {
            "Md5": md5,
            "ToWxid": to_wxid,
            "TotalLen": size,
            "Wxid": config.MY_WXID
        }
        return await wechat_api("/Msg/SendEmoji", payload)
    except Exception as e:
        logger.error(f"处理贴纸时出错: {e}")
        return False

async def _send_telethon_voice(to_wxid: str, message, client):
    """发送语音消息到微信"""
    if not message.voice:
        logger.error("未收到语音数据")
        return False

    # 语音信息
    duration = getattr(message.voice, 'duration', 0)
    download_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "download")
    voice_dir = os.path.join(download_dir, "voice")
    
    local_voice_path = None
    silk_path = None
    
    try:
        # 确保语音目录存在
        os.makedirs(voice_dir, exist_ok=True)
        
        # 1. 下载Telethon语音文件
        local_voice_path = await _download_telethon_voice(message, client, voice_dir)
        if not local_voice_path:
            logger.error("下载Telethon语音文件失败")
            return False
        
        # 2. 转换为SILK格式
        silk_path = await _convert_voice_to_silk(local_voice_path, str(message.id), voice_dir)
        if not silk_path:
            logger.error("转换语音文件为SILK格式失败")
            return False
        
        # 3. 生成base64
        silk_base64 = local_file_to_base64(silk_path)
        if not silk_base64:
            logger.error("转换SILK文件为base64失败")
            return False

        # 4. 发送SILK语音到微信
        voice_time = duration * 1000 if duration > 0 else 1000 # 如果微信API需要毫秒
        
        payload = {
            "Base64": silk_base64,
            "ToWxid": to_wxid,
            "Type": 4,
            "VoiceTime": voice_time,
            "Wxid": config.MY_WXID
        }
        
        return await wechat_api("/Msg/SendVoice", payload)
    
    except Exception as e:
        logger.error(f"处理Telethon语音消息失败: {e}")
        logger.error(traceback.format_exc())
        return False
    finally:
        # 清理临时文件
        files_to_clean = [
            (local_voice_path, "原始语音文件"),
            (silk_path, "SILK文件")
        ]
        
        for file_path, file_type in files_to_clean:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug(f"清理{file_type}: {file_path}")
                except Exception as e:
                    logger.warning(f"清理{file_type}失败 {file_path}: {e}")

async def _send_telethon_location(to_wxid: str, message) -> bool:
    """发送定位消息到微信"""
    # 获取定位信息
    if hasattr(message, 'venue') and message.venue:
        venue = message.venue
        geo = venue.geo
        latitude = geo.lat
        longitude = geo.long
        title = venue.title
        address = venue.address
    elif message.geo:
        geo = message.geo
        latitude = geo.lat
        longitude = geo.long
        title = ""
        address = ""
    else:
        return False

    payload = {
        "Infourl": "",
        "Label": address,
        "Poiname": title,
        "Scale": 0,
        "ToWxid": to_wxid,
        "Wxid": config.MY_WXID,
        "X": latitude,
        "Y": longitude
    }
    return await wechat_api("/Msg/ShareLocation", payload)

async def _send_telethon_reply(to_wxid: str, message, client):
    """发送回复消息到微信"""
    if not message.reply_to_msg_id:
        logger.error("未收到回复信息数据")
        return False
    try:
        send_text = message.text
        reply_to_message_id = message.reply_to_msg_id
        reply_to_wx_msgid = msgid_mapping.tg_to_wx(reply_to_message_id)
        if reply_to_wx_msgid is None:
            logger.warning(f"找不到TG消息ID {reply_to_message_id} 对应的微信消息映射")
            # 处理找不到映射的情况，可能需要跳过或使用默认值
            await _send_telethon_text(to_wxid, send_text)
            return True
            
        # 获取回复的消息内容
        reply_message = await client.get_messages(message.peer_id, ids=reply_to_message_id)
        reply_to_text = reply_message.text if reply_message and reply_message.text else ""
        
        reply_xml = f"""<appmsg appid="" sdkver="0"><title>{send_text}</title><des /><action /><type>57</type><showtype>0</showtype><soundtype>0</soundtype><mediatagname /><messageext /><messageaction /><content /><contentattr>0</contentattr><url /><lowurl /><dataurl /><lowdataurl /><songalbumurl /><songlyric /><appattach><totallen>0</totallen><attachid /><emoticonmd5 /><fileext /><aeskey /></appattach><extinfo /><sourceusername /><sourcedisplayname /><thumburl /><md5 /><statextstr /><refermsg><content>{reply_to_text}</content><type>1</type><svrid>{int(reply_to_wx_msgid["msgid"])}</svrid><chatusr>{reply_to_wx_msgid["fromwxid"]}</chatusr><fromusr>${to_wxid}</fromusr></refermsg></appmsg>"""
        payload = {
            "ToWxid": to_wxid,
            "Type": 49,
            "Wxid": config.MY_WXID,
            "Xml": reply_xml
        }
        return await wechat_api("/Msg/SendApp", payload)
    except Exception as e:
        logger.error(f"处理回复消息时出错: {e}")
        return False

async def _send_telethon_link(to_wxid: str, message):
    """处理链接信息"""
    text = message.text

    msg_entities = message.entities or []
    if msg_entities and len(msg_entities) > 0:
        entity = msg_entities[0]
        # 查找第一个链接实体
        for item in msg_entities:
            if isinstance(item, (MessageEntityTextUrl, MessageEntityUrl)):
                entity = item
                break

        if isinstance(entity, MessageEntityTextUrl) and hasattr(entity, 'url'):
            link_title = message.text[entity.offset:entity.offset + entity.length]
            link_url = entity.url
            link_desc = ''
        elif isinstance(entity, MessageEntityUrl):
            link_title = '非公众号链接'
            offset = entity.offset
            length = entity.length
            link_url = message.text[offset:offset + length]
            link_desc = link_url
        else:
            return False
        
        if link_title and link_url:
            xml_text = f"<appmsg><title>{link_title}</title><des>{link_desc}</des><type>5</type><url>{link_url}</url><thumburl></thumburl></appmsg>"

        payload = {
            "ToWxid": to_wxid,
            "Type": 49,
            "Wxid": config.MY_WXID,
            "Xml": xml_text
        }
        return await wechat_api('/Msg/SendApp', payload)

async def revoke_telethon(chat_id, message, client):
    try:
        delete_message_id = message.reply_to_msg_id
        delete_wx_msgid = msgid_mapping.tg_to_wx(delete_message_id)

        # 撤回失败时发送提示
        if not delete_wx_msgid:
            return await telegram_sender.send_text(chat_id, locale.common("revoke_failed"), reply_to_message_id=delete_message_id)
        
        # 撤回
        to_wxid = delete_wx_msgid["towxid"]
        new_msg_id = delete_wx_msgid["msgid"]
        client_msg_id = delete_wx_msgid["clientmsgid"]
        create_time = delete_wx_msgid["createtime"]
        
        payload = {
            "ClientMsgId": client_msg_id,
            "CreateTime": create_time,
            "NewMsgId": new_msg_id,
            "ToUserName": to_wxid,
            "Wxid": config.MY_WXID
        }
        await wechat_api("/Msg/Revoke", payload)

        # 删除撤回命令对应的消息
        await client.delete_messages(message.peer_id, [message.id])
        
    except Exception as e:
        logger.error(f"处理消息删除逻辑时出错: {e}")

# 获取文件的 Base64 编码
async def get_file_base64(message, client):
    """获取文件并转换为 Base64 格式"""
    try:
        # 下载文件到内存
        file_content = await client.download_media(message, file=bytes)
        
        # 转换为 Base64
        file_base64 = base64.b64encode(file_content).decode('utf-8')
        
        return file_base64
        
    except Exception as e:
        logger.error(f"获取文件并转换为Base64失败: {e}")
        return False

def local_file_to_base64(file_path: str) -> str:
    """将本地文件转换为base64编码"""
    try:
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return None
            
        with open(file_path, 'rb') as f:
            file_content = f.read()
            
        file_base64 = base64.b64encode(file_content).decode('utf-8')
        return file_base64
        
    except Exception as e:
        logger.error(f"转换文件为base64失败 {file_path}: {e}")
        return None

async def _download_telethon_voice(message, client, voice_dir: str) -> str:
    """
    下载Telethon语音文件
    
    Args:
        message: Telethon消息对象
        client: Telethon客户端
        voice_dir: 语音文件保存目录
        
    Returns:
        str: 下载成功返回本地文件路径，失败返回None
    """
    try:        
        # 构建本地路径
        file_extension = ".ogg"  # Telethon语音通常是ogg格式
        local_filename = f"{message.id}{file_extension}"
        local_voice_path = os.path.join(voice_dir, local_filename)
        
        # 确保目录存在
        os.makedirs(voice_dir, exist_ok=True)
        
        # 下载文件
        await client.download_media(message, file=local_voice_path)
        
        # 验证下载的文件
        if not os.path.exists(local_voice_path):
            logger.error("下载的语音文件不存在")
            return None
            
        downloaded_size = os.path.getsize(local_voice_path)
        
        if downloaded_size == 0:
            logger.error("下载的语音文件为空")
            os.remove(local_voice_path)
            return None
        
        return local_voice_path
        
    except Exception as e:
        logger.error(f"下载语音文件失败 (message_id: {message.id}): {e}")
        logger.error(traceback.format_exc())
        return None

async def _convert_voice_to_silk(input_path: str, file_id: str, voice_dir: str) -> Optional[str]:
    """
    异步将语音文件转换为SILK格式
    
    Args:
        input_path: 输入语音文件路径
        file_id: 文件ID（用于生成输出文件名）
        voice_dir: 输出目录
        
    Returns:
        Optional[str]: 转换成功返回SILK文件路径，失败返回None
    """
    pcm_path = None
    
    def _ffmpeg_convert(input_path: str, pcm_path: str) -> bool:
        """在线程中执行ffmpeg转换"""
        try:
            (
                ffmpeg
                .input(input_path)
                .output(
                    pcm_path,
                    format='s16le',          # 输出格式：16位小端PCM
                    acodec='pcm_s16le',      # 音频编码器
                    ar=44100,                # 采样率44100Hz
                    ac=1                     # 单声道
                )
                .overwrite_output()          # 覆盖输出文件
                .run(quiet=True)             # 静默运行，不输出到控制台
            )
            return True
        except ffmpeg.Error as e:
            logger.error(f"FFmpeg转换失败: {e}")
            return False
        except Exception as e:
            logger.error(f"FFmpeg转换异常: {e}")
            return False
    
    try:
        # 检查输入文件
        if not os.path.exists(input_path):
            logger.error(f"输入文件不存在: {input_path}")
            return None
            
        # 生成PCM文件路径
        pcm_filename = f"{file_id}.pcm"
        pcm_path = os.path.join(voice_dir, pcm_filename)
        
        # 生成SILK文件路径
        silk_filename = f"{file_id}.silk"
        silk_path = os.path.join(voice_dir, silk_filename)
        
        # 1. 使用ffmpeg转换为PCM格式
        loop = asyncio.get_event_loop()
        ffmpeg_success = await loop.run_in_executor(None, _ffmpeg_convert, input_path, pcm_path)
        
        if not ffmpeg_success:
            logger.error("FFmpeg转换PCM失败")
            return None
            
        # 验证PCM文件
        if not os.path.exists(pcm_path):
            logger.error("PCM文件转换失败，文件不存在")
            return None
            
        pcm_size = os.path.getsize(pcm_path)
        if pcm_size == 0:
            logger.error("PCM文件为空")
            return None
            
        # 2. 使用pilk将PCM转换为SILK
        def _pilk_convert():
            try:
                pilk.encode(pcm_path, silk_path, pcm_rate=44100, tencent=True)
                return True
            except Exception as e:
                logger.error(f"Pilk转换SILK失败: {e}")
                return False
        
        pilk_success = await loop.run_in_executor(None, _pilk_convert)
        
        if not pilk_success:
            logger.error("Pilk转换SILK失败")
            return None
            
        # 验证SILK文件
        if not os.path.exists(silk_path):
            logger.error("SILK文件转换失败，文件不存在")
            return None
            
        silk_size = os.path.getsize(silk_path)
        if silk_size == 0:
            logger.error("SILK文件为空")
            return None
            
        logger.debug(f"语音转换成功: {input_path} -> {silk_path} (PCM: {pcm_size}B, SILK: {silk_size}B)")
        return silk_path
        
    except Exception as e:
        logger.error(f"语音转换异常: {e}")
        logger.error(traceback.format_exc())
        return None
    finally:
        # 清理PCM临时文件
        if pcm_path and os.path.exists(pcm_path):
            try:
                os.remove(pcm_path)
                logger.debug(f"清理PCM临时文件: {pcm_path}")
            except Exception as e:
                logger.warning(f"清理PCM文件失败 {pcm_path}: {e}")

# 获取Telethon消息ID
async def get_telethon_msg_id(client, chat_id: str, sender: str, message_text: str, message_date: datetime) -> Optional[int]:
    """
    获取Telethon消息ID
    
    Args:
        client: Telethon客户端
        chat_id: 聊天ID
        sender: 发送者 ('me' 表示自己)
        message_text: 消息文本
        message_date: 消息时间
        
    Returns:
        Optional[int]: 消息ID，未找到返回None
    """
    try:
        # 将chat_id转换为适当的peer
        if chat_id.startswith('-100'):
            # 超级群组
            peer_id = int(chat_id[4:])
        elif chat_id.startswith('-'):
            # 普通群组
            peer_id = int(chat_id[1:])
        else:
            # 私聊
            peer_id = int(chat_id)
        
        # 获取最近的消息
        messages = await client.get_messages(peer_id, limit=50)
        
        # 查找匹配的消息
        for msg in messages:
            # 检查发送者
            if sender == 'me':
                if not msg.out:  # msg.out 表示是否为自己发送的消息
                    continue
            
            # 检查消息内容
            if msg.text == message_text:
                # 检查时间差（允许5秒误差）
                time_diff = abs((msg.date - message_date).total_seconds())
                if time_diff <= 5:
                    return msg.id
        
        logger.warning(f"未找到匹配的Telethon消息 (chat_id: {chat_id}, sender: {sender})")
        return None
        
    except Exception as e:
        logger.error(f"获取Telethon消息ID失败: {e}")
        return None
