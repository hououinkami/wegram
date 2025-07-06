import asyncio
import logging
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ffmpeg
import pilk
from telegram import Update

import config
from config import LOCALE as locale
from api.wechat_api import wechat_api
from api.telegram_sender import telegram_sender
from service.telethon_client import get_client
from utils import tools
from utils.contact_manager import contact_manager
from utils.message_mapper import msgid_mapping
from utils.sticker_converter import converter
from utils.sticker_mapper import get_sticker_info

logger = logging.getLogger(__name__)

# ==================== Telegram相关方法 ====================
# 处理Telegram更新中的消息
async def process_telegram_update(update: Update) -> None:
    # 处理消息
    if update.message:
        message = update.message
        message_id = message.message_id
        message_date = message.date
        chat_id = str(message.chat.id)
        user_id = message.from_user.id
        is_bot = message.from_user.is_bot
        
        # 跳过群组操作消息
        if (message.group_chat_created or 
            message.supergroup_chat_created or 
            message.delete_chat_photo or
            message.new_chat_photo or 
            message.new_chat_members or 
            message.left_chat_member or 
            message.new_chat_title or 
            message.pinned_message):
            return
        
        # 判断是否为机器人消息
        if is_bot:
            return
        
        # 判断消息类型并处理
        if message.text:
            to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
            if not to_wxid:
                return False

        # 转发消息
        wx_api_response = await forward_telegram_to_wx(chat_id, message)
        
        # 将消息添加进映射
        if wx_api_response:
            # 获取自己发送的消息对应Telethon的MsgID
            telethon_client = get_client()
            telethon_msg_id = await get_telethon_msg_id(telethon_client, abs(int(chat_id)), 'me', message.text, message_date)

            add_send_msgid(wx_api_response, message_id, telethon_msg_id)

# 转发函数
async def forward_telegram_to_wx(chat_id: str, message) -> bool:
    to_wxid = await contact_manager.get_wxid_by_chatid(chat_id)
    
    if not to_wxid:
        logger.error(f"未找到chat_id {chat_id} 对应的微信ID")
        return False
    
    try:
        # 判断消息类型并处理
        if message.text:
            text = message.text

            # 判断是否为单纯文本信息
            msg_entities = message.entities or []
            is_url = False
            entity = None
            if msg_entities and len(msg_entities) > 0:
                entity = msg_entities[0]
                # 查找第一个链接实体
                for item in msg_entities:
                    if item.type in ['text_link', 'url']:
                        entity = item
                        is_url = True
                        break
    
            if message.reply_to_message:
                # 回复消息
                return await _send_telegram_reply(to_wxid, message)
            elif msg_entities and is_url:
                # 链接消息
                return await _send_telegram_link(to_wxid, message)
            elif msg_entities and entity and entity.type == "expandable_blockquote":
                # 转发群聊消息时去除联系人
                text = text.split('\n', 1)[1]
                return await _send_telegram_text(to_wxid, text)
            else:
                # 纯文本消息
                # 处理文本中的emoji
                processed_text = process_emoji_text(text)
                return await _send_telegram_text(to_wxid, processed_text)
            
        elif message.photo:
            # 发送附带文字
            if message.caption:
                await _send_telegram_text(to_wxid, message.caption)
            # 图片消息
            return await _send_telegram_photo(to_wxid, message.photo)
            
        elif message.video:
            # 发送附带文字
            if message.caption:
                await _send_telegram_text(to_wxid, message.caption)
            # 视频消息
            return await _send_telegram_video(to_wxid, message.video)
        
        elif message.sticker:
            # 贴纸消息
            return await _send_telegram_sticker(to_wxid, message.sticker)
        
        elif message.voice:
            # 语音消息
            return await _send_telegram_voice(to_wxid, message.voice)
        
        elif message.document:
            # 发送附带文字
            if message.caption:
                await _send_telegram_text(to_wxid, message.caption)
            # 文档消息
            return await _send_telegram_document(to_wxid, message.document)

        elif message.location:
            # 定位消息
            return await _send_telegram_location(to_wxid, message)

        else:
            return False
            
    except Exception as e:
        logger.error(f"转发消息时出错: {e}")
        return False


