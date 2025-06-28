import asyncio
import io
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from telegram import Bot, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.error import NetworkError, TelegramError, TimedOut

import config
from utils.locales import Locale

logger = logging.getLogger(__name__)
locale = Locale(config.LANG)

class TelegramSender:
    """
    基于线程本地存储的 Telegram 消息发送器
    
    特性：
    - 线程安全：每个线程独立的 Bot 实例
    - 自动重试：网络错误自动重试
    - 资源管理：支持手动和自动清理
    - 错误恢复：Bot 实例异常时自动重新创建
    """
    
    def __init__(self, bot_token: str, default_chat_id: Optional[int] = None, 
                 max_retries: int = 3, retry_delay: float = 1.0):
        """
        初始化 TelegramSender
        
        Args:
            bot_token: Telegram Bot Token
            default_chat_id: 默认聊天ID（可选）
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
        """
        self.bot_token = bot_token
        self.default_chat_id = default_chat_id
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._local = threading.local()
        
        logger.info(f"TelegramSender 初始化完成，线程本地存储模式")

    @property
    def bot(self) -> Bot:
        """
        线程安全的 Bot 实例获取
        
        Returns:
            Bot: 当前线程的 Bot 实例
        """
        try:
            # 检查现有 Bot 是否有效
            if (hasattr(self._local, 'bot') and 
                self._local.bot is not None):
                return self._local.bot
        except Exception as e:
            logger.warning(f"访问现有 Bot 实例时出错: {e}")
            # 清理可能损坏的实例
            self._local.bot = None
        
        # 创建新的 Bot 实例
        try:
            self._local.bot = Bot(token=self.bot_token)
            thread_name = threading.current_thread().name
            logger.debug(f"为线程 {thread_name} 创建新的 Bot 实例")
            return self._local.bot
        except Exception as e:
            logger.error(f"创建 Bot 实例失败: {e}")
            raise

    def cleanup_current_bot(self):
        """清理当前线程的 Bot 实例"""
        if hasattr(self._local, 'bot'):
            self._local.bot = None
            thread_name = threading.current_thread().name
            logger.debug(f"清理线程 {thread_name} 的 Bot 实例")

    async def cleanup_current_bot_async(self):
        """异步清理当前线程的 Bot 实例"""
        if hasattr(self._local, 'bot') and self._local.bot is not None:
            try:
                await self._local.bot.shutdown()
                thread_name = threading.current_thread().name
                logger.debug(f"异步清理线程 {thread_name} 的 Bot 实例")
            except Exception as e:
                logger.warning(f"清理 Bot 实例时出错: {e}")
            finally:
                self._local.bot = None

    async def _retry_operation(self, operation, *args, **kwargs):
        """
        带重试的操作执行器
        
        Args:
            operation: 要执行的异步操作
            *args, **kwargs: 操作参数
            
        Returns:
            操作结果
        """
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                return await operation(*args, **kwargs)
            except (NetworkError, TimedOut) as e:
                last_exception = e
                if attempt < self.max_retries:
                    wait_time = self.retry_delay * (2 ** attempt)  # 指数退避
                    logger.warning(f"操作失败，{wait_time}秒后重试 (尝试 {attempt + 1}/{self.max_retries + 1}): {e}")
                    await asyncio.sleep(wait_time)
                    
                    # 网络错误时重新创建 Bot 实例
                    self.cleanup_current_bot()
                else:
                    logger.error(f"操作最终失败，已重试 {self.max_retries} 次: {e}")
                    break
            except TelegramError as e:
                # 非网络错误，不重试
                logger.error(f"Telegram API 错误: {e}")
                raise
            except Exception as e:
                # 其他未知错误
                logger.error(f"未知错误: {e}")
                raise
        
        # 所有重试都失败了
        raise last_exception

    async def send_text(self, chat_id: Optional[int] = None, text: str = "", 
                       reply_to_message_id: Optional[int] = None, 
                       parse_mode: str = ParseMode.HTML, 
                       disable_web_page_preview: bool = False,
                       reply_markup: Optional[InlineKeyboardMarkup] = None):
        """
        发送文本消息
        
        Args:
            chat_id: 聊天ID，为空时使用默认值
            text: 消息文本
            parse_mode: 解析模式
            reply_to_message_id: 回复的消息ID
            disable_web_page_preview: 禁用网页预览
            reply_markup: 内联键盘
            
        Returns:
            Message: 发送的消息对象
        """
        if not text.strip():
            raise ValueError("消息文本不能为空")
        
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        return await self._retry_operation(
            self.bot.send_message,
            chat_id=target_chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_to_message_id=reply_to_message_id,
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=reply_markup
        )

    async def send_photo(self, chat_id: Optional[int] = None, photo: Union[str, Path, io.BytesIO, bytes] = None, caption: str = "", 
                        parse_mode: str = ParseMode.HTML,
                        reply_to_message_id: Optional[int] = None,
                        reply_markup: Optional[InlineKeyboardMarkup] = None):
        """
        发送图片
        
        Args:
            photo: 图片文件路径、Path对象、BytesIO对象或字节数据
            chat_id: 聊天ID，为空时使用默认值
            caption: 图片说明
            parse_mode: 解析模式
            reply_to_message_id: 回复的消息ID
            reply_markup: 内联键盘
            
        Returns:
            Message: 发送的消息对象
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        # 处理不同类型的图片输入
        if isinstance(photo, (str, Path)):
            photo_path = Path(photo)
            if not photo_path.exists():
                raise FileNotFoundError(f"图片文件不存在: {photo_path}")
            photo_input = InputFile(photo_path.open('rb'), filename=photo_path.name)
        elif isinstance(photo, io.BytesIO):
            photo_input = InputFile(photo, filename=f"{locale.type(3)}.jpg")
        elif isinstance(photo, bytes):
            photo_input = InputFile(io.BytesIO(photo), filename=f"{locale.type(3)}.jpg")
        else:
            photo_input = photo
        
        return await self._retry_operation(
            self.bot.send_photo,
            chat_id=target_chat_id,
            photo=photo_input,
            caption=caption,
            parse_mode=parse_mode,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup
        )

    async def send_document(self, chat_id: Optional[int] = None, document: Union[str, Path, io.BytesIO, bytes] = None, caption: str = "", 
                           parse_mode: str = ParseMode.HTML,
                           reply_to_message_id: Optional[int] = None,
                           reply_markup: Optional[InlineKeyboardMarkup] = None,
                           filename: Optional[str] = None):
        """
        发送文档
        
        Args:
            document: 文档文件路径、Path对象、BytesIO对象或字节数据
            chat_id: 聊天ID，为空时使用默认值
            caption: 文档说明
            parse_mode: 解析模式
            reply_to_message_id: 回复的消息ID
            reply_markup: 内联键盘
            filename: 自定义文件名
            
        Returns:
            Message: 发送的消息对象
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        # 处理不同类型的文档输入
        if isinstance(document, (str, Path)):
            doc_path = Path(document)
            if not doc_path.exists():
                raise FileNotFoundError(f"文档文件不存在: {doc_path}")
            doc_input = InputFile(doc_path.open('rb'), 
                                 filename=filename or doc_path.name)
        elif isinstance(document, io.BytesIO):
            doc_input = InputFile(document, filename=filename or locale.type(6))
        elif isinstance(document, bytes):
            doc_input = InputFile(io.BytesIO(document), 
                                 filename=filename or locale.type(6))
        else:
            doc_input = document
        
        return await self._retry_operation(
            self.bot.send_document,
            chat_id=target_chat_id,
            document=doc_input,
            caption=caption,
            parse_mode=parse_mode,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup
        )

    async def send_video(self, chat_id: Optional[int] = None, video: Union[str, Path, io.BytesIO, bytes] = None, caption: str = "", 
                        parse_mode: str = ParseMode.HTML,
                        duration: Optional[int] = None,
                        width: Optional[int] = None,
                        height: Optional[int] = None,
                        reply_to_message_id: Optional[int] = None,
                        reply_markup: Optional[InlineKeyboardMarkup] = None,
                        filename: Optional[str] = None):
        """
        发送视频
        
        Args:
            video: 视频文件路径、Path对象、BytesIO对象或字节数据
            chat_id: 聊天ID，为空时使用默认值
            caption: 视频说明
            parse_mode: 解析模式
            duration: 视频时长（秒）
            width: 视频宽度
            height: 视频高度
            reply_to_message_id: 回复的消息ID
            reply_markup: 内联键盘
            filename: 自定义文件名
            
        Returns:
            Message: 发送的消息对象
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        # 处理不同类型的视频输入
        if isinstance(video, (str, Path)):
            video_path = Path(video)
            if not video_path.exists():
                raise FileNotFoundError(f"视频文件不存在: {video_path}")
            video_input = InputFile(video_path.open('rb'), 
                                   filename=filename or video_path.name)
        elif isinstance(video, io.BytesIO):
            video_input = InputFile(video, filename=filename or f"{locale.type(43)}.mp4")
        elif isinstance(video, bytes):
            video_input = InputFile(io.BytesIO(video), 
                                   filename=filename or f"{locale.type(43)}.mp4")
        else:
            video_input = video
        
        return await self._retry_operation(
            self.bot.send_video,
            chat_id=target_chat_id,
            video=video_input,
            caption=caption,
            parse_mode=parse_mode,
            duration=duration,
            width=width,
            height=height,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup
        )

    async def send_audio(self, chat_id: Optional[int] = None, audio: Union[str, Path, io.BytesIO, bytes] = None, caption: str = "", 
                        parse_mode: str = ParseMode.HTML,
                        duration: Optional[int] = None,
                        performer: Optional[str] = None,
                        title: Optional[str] = None,
                        reply_to_message_id: Optional[int] = None,
                        reply_markup: Optional[InlineKeyboardMarkup] = None,
                        filename: Optional[str] = None):
        """
        发送音频
        
        Args:
            audio: 音频文件路径、Path对象、BytesIO对象或字节数据
            chat_id: 聊天ID，为空时使用默认值
            caption: 音频说明
            parse_mode: 解析模式
            duration: 音频时长（秒）
            performer: 演唱者
            title: 音频标题
            reply_to_message_id: 回复的消息ID
            reply_markup: 内联键盘
            filename: 自定义文件名
            
        Returns:
            Message: 发送的消息对象
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        # 处理不同类型的音频输入
        if isinstance(audio, (str, Path)):
            audio_path = Path(audio)
            if not audio_path.exists():
                raise FileNotFoundError(f"音频文件不存在: {audio_path}")
            audio_input = InputFile(audio_path.open('rb'), 
                                   filename=filename or audio_path.name)
        elif isinstance(audio, io.BytesIO):
            audio_input = InputFile(audio, filename=filename or f"{locale.type(34)}.mp3")
        elif isinstance(audio, bytes):
            audio_input = InputFile(io.BytesIO(audio), 
                                   filename=filename or f"{locale.type(34)}.mp3")
        else:
            audio_input = audio
        
        return await self._retry_operation(
            self.bot.send_audio,
            chat_id=target_chat_id,
            audio=audio_input,
            caption=caption,
            parse_mode=parse_mode,
            duration=duration,
            performer=performer,
            title=title,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup
        )
    
    async def send_voice(self, chat_id: Optional[int] = None, 
                        voice: Union[str, Path, io.BytesIO, bytes] = None, 
                        caption: str = "", 
                        duration: Optional[int] = None,
                        parse_mode: str = ParseMode.HTML,
                        reply_to_message_id: Optional[int] = None,
                        reply_markup: Optional[InlineKeyboardMarkup] = None,
                        filename: Optional[str] = None):
        """
        发送语音消息
        
        Args:
            chat_id: 聊天ID，为空时使用默认值
            voice: 语音文件路径、Path对象、BytesIO对象或字节数据
            caption: 语音说明
            parse_mode: 解析模式
            duration: 语音时长（秒）
            reply_to_message_id: 回复的消息ID
            reply_markup: 内联键盘
            filename: 自定义文件名
            
        Returns:
            Message: 发送的消息对象
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        if voice is None:
            raise ValueError("必须提供 voice 参数")
        
        # 处理不同类型的语音输入
        if isinstance(voice, (str, Path)):
            voice_path = Path(voice)
            if not voice_path.exists():
                raise FileNotFoundError(f"语音文件不存在: {voice_path}")
            voice_input = InputFile(voice_path.open('rb'), 
                                   filename=filename or voice_path.name)
        elif isinstance(voice, io.BytesIO):
            voice_input = InputFile(voice, filename=filename or f"{locale.type(34)}.ogg")
        elif isinstance(voice, bytes):
            voice_input = InputFile(io.BytesIO(voice), 
                                   filename=filename or f"{locale.type(34)}.ogg")
        else:
            voice_input = voice
        
        return await self._retry_operation(
            self.bot.send_voice,
            chat_id=target_chat_id,
            voice=voice_input,
            caption=caption,
            parse_mode=parse_mode,
            duration=duration,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup
        )

    async def send_animation(self, chat_id: Optional[int] = None, animation: Union[str, Path, io.BytesIO, bytes] = None, caption: str = "",  
                           parse_mode: str = ParseMode.HTML,
                           duration: Optional[int] = None,
                           width: Optional[int] = None,
                           height: Optional[int] = None,
                           thumbnail: Optional[Union[str, Path, io.BytesIO, bytes]] = None,
                           reply_to_message_id: Optional[int] = None,
                           reply_markup: Optional[InlineKeyboardMarkup] = None,
                           filename: Optional[str] = None):
        """
        发送动画（GIF 或 H.264/MPEG-4 AVC 视频，无声音）
        
        Args:
            chat_id: 聊天ID，为空时使用默认值
            animation: 动画文件路径、Path对象、BytesIO对象或字节数据
            caption: 动画说明
            parse_mode: 解析模式
            duration: 动画时长（秒）
            width: 动画宽度
            height: 动画高度
            thumbnail: 缩略图文件（可选）
            reply_to_message_id: 回复的消息ID
            reply_markup: 内联键盘
            filename: 自定义文件名
            
        Returns:
            Message: 发送的消息对象
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        # 处理不同类型的动画输入
        if isinstance(animation, (str, Path)):
            animation_path = Path(animation)
            if not animation_path.exists():
                raise FileNotFoundError(f"动画文件不存在: {animation_path}")
            animation_input = InputFile(animation_path.open('rb'), 
                                       filename=filename or animation_path.name)
        elif isinstance(animation, io.BytesIO):
            animation_input = InputFile(animation, filename=filename or f"{locale.type(47)}.gif")
        elif isinstance(animation, bytes):
            animation_input = InputFile(io.BytesIO(animation), 
                                       filename=filename or f"{locale.type(47)}.gif")
        else:
            animation_input = animation
        
        # 处理缩略图（如果提供）
        thumb_input = None
        if thumbnail is not None:
            if isinstance(thumbnail, (str, Path)):
                thumb_path = Path(thumbnail)
                if thumb_path.exists():
                    thumb_input = InputFile(thumb_path.open('rb'), filename=thumb_path.name)
            elif isinstance(thumbnail, io.BytesIO):
                thumb_input = InputFile(thumbnail, filename="thumb.jpg")
            elif isinstance(thumbnail, bytes):
                thumb_input = InputFile(io.BytesIO(thumbnail), filename="thumb.jpg")
            else:
                thumb_input = thumbnail
        
        return await self._retry_operation(
            self.bot.send_animation,
            chat_id=target_chat_id,
            animation=animation_input,
            caption=caption,
            parse_mode=parse_mode,
            duration=duration,
            width=width,
            height=height,
            thumbnail=thumb_input,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup
        )
    
    async def send_location(self, chat_id: Optional[int] = None,
                        latitude: float = None,
                        longitude: float = None,
                        title: str = "",
                        address: str = "",
                        foursquare_id: Optional[str] = None,
                        foursquare_type: Optional[str] = None,
                        google_place_id: Optional[str] = None,
                        google_place_type: Optional[str] = None,
                        reply_to_message_id: Optional[int] = None,
                        reply_markup: Optional[InlineKeyboardMarkup] = None):
        """
        发送场所信息（包含标题和地址的位置）
        
        Args:
            chat_id: 聊天ID，为空时使用默认值
            latitude: 纬度（必需）
            longitude: 经度（必需）
            title: 场所标题（必需）
            address: 场所地址（必需）
            foursquare_id: Foursquare ID
            foursquare_type: Foursquare 类型
            google_place_id: Google Places ID
            google_place_type: Google Places 类型
            reply_to_message_id: 回复的消息ID
            reply_markup: 内联键盘
            
        Returns:
            Message: 发送的消息对象
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        if latitude is None or longitude is None:
            raise ValueError("必须提供 latitude 和 longitude 参数")
        
        if not title.strip():
            raise ValueError("必须提供 title 参数")
        
        if not address.strip():
            raise ValueError("必须提供 address 参数")
        
        # 验证纬度和经度范围
        if not (-90 <= latitude <= 90):
            raise ValueError("纬度必须在 -90 到 90 度之间")
        
        if not (-180 <= longitude <= 180):
            raise ValueError("经度必须在 -180 到 180 度之间")
        
        return await self._retry_operation(
            self.bot.send_venue,
            chat_id=target_chat_id,
            latitude=latitude,
            longitude=longitude,
            title=title,
            address=address,
            foursquare_id=foursquare_id,
            foursquare_type=foursquare_type,
            google_place_id=google_place_id,
            google_place_type=google_place_type,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup
        )

    async def send_realtime_location(self, chat_id: Optional[int] = None, 
                        latitude: float = None, 
                        longitude: float = None,
                        live_period: Optional[int] = None,
                        heading: Optional[int] = None,
                        proximity_alert_radius: Optional[int] = None,
                        reply_to_message_id: Optional[int] = None,
                        reply_markup: Optional[InlineKeyboardMarkup] = None):
        """
        发送地理位置
        
        Args:
            chat_id: 聊天ID，为空时使用默认值
            latitude: 纬度（必需）
            longitude: 经度（必需）
            live_period: 实时位置更新周期（秒），范围 60-86400
            heading: 移动方向（度），范围 1-360，仅适用于实时位置
            proximity_alert_radius: 接近警报半径（米），范围 1-100000
            reply_to_message_id: 回复的消息ID
            reply_markup: 内联键盘
            
        Returns:
            Message: 发送的消息对象
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        if latitude is None or longitude is None:
            raise ValueError("必须提供 latitude 和 longitude 参数")
        
        # 验证纬度和经度范围
        if not (-90 <= latitude <= 90):
            raise ValueError("纬度必须在 -90 到 90 度之间")
        
        if not (-180 <= longitude <= 180):
            raise ValueError("经度必须在 -180 到 180 度之间")
        
        # 验证实时位置参数
        if live_period is not None:
            if not (60 <= live_period <= 86400):
                raise ValueError("live_period 必须在 60 到 86400 秒之间")
        
        # 验证移动方向
        if heading is not None:
            if not (1 <= heading <= 360):
                raise ValueError("heading 必须在 1 到 360 度之间")
            if live_period is None:
                logger.warning("heading 参数仅在设置 live_period 时有效")
        
        # 验证接近警报半径
        if proximity_alert_radius is not None:
            if not (1 <= proximity_alert_radius <= 100000):
                raise ValueError("proximity_alert_radius 必须在 1 到 100000 米之间")
        
        return await self._retry_operation(
            self.bot.send_location,
            chat_id=target_chat_id,
            latitude=latitude,
            longitude=longitude,
            live_period=live_period,
            heading=heading,
            proximity_alert_radius=proximity_alert_radius,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup
        )

    async def edit_message_text(self, chat_id: Optional[int] = None, text: str = "",  
                               message_id: Optional[int] = None,
                               inline_message_id: Optional[str] = None,
                               parse_mode: str = ParseMode.HTML,
                               disable_web_page_preview: bool = False,
                               reply_markup: Optional[InlineKeyboardMarkup] = None):
        """
        编辑消息文本
        
        Args:
            text: 新的消息文本
            chat_id: 聊天ID
            message_id: 消息ID
            inline_message_id: 内联消息ID
            parse_mode: 解析模式
            disable_web_page_preview: 禁用网页预览
            reply_markup: 内联键盘
            
        Returns:
            Message: 编辑后的消息对象
        """
        if not text.strip():
            raise ValueError("消息文本不能为空")
        
        return await self._retry_operation(
            self.bot.edit_message_text,
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            inline_message_id=inline_message_id,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=reply_markup
        )

    async def delete_message(self, chat_id: Optional[int] = None, 
                            message_id: int = None):
        """
        删除消息
        
        Args:
            chat_id: 聊天ID，为空时使用默认值
            message_id: 消息ID
            
        Returns:
            bool: 删除是否成功
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        if message_id is None:
            raise ValueError("必须提供 message_id")
        
        return await self._retry_operation(
            self.bot.delete_message,
            chat_id=target_chat_id,
            message_id=message_id
        )

    async def get_chat(self, chat_id: Optional[int] = None):
        """
        获取聊天信息
        
        Args:
            chat_id: 聊天ID，为空时使用默认值
            
        Returns:
            Chat: 聊天对象
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        return await self._retry_operation(
            self.bot.get_chat,
            chat_id=target_chat_id
        )

    async def get_me(self):
        """
        获取机器人信息
        
        Returns:
            User: 机器人用户对象
        """
        return await self._retry_operation(self.bot.get_me)
    
    async def get_file(self, file_id: str):
        """
        获取文件信息
        
        Args:
            file_id: 文件ID
            
        Returns:
            File: 文件对象
        """
        return await self._retry_operation(
            self.bot.get_file,
            file_id=file_id
        )

    async def send_media_group(self, media: List[Union[Dict[str, Any], Any]], 
                              chat_id: Optional[int] = None,
                              reply_to_message_id: Optional[int] = None):
        """
        发送媒体组（相册）
        
        Args:
            media: 媒体列表
            chat_id: 聊天ID，为空时使用默认值
            reply_to_message_id: 回复的消息ID
            
        Returns:
            List[Message]: 发送的消息列表
        """
        target_chat_id = chat_id or self.default_chat_id
        if target_chat_id is None:
            raise ValueError("必须提供 chat_id 或设置默认 chat_id")
        
        if not media:
            raise ValueError("媒体列表不能为空")
        
        return await self._retry_operation(
            self.bot.send_media_group,
            chat_id=target_chat_id,
            media=media,
            reply_to_message_id=reply_to_message_id
        )

    def get_thread_info(self) -> Dict[str, Any]:
        """
        获取当前线程的信息（调试用）
        
        Returns:
            Dict: 线程信息
        """
        thread = threading.current_thread()
        has_bot = hasattr(self._local, 'bot') and self._local.bot is not None
        
        return {
            "thread_id": thread.ident,
            "thread_name": thread.name,
            "has_bot_instance": has_bot,
            "bot_id": id(self._local.bot) if has_bot else None
        }

    def __str__(self) -> str:
        """字符串表示"""
        thread_info = self.get_thread_info()
        return (f"TelegramSender(thread={thread_info['thread_name']}, "
               f"has_bot={thread_info['has_bot_instance']}, "
               f"default_chat_id={self.default_chat_id})")

    def __repr__(self) -> str:
        """详细字符串表示"""
        return self.__str__()

# 创建全局实例
telegram_sender = TelegramSender(config.BOT_TOKEN)
