import asyncio
import logging
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Callable, Any, Union, Optional

from PIL import Image

import config
from api.telegram_sender import telegram_sender
from utils.sticker_converter import converter

logger = logging.getLogger(__name__)

class AsyncFileProcessor:
    def __init__(self, telegram_sender):
        self.telegram_sender = telegram_sender
    
    def _create_placeholder_file(self, file_name: str) -> BytesIO:
        """创建1字节的占位符文件，文件名为file_name"""
        placeholder = BytesIO(b'\x00')  # 1字节的空数据
        placeholder.name = file_name
        placeholder.seek(0)
        return placeholder
    
    async def send_with_placeholder(self, file_type: str, file_name: str,
                                    chat_id: int, sender_name: str, reply_to_message_id: int,
                                    download_func, *download_args, **download_kwargs) -> dict:
        """
        先发送占位符，然后异步下载并更新真实文件
        """
        # 1. 先发送1B临时文件，文件名为file_name
        placeholder_caption = f"{sender_name}"
        placeholder_file = self._create_placeholder_file(file_name)
        
        # 统一发送为document，文件名为file_name
        response = await self.telegram_sender.send_document(
            chat_id, placeholder_file, placeholder_caption,
            reply_to_message_id, 
            filename=file_name
        )
        
        # 2. 异步下载并更新
        if response:
            message_id = response.message_id
            # 创建异步任务来处理文件下载和更新
            asyncio.create_task(
                self._download_and_update(
                    file_type,
                    chat_id, message_id, sender_name,
                    download_func, download_args, download_kwargs
                )
            )
        
        return response
    
    async def _download_and_update(self, file_type: str, 
        chat_id: int, message_id: int, sender_name: str, 
        download_func, args, kwargs):
        """异步下载文件并更新消息"""
        try:
            # 执行下载
            result = await download_func(*args, **kwargs)

            if len(result) == 3:
                success, file_data, filename = result
            elif len(result) == 2:
                file_data, filename = result
                success = file_data is not None
            else:
                success, file_data, filename = False, None, "未知错误"
            
            if success:
                if file_type == 'sticker':

                    match = re.search(r'<blockquote[^>]*>(.*?)</blockquote>', sender_name, re.DOTALL)
                    sender_name_text = match.group(1) if match else sender_name

                    webm_path = await converter.image_to_webp(file_data)
                    # webm_path = await converter.gif_to_webm("/app/download/sticker/000.gif")

                    # 贴纸特殊处理
                    await self.replace_message_with_sticker(
                        telegram_sender=self.telegram_sender,
                        chat_id=chat_id,
                        message_id=message_id,
                        sticker_data=webm_path,
                        original_caption=sender_name_text,
                        filename=filename
                    )
                else:
                    if file_type == 'photo':
                        file_type = await self.image_send_mode(file_data)
                    
                    # 使用edit_message_media方法，只替换媒体内容，不修改caption
                    await self.telegram_sender.edit_message_media(
                        chat_id=chat_id,
                        message_id=message_id,
                        media=file_data,
                        media_type=file_type,
                        filename=filename,
                        caption=sender_name
                    )
                
            else:
                if filename != "企微图片":
                    # 下载失败，更新为错误消息
                    logger.warning(f"⚠️ 文件下载失败")
                
        except Exception as e:
            logger.error(f"❌ 异步下载或更新过程中出错: {e}", exc_info=True)

    async def image_send_mode(self, file_data) -> str:
        """分析图片特征决定发送方式"""
        try:
            # 如果是BytesIO，需要特殊处理
            if hasattr(file_data, 'read'):
                file_data.seek(0)  # 重置指针
                img = Image.open(file_data)
                file_data.seek(0)  # 重置指针供后续使用
            else:
                # 如果是文件路径
                img = Image.open(file_data)
                
            width, height = img.size
            file_size = 0
            
            # 获取文件大小
            if hasattr(file_data, 'getvalue'):
                # BytesIO
                file_size = len(file_data.getvalue()) / (1024 * 1024)  # MB
            elif isinstance(file_data, str) and os.path.exists(file_data):
                # 文件路径
                file_size = os.path.getsize(file_data) / (1024 * 1024)  # MB
            
            # 判断条件
            ratio = max(width/height, height/width)
            max_dimension = max(width, height)
            
            # 决定发送方式的条件
            should_use_document = (
                ratio > float(config.MAX_RATIO) or              # 长宽比过大
                file_size > float(config.MAX_SIZE) or            # 文件大于3MB
                max_dimension > 9000 or     # 单边过大
                width + height > 10000       # 总尺寸过大
            )
            
            return 'document' if should_use_document else 'photo'
            
        except Exception as e:
            logger.warning(f"图片分析失败: {e}, 默认使用photo模式")
            return 'photo'
    
    async def replace_message_with_sticker(self, telegram_sender, chat_id: int, message_id: int, 
                                        sticker_data: Union[BytesIO, bytes, str, Path], 
                                        original_caption: str,
                                        reply_to_message_id: Optional[int] = None,
                                        filename: Optional[str] = None) -> Optional[Any]:
        """
        删除原有消息并用贴纸替换，内联键盘显示原消息的caption
        
        Args:
            telegram_sender: TelegramSender实例
            chat_id: 聊天ID
            message_id: 要删除的原消息ID
            sticker_data: 贴纸数据（BytesIO、bytes、文件路径或文件ID）
            original_caption: 原消息的caption（将显示在内联键盘按钮中）
            reply_to_message_id: 回复的消息ID（可选）
            filename: 贴纸文件名（可选）
            
        Returns:
            Message: 发送的贴纸消息对象，如果失败返回None
        """
        try:
            # 1. 先发送贴纸（带有显示原caption的内联键盘）
            sticker_message = await telegram_sender.send_sticker(
                chat_id=chat_id,
                sticker=sticker_data,
                emoji="🫥",  # 默认贴纸表情
                reply_to_message_id=reply_to_message_id,
                filename=filename,
                # 使用新的内联键盘功能显示原caption
                title=original_caption
            )
            
            # 2. 发送成功后删除原消息
            if sticker_message:
                try:
                    await telegram_sender.delete_message(
                        chat_id=chat_id,
                        message_id=message_id
                    )
                    logger.info(f"✅ 成功替换消息 {message_id} 为贴纸 {sticker_message.message_id}")
                except Exception as delete_error:
                    logger.warning(f"⚠️ 贴纸发送成功但删除原消息失败: {delete_error}")
                    # 即使删除失败，也返回贴纸消息（因为贴纸发送成功了）
            
            return sticker_message
            
        except Exception as e:
            logger.error(f"❌ 替换消息为贴纸时出错: {e}", exc_info=True)
            return None

# 全局实例
async_file_processor = AsyncFileProcessor(telegram_sender)