async def _send_telegram_text(to_wxid: str, text: str) -> bool:
    """发送文本消息到微信"""
    payload = {
        "At": "",
        "Content": text,
        "ToWxid": to_wxid,
        "Type": 1,
        "Wxid": config.MY_WXID
    }
    return await wechat_api("SEND_TEXT", payload)


async def _send_telegram_photo(to_wxid: str, photo: list) -> bool:
    """发送图片消息到微信"""
    if not photo:
        logger.error("未收到照片数据")
        return False
    
    # 获取最大尺寸的照片文件ID
    file_id = photo[-1].file_id  # 最后一个通常是最大尺寸
    
    try:
        image_base64 = await tools.telegram_file_to_base64(file_id)
        
        payload = {
            "Base64": image_base64,
            "ToWxid": to_wxid,
            "Wxid": config.MY_WXID
        }
        
        return await wechat_api("SEND_IMAGE", payload)
    except Exception as e:
        logger.error(f"处理图片时出错: {e}")
        return False


async def _send_telegram_video(to_wxid: str, video) -> bool:
    """发送视频消息到微信"""
    if not video:
        logger.error("未收到视频数据")
        return False
    
    # 获取视频与缩略图文件ID
    file_id = video.file_id
    thumb_file_id = video.thumbnail.file_id
    duration = video.duration
    
    try:
        video_base64 = await tools.telegram_file_to_base64(file_id)
        thumb_base64 = await tools.telegram_file_to_base64(thumb_file_id)
        
        payload = {
            "Base64": video_base64,
            "ImageBase64": thumb_base64,
            "PlayLength": int(duration),
            "ToWxid": to_wxid,
            "Wxid": config.MY_WXID
        }
        
        return await wechat_api("SEND_VIDEO", payload)
    except Exception as e:
        logger.error(f"处理视频时出错: {e}")
        return False

async def _send_telegram_sticker(to_wxid: str, sticker) -> bool:
    """发送贴纸消息到微信"""
    if not sticker:
        logger.error("未收到贴纸数据")
        return False
    
    # 提取贴纸的file_unique_id
    file_unique_id = sticker.file_unique_id
    try:
        sticker_info = get_sticker_info(file_unique_id)
        payload = {}

        if sticker_info:
            md5 = sticker_info.get("md5", "")
            len = int(sticker_info.get("size", 0))
            name = sticker_info.get("name", "")
        
            payload = {
                "Md5": md5,
                "ToWxid": to_wxid,
                "TotalLen": len,
                "Wxid": config.MY_WXID
            }
        else:
            # 下载并转换
            try:
                # 下载贴纸
                sticker_path = await _download_telegram_sticker(sticker)

                # 根据文件类型选择转换方法
                file_extension = Path(sticker_path).suffix
                gif_path = None
                
                if file_extension == '.tgs':
                    # TGS 动画贴纸
                    gif_path = await converter.tgs_to_gif(sticker_path)
                
                elif file_extension == '.webm':
                    # WebM 视频贴纸处理
                    gif_path = await converter.webm_to_gif(sticker_path)

                elif file_extension == '.webp':
                    # WebP 可能是动画也可能是静态
                    gif_path = await converter.webp_to_gif(sticker_path)
                
                if not gif_path:
                    logger.error(f"转换失败: {sticker_path}")
                    return False
                
                # 转换成功，准备发送
                # sticker_base64 = tools.local_file_to_base64(gif_path)
                # if not sticker_base64:
                #     logger.error("转换贴纸文件为base64失败")
                #     return False
                    
                payload = {
                    "Md5": "",
                    "TotalLen": 0,
                    # "Base64": sticker_base64,
                    "ToWxid": to_wxid,
                    "Wxid": config.MY_WXID
                }
                
            except Exception as e:
                logger.error(f"下载并转换贴纸失败: {e}")
                return False
        
        # 执行发送操作
        result = await wechat_api("SEND_EMOJI", payload)

        if result.get("Data", {}):
            return result
        else:
            err_msg = result.get("Message", {})
            logger.error(f"贴纸发送失败: {err_msg}")
    
    except Exception as e:
        logger.error(f"处理贴纸时出错: {e}")
        return False

