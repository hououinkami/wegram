import asyncio
import base64
import logging
import os
import re
from datetime import datetime
from io import BytesIO
from typing import Optional

import aiohttp
from PIL import Image

logger = logging.getLogger(__name__)

async def get_image_from_url(url: str) -> Optional[BytesIO]:
    """从URL下载图片并处理为BytesIO对象"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                image_data = await response.read()
        
        return BytesIO(image_data)
        
    except Exception as e:
        logger.error(f"下载处理图片失败: {e}")
        return None

def parse_time_without_seconds(time_str):
    """解析时间并忽略秒数"""
    time_str = re.sub(r'(\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{1,2}):\d{1,2}', r'\1', time_str)
    
    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M")
    except ValueError:
        logger.warning(f"无法解析时间格式: {time_str}，使用当前时间")
        return datetime.now()

async def telegram_file_to_base64(file_id):
    """获取文件并转换为 Base64 格式"""
    try:
        from api.telegram_sender import telegram_sender
        
        # Step 1: 获取文件信息
        file = await telegram_sender.get_file(file_id)
        
        # Step 2: 下载文件到内存
        file_content = await file.download_as_bytearray()
        
        # Step 3: 转换为 Base64
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

async def process_avatar_from_url(url: str, min_size: int = 512) -> Optional[BytesIO]:
    """从URL下载图片并处理为头像格式"""
    try:
        image_bytesio = await get_image_from_url(url)
        if image_bytesio is None:
            return None
        
        loop = asyncio.get_event_loop()
        processed_image = await loop.run_in_executor(
            None,
            process_avatar_image,
            image_bytesio.getvalue(),
            min_size
        )
        
        return processed_image
        
    except Exception as e:
        logger.error(f"下载处理图片失败: {e}")
        return None

def process_avatar_image(image_data: bytes, min_size: int = 512) -> BytesIO:
    """处理头像图片内容"""
    try:
        img = Image.open(BytesIO(image_data))
        
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        width, height = img.size
        if width < min_size or height < min_size:
            ratio = max(min_size / width, min_size / height)
            new_width = int(width * ratio)
            new_height = int(height * ratio)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        if img.width != img.height:
            size = min(img.size)
            left = (img.width - size) // 2
            top = (img.height - size) // 2
            img = img.crop((left, top, left + size, top + size))
        
        output = BytesIO()
        img.save(output, format='JPEG', quality=95)
        output.seek(0)
        return output
        
    except Exception as e:
        logger.error(f"图片处理失败: {e}")
        try:
            img = Image.open(BytesIO(image_data))
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            output = BytesIO()
            img.save(output, format='JPEG', quality=95)
            output.seek(0)
            return output
        except Exception:
            return BytesIO(image_data)

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
            if value != {} and value is not None:
                return value
        else:
            value = data.get(key)
            if value is not None:
                return value
    return default