async def _send_telegram_voice(to_wxid: str, voice):
    """发送语音消息到微信"""
    if not voice:
        logger.error("未收到语音数据")
        return False

    # 语音信息
    file_id = voice.file_id
    duration = voice.duration
    file_size = voice.file_size
    download_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "download")
    voice_dir = os.path.join(download_dir, "voice")
    
    local_voice_path = None
    silk_path = None
    
    try:
        # 确保语音目录存在
        os.makedirs(voice_dir, exist_ok=True)
        
        # 1. 下载Telegram语音文件
        local_voice_path = await _download_telegram_voice(file_id, voice_dir)
        if not local_voice_path:
            logger.error("下载Telegram语音文件失败")
            return False
        
        # 2. 转换为SILK格式
        silk_path = await _convert_voice_to_silk(local_voice_path, file_id, voice_dir)
        if not silk_path:
            logger.error("转换语音文件为SILK格式失败")
            return False
        
        # 3. 生成base64
        silk_base64 = tools.local_file_to_base64(silk_path)
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
        
        return await wechat_api("SEND_VOICE", payload)
    
    except Exception as e:
        logger.error(f"处理Telegram语音消息失败: {e}")
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

async def _send_telegram_document(to_wxid: str, document) -> bool:
    """发送文档消息到微信"""
    if not document:
        logger.error("未收到文档数据")
        return False
    
    try:
        # 获取文件信息
        file_id = document.file_id
        file_name = document.file_name or f"document_{file_id}"
        file_size = document.file_size
        mime_type = document.mime_type
        
        # 检查文件大小限制
        max_size = 50 * 1024 * 1024  # 50MB
        if file_size and file_size > max_size:
            logger.error(f"文件太大: {file_size} bytes (限制: {max_size} bytes)")
            return False
        
        # 下载文件并转换为base64
        file_base64 = await tools.telegram_file_to_base64(file_id)
        if not file_base64:
            logger.error("获取文件base64失败")
            return False
        
        # if not file_base64.startswith('data:'):
        #     # 如果没有数据URL前缀，添加它
        #     file_base64 = f"data:{mime_type or 'application/octet-stream'};base64,{file_base64}"
        
        # 构建发送载荷
        payload = {
            "Wxid": config.MY_WXID,
            "fileData": file_base64
        }
        
        upload_file = await wechat_api("UPLOAD_FILE", payload)
        logger.warning(upload_file)
        return upload_file
        
    except Exception as e:
        logger.error(f"处理文档时出错: {e}")
        return False

async def _send_telegram_location(to_wxid: str, message) -> bool:
    """发送定位消息到微信"""
    # 获取定位信息
    if message.venue:
        venue = message.venue
        location = venue.location
        latitude = location.latitude
        longitude = location.longitude
        title = venue.title
        address = venue.address
    elif message.location:
        location = message.location
        latitude = location.latitude
        longitude = location.longitude
        title = ""
        address = ""

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
    return await wechat_api("SEND_LOCATION", payload)

async def _send_telegram_reply(to_wxid: str, message):
    """发送回复消息到微信"""
    if not message.reply_to_message:
        logger.error("未收到回复信息数据")
        return False
    try:
        send_text = message.text
        reply_to_message = message.reply_to_message
        reply_to_message_id = reply_to_message.message_id
        reply_to_wx_msgid = msgid_mapping.tg_to_wx(reply_to_message_id)
        if reply_to_wx_msgid is None:
            logger.warning(f"找不到TG消息ID {reply_to_message_id} 对应的微信消息映射")
            # 处理找不到映射的情况，可能需要跳过或使用默认值
            await _send_telegram_text(to_wxid, send_text)
        reply_to_text = reply_to_message.text or ""
        reply_xml = f"""<appmsg appid="" sdkver="0"><title>{send_text}</title><des /><action /><type>57</type><showtype>0</showtype><soundtype>0</soundtype><mediatagname /><messageext /><messageaction /><content /><contentattr>0</contentattr><url /><lowurl /><dataurl /><lowdataurl /><songalbumurl /><songlyric /><appattach><totallen>0</totallen><attachid /><emoticonmd5 /><fileext /><aeskey /></appattach><extinfo /><sourceusername /><sourcedisplayname /><thumburl /><md5 /><statextstr /><refermsg><content>{reply_to_text}</content><type>1</type><svrid>{int(reply_to_wx_msgid["msgid"])}</svrid><chatusr>{reply_to_wx_msgid["fromwxid"]}</chatusr><fromusr>${to_wxid}</fromusr></refermsg></appmsg>"""
        payload = {
            "ToWxid": to_wxid,
            "Type": 49,
            "Wxid": config.MY_WXID,
            "Xml": reply_xml
        }
        return await wechat_api("SEND_APP", payload)
    except Exception as e:
        logger.error(f"处理回复消息时出错: {e}")
        return False

async def _send_telegram_link(to_wxid: str, message):
    """处理链接信息"""
    text = message.text

    msg_entities = message.entities or []
    if msg_entities and len(msg_entities) > 0:
        entity = msg_entities[0]
        # 查找第一个链接实体
        for item in msg_entities:
            if item.type in ['text_link', 'url']:
                entity = item
                break

        if entity.type == 'text_link' and entity.url:
            link_title = message.text
            link_url = entity.url
            link_desc = ''
        elif entity.type == 'url':
            link_title = '分享链接'
            offset = entity.offset
            length = entity.length
            link_url = message.text[offset:offset + length]
            link_desc = link_url
        
        if link_title and link_url:
            text = f"<appmsg><title>{link_title}</title><des>{link_desc}</des><type>5</type><url>{link_url}</url><thumburl></thumburl></appmsg>"

        payload = {
            "ToWxid": to_wxid,
            "Type": 49,
            "Wxid": config.MY_WXID,
            "Xml": text
        }
        return await wechat_api('SEND_APP', payload)

async def revoke_by_telegram_bot_command(chat_id, message):
    try:
        delete_message = message.reply_to_message
        delete_message_id = delete_message.message_id
        delete_wx_msgid = msgid_mapping.tg_to_wx(delete_message_id)

        # 撤回失败时发送提示
        if not delete_wx_msgid:
            return await telegram_sender.send_text(chat_id, locale.command("revoke_failed"), reply_to_message_id=delete_message_id)
        
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
        await wechat_api("REVOKE", payload)

        # 删除撤回命令对应的消息
        await telegram_sender.delete_message(chat_id, message.message_id)
        
    except Exception as e:
        logger.error(f"处理消息删除逻辑时出错: {e}")

async def _download_telegram_voice(file_id: str, voice_dir: str) -> str:
    """
    下载Telegram语音文件
    
    Args:
        file_id: Telegram文件ID
        voice_dir: 语音文件保存目录
        
    Returns:
        str: 下载成功返回本地文件路径，失败返回None
    """
    try:        
        # 1. 获取文件信息
        file = await telegram_sender.get_file(file_id)
        
        # 2. 构建本地路径
        # 生成本地文件名（使用file_id作为文件名，保持原扩展名）
        file_extension = Path(file.file_path).suffix or ".ogg"
        local_filename = f"{file_id}{file_extension}"
        local_voice_path = os.path.join(voice_dir, local_filename)
        
        # 确保目录存在
        os.makedirs(voice_dir, exist_ok=True)
        
        # 3. 下载文件
        await file.download_to_drive(local_voice_path)
        
        # 4. 验证下载的文件
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
        logger.error(f"下载语音文件失败 (file_id: {file_id}): {e}")
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
            logger.error(f"ffmpeg转换失败: {e.stderr.decode() if e.stderr else str(e)}")
            return False
        except Exception as e:
            logger.error(f"ffmpeg转换过程中出现异常: {e}")
            return False
    
    def _pilk_convert(pcm_path: str, silk_path: str) -> Optional[float]:
        """在线程中执行pilk转换"""
        try:
            silk_duration = pilk.encode(
                pcm_path, 
                silk_path, 
                pcm_rate=44100, 
                tencent=True
            )
            return silk_duration
        except Exception as e:
            logger.error(f"pilk转换失败: {e}")
            return None
    
    def _file_exists_and_size(file_path: str) -> tuple[bool, int]:
        """检查文件是否存在并返回大小"""
        if os.path.exists(file_path):
            return True, os.path.getsize(file_path)
        return False, 0
    
    def _remove_file(file_path: str) -> bool:
        """删除文件"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                return True
        except Exception as e:
            logger.warning(f"删除文件失败 {file_path}: {e}")
        return False
    
    try:
        # 1. 准备文件路径
        pcm_filename = f"{file_id}.pcm"
        pcm_path = os.path.join(voice_dir, pcm_filename)
        silk_filename = f"{file_id}.silk"
        silk_path = os.path.join(voice_dir, silk_filename)
        
        # 确保输出目录存在
        await asyncio.to_thread(os.makedirs, voice_dir, exist_ok=True)
        
        # 2. 异步执行ffmpeg转换
        ffmpeg_success = await asyncio.to_thread(_ffmpeg_convert, input_path, pcm_path)
        
        if not ffmpeg_success:
            return None
        
        # 验证PCM文件
        pcm_exists, pcm_size = await asyncio.to_thread(_file_exists_and_size, pcm_path)
        if not pcm_exists:
            logger.error("PCM文件未生成")
            return None
        
        if pcm_size == 0:
            logger.error("PCM文件为空")
            await asyncio.to_thread(_remove_file, pcm_path)
            return None
        
        # 3. 异步执行SILK转换
        silk_duration = await asyncio.to_thread(_pilk_convert, pcm_path, silk_path)
        
        if silk_duration is None:
            return None
        
        # 验证SILK文件
        silk_exists, silk_size = await asyncio.to_thread(_file_exists_and_size, silk_path)
        if not silk_exists:
            logger.error("SILK文件未生成")
            return None
        
        if silk_size == 0:
            logger.error("SILK文件为空")
            await asyncio.to_thread(_remove_file, silk_path)
            return None
        
        return silk_path
        
    except Exception as e:
        logger.error(f"转换过程中出现异常: {e}")
        logger.error(traceback.format_exc())
        return None
    finally:
        # 异步清理PCM临时文件
        if pcm_path:
            try:
                removed = await asyncio.to_thread(_remove_file, pcm_path)
                if removed:
                    logger.debug(f"清理PCM临时文件: {pcm_path}")
            except Exception as e:
                logger.warning(f"清理PCM临时文件失败 {pcm_path}: {e}")

async def _download_telegram_sticker(sticker) -> str:
    """从 Telegram Update 对象下载贴纸到本地"""
    try:
        # 检查是否有贴纸消息
        if not sticker:
            return None
        
        file_id = sticker.file_id
        file_unique_id = sticker.file_unique_id
        
        # 设置下载目录
        download_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "download")
        sticker_dir = os.path.join(download_dir, "sticker")
        os.makedirs(sticker_dir, exist_ok=True)
        
        # 检查是否已存在文件
        possible_extensions = ['.webp', '.tgs', '.webm', '.png', '.jpg', '.jpeg']
        for ext in possible_extensions:
            existing_path = os.path.join(sticker_dir, f"{file_unique_id}{ext}")
            if os.path.exists(existing_path):
                return existing_path
        
        # 获取文件信息并下载
        file = await telegram_sender.get_file(file_id)
        
        # 确定文件扩展名
        file_extension = Path(file.file_path).suffix
        if not file_extension:
            # 根据贴纸类型推断扩展名
            if sticker.is_animated:
                file_extension = ".tgs"
            elif sticker.is_video:
                file_extension = ".webm"
            else:
                file_extension = ".webp"
        
        local_filename = f"{file_unique_id}{file_extension}"
        local_path = os.path.join(sticker_dir, local_filename)
        
        # 下载文件
        await file.download_to_drive(local_path)
        
        # 验证下载
        if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
            logger.error(f"下载失败或文件为空: {local_path}")
            if os.path.exists(local_path):
                os.remove(local_path)
            return None
        
        file_size = os.path.getsize(local_path)
        
        return local_path
        
    except Exception as e:
        logger.error(f"下载贴纸失败: {e}")
        return None

# 添加msgid映射
def add_send_msgid(wx_api_response, tg_msgid, telethon_msg_id: int = 0):
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
        to_wx_id = tools.multi_get(response_data, 'ToUsetName.string', 'toUserName.string', 'ToUserName.string', 'toUserName', 'ToUserName')
        new_msg_id = tools.multi_get(response_data, 'NewMsgId', 'Newmsgid', 'newMsgId')
        client_msg_id = tools.multi_get(response_data, 'ClientMsgid', 'ClientImgId.string', 'clientmsgid', 'clientMsgId')
        create_time = tools.multi_get(response_data, 'Createtime', 'createtime', 'createTime', 'CreateTime')
        if new_msg_id:
            msgid_mapping.add(
                tg_msg_id=tg_msgid,
                from_wx_id=config.MY_WXID,
                to_wx_id=to_wx_id,
                wx_msg_id=new_msg_id,
                client_msg_id=client_msg_id,
                create_time=create_time,
                content="",
                telethon_msg_id=telethon_msg_id
            )
        else:
            logger.warning(f"NewMsgId 不存在: {response_data}")
    else:
        logger.warning("消息列表为空")

async def get_telethon_msg_id(client, chat_id, sender_id, text=None, send_time=None, tolerance=2):
    """根据时间和文本获取Telethon消息ID"""    
    # 转换时间格式
    if isinstance(send_time, (int, float)):
        target_time = datetime.fromtimestamp(send_time, tz=timezone.utc)
    else:
        target_time = send_time.replace(tzinfo=timezone.utc) if send_time.tzinfo is None else send_time
    
    # 获取指定发送者的最近消息
    messages = await client.get_messages(chat_id, limit=5, from_user=sender_id)
    
    for msg in messages:
        msg_time = msg.date.replace(tzinfo=timezone.utc) if msg.date.tzinfo is None else msg.date
        time_diff = abs((msg_time - target_time).total_seconds())
        
        # 检查时间和文本匹配
        if time_diff == 0:
            return msg.id
        elif time_diff <= tolerance:
            if text is None or msg.text == text:
                return msg.id
    
    return None

async def revoke_telethon(event):
    try:
        for deleted_id in event.deleted_ids:
            wx_msg = msgid_mapping.telethon_to_wx(deleted_id)
            if not wx_msg:
                return
            to_wxid = wx_msg["towxid"]
            new_msg_id = wx_msg["msgid"]
            client_msg_id = wx_msg["clientmsgid"]
            create_time = wx_msg["createtime"]
            
            payload = {
                "ClientMsgId": client_msg_id,
                "CreateTime": create_time,
                "NewMsgId": new_msg_id,
                "ToUserName": to_wxid,
                "Wxid": config.MY_WXID
            }
            await wechat_api("REVOKE", payload)
        
    except Exception as e:
        logger.error(f"处理消息删除逻辑时出错: {e}")

# 定义emoji列表
EMOJI_LIST = ["微笑", "撇嘴", "色", "发呆", "得意", "流泪", "害羞", "闭嘴", "睡", "大哭", "尴尬", "发怒", "调皮", "呲牙", "惊讶", "难过", "囧", "抓狂", "吐", "偷笑", "愉快", "白眼", "傲慢", "困", "惊恐", "憨笑", "悠闲", "咒骂", "疑问", "嘘", "晕", "衰", "骷髅", "敲打", "再见", "擦汗", "抠鼻", "鼓掌", "坏笑", "右哼哼", "鄙视", "委屈", "快哭了", "阴险", "亲亲", "可怜", "笑脸", "生病", "脸红", "破涕为笑", "恐惧", "失望", "无语", "嘿哈", "捂脸", "奸笑", "机智", "皱眉", "耶", "吃瓜", "加油", "汗", "天啊", "Emm", "社会社会", "旺柴", "好的", "打脸", "哇", "翻白眼", "666", "让我看看", "叹气", "苦涩", "裂开", "嘴唇", "爱心", "心碎", "拥抱", "强", "弱", "握手", "胜利", "抱拳", "勾引", "拳头", "OK", "合十", "啤酒", "咖啡", "蛋糕", "玫瑰", "凋谢", "菜刀", "炸弹", "便便", "月亮", "太阳", "庆祝", "礼物", "红包", "发", "福", "烟花", "爆竹", "猪头", "跳跳", "发抖", "转圈", "Smile", "Grimace", "Drool", "Scowl", "Chill", "Sob", "Shy", "Shutup", "Sleep", "Cry", "Awkward", "Pout", "Wink", "Grin", "Surprised", "Frown", "Tension", "Scream", "Puke", "Chuckle", "Joyful", "Slight", "Smug", "Drowsy", "Panic", "Laugh", "Loafer", "Scold", "Doubt", "Shhh", "Dizzy", "BadLuck", "Skull", "Hammer", "Bye", "Relief", "DigNose", "Clap", "Trick", "Bah！R", "Lookdown", "Wronged", "Puling", "Sly", "Kiss", "Whimper", "Happy", "Sick", "Flushed", "Lol", "Terror", "Let Down", "Duh", "Hey", "Facepalm", "Smirk", "Smart", "Concerned", "Yeah!", "Onlooker", "GoForIt", "Sweats", "OMG", "Respect", "Doge", "NoProb", "MyBad", "Wow", "Boring", "Awesome", "LetMeSee", "Sigh", "Hurt", "Broken", "Lip", "Heart", "BrokenHeart", "Hug", "Strong", "Weak", "Shake", "Victory", "Salute", "Beckon", "Fist", "Worship", "Beer", "Coffee", "Cake", "Rose", "Wilt", "Cleaver", "Bomb", "Poop", "Moon", "Sun", "Party", "Gift", "Packet", "Rich", "Blessing", "Fireworks", "Firecracker", "Pig", "Waddle", "Tremble", "Twirl"]

def process_emoji_text(text):
    """处理文本中的emoji关键词：字符串开头的或前面带空格的，并去掉emoji后面的空格"""
    # 按长度降序排列，避免短词匹配覆盖长词
    sorted_emojis = sorted(EMOJI_LIST, key=len, reverse=True)

    # 自定义替换
    text = text.replace("滑稽", "奸笑")
    
    for emoji in sorted_emojis:
        # 匹配：(字符串开头或空格) + emoji + (可选的多个空格)
        pattern = r'(^| )' + re.escape(emoji) + r'( *)\b'
        
        # 直接替换为[emoji]，不保留前后的空格
        text = re.sub(pattern, f'[{emoji}]', text)
    
    return text
