import asyncio
import base64
import logging
import os
import re
import requests
import tempfile
import time
import warnings
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional, Union, BinaryIO

import aiohttp
import aiofiles
import whisper
from PIL import Image

from service.telethon_client import get_client
from utils.message_formatter import escape_html_chars

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

async def telegram_file_to_base64(video_obj=None,
                                chat_id=None, 
                                message_id=None,
                                size_threshold_mb: int = 20,
                                force_method: Optional[str] = None):
    """
    获取文件并转换为 Base64 格式
    
    Args:
        video_obj: API的video对象（用于API下载）
        chat_id: 聊天ID（用于Telethon下载）
        message_id: 消息ID（用于Telethon下载）
        size_threshold_mb: 文件大小阈值(MB)，超过此大小使用telethon下载
        force_method: 强制使用的方法 ('api' 或 'telethon')
    
    Returns:
        str: Base64编码的文件内容，失败返回False
    """
    try:        
        # 参数验证
        if not video_obj and not (chat_id and message_id):
            raise ValueError("必须提供 video_obj 或者 (chat_id + message_id)")
        
        # 如果强制指定方法
        if force_method == 'api':
            if not video_obj:
                raise ValueError("使用API方法必须提供video_obj")
            return await _download_via_api(video_obj)
        elif force_method == 'telethon':
            if not (chat_id and message_id):
                raise ValueError("使用Telethon方法必须提供chat_id和message_id")
            return await _download_via_telethon(chat_id, message_id)
        
        # 智能选择逻辑
        if video_obj:
            try:
                # 从video对象获取文件大小
                file_size = getattr(video_obj, 'file_size', 0)
                file_size_mb = file_size / (1024 * 1024)
                
                # 根据文件大小选择下载方式
                if file_size_mb < size_threshold_mb:
                    logger.info(f"🚀 使用Bot API下载 (< {size_threshold_mb}MB)")
                    try:
                        return await _download_via_api(video_obj)
                    except Exception as api_error:
                        logger.warning(f"⚠️ Bot API下载失败: {api_error}")
                        if chat_id and message_id:
                            return await _download_via_telethon(chat_id, message_id)
                        else:
                            raise api_error
                else:
                    logger.info(f"🔄 使用Telethon下载 (≥ {size_threshold_mb}MB)")
                    if chat_id and message_id:
                        return await _download_via_telethon(chat_id, message_id)
                    else:
                        return await _download_via_api(video_obj)
                        
            except Exception as e:
                logger.warning(f"⚠️ 处理video_obj失败: {e}")
                if chat_id and message_id:
                    return await _download_via_telethon(chat_id, message_id)
                else:
                    raise e
        else:
            # 只有Telethon参数
            logger.info("🔄 使用Telethon下载")
            return await _download_via_telethon(chat_id, message_id)
            
    except Exception as e:
        logger.error(f"❌ 获取文件并转换为Base64失败: {e}")
        return False

async def _download_via_api(video_obj):
    """通过API下载文件"""
    from api.telegram_sender import telegram_sender
    
    start_time = time.time()
    
    # 获取文件（使用video对象的file_id）
    file = await telegram_sender.get_file(video_obj.file_id)
    
    # 下载文件到内存
    file_content = await file.download_as_bytearray()
    
    # 转换为Base64
    file_base64 = base64.b64encode(file_content).decode('utf-8')
    
    download_time = time.time() - start_time
    file_size_mb = len(file_content) / (1024 * 1024)
    logger.info(f"✅ Bot API下载完成，大小: {file_size_mb:.2f}MB，耗时: {download_time:.2f}s")
    
    return file_base64

async def _download_via_telethon(chat_id, message_id):
    """通过Telethon下载文件"""   
    start_time = time.time()
    
    client = get_client()
    
    # 获取消息
    message = await client.get_messages(chat_id, ids=message_id)
    if not message or not message.media:
        raise ValueError(f"消息 {message_id} 不存在或不包含媒体文件")
    
    # 下载文件到内存
    file_content = await client.download_media(message, file=bytes)
    
    if not file_content:
        raise RuntimeError("Telethon下载失败，文件内容为空")
    
    # 转换为Base64
    file_base64 = base64.b64encode(file_content).decode('utf-8')
    
    download_time = time.time() - start_time
    file_size_mb = len(file_content) / (1024 * 1024)
    logger.info(f"✅ Telethon下载完成，大小: {file_size_mb:.2f}MB，耗时: {download_time:.2f}s")
    
    return file_base64

async def telegram_file_to_base64_by_file_id(file_id):
    """通过file_id获取文件并转换为 Base64 格式"""
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

async def local_file_to_bytesio(file_path: str) -> BytesIO | None:
    """将本地文件转换为BytesIO"""
    try:
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return None
            
        async with aiofiles.open(file_path, 'rb') as f:
            data = await f.read()
            file_buffer = BytesIO(data)
            file_buffer.seek(0)
            return file_buffer
        
    except Exception as e:
        logger.error(f"转换文件为BytesIO失败 {file_path}: {e}")
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

# 全局模型缓存
_model_cache = {}

def _get_model(model_size="base", model_dir=None):
    """获取或加载模型（M2 优化版本）"""
    cache_key = f"{model_size}_{model_dir}"
    
    if cache_key not in _model_cache:
        logger.info(f"🤖 正在加载 Whisper 模型: {model_size}")
        
        # 加载模型并忽略警告
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="FP16 is not supported on CPU")
            warnings.filterwarnings("ignore", category=UserWarning)
            
            model = whisper.load_model(model_size, download_root=model_dir)
            
            # 移动到最佳设备
            model = model.to("cpu")
        
        _model_cache[cache_key] = model
        logger.info(f"✅ 模型加载完成")
    
    return _model_cache[cache_key]

def get_60s(format_type="text"):
    """获取API内容并格式化为指定格式
    
    Args:
        url (str): API地址
        format_type (str): 输出格式类型
            - "text": 普通文本格式（默认）
            - "html": HTML blockquote格式
            - "both": 返回两种格式的字典
    
    Returns:
        str or dict: 根据format_type返回相应格式的内容
    """
    url="https://60s-api.viki.moe/v2/60s"

    try:       
        # 发送GET请求
        response = requests.get(url, timeout=10)
        
        # 检查响应状态码
        if response.status_code == 200:
            # 获取JSON数据
            data = response.json()
            
            if 'data' in data:
                news_data = data['data']
                date = news_data.get('date', 'N/A')
                news_list = news_data.get('news', [])
                
                # 构建普通文本格式
                text_format = "📰 每天60秒读懂世界\n"
                text_format += f"日期：{date}\n"
                
                # 构建HTML格式
                html_format = "<blockquote>📰 每天60秒读懂世界</blockquote>\n"
                html_format += f"<blockquote>日期：{date}</blockquote>\n"
                
                # 圈数字符号列表
                circle_numbers = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩', 
                                '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳']
                
                # 添加编号的新闻条目
                for i, news in enumerate(news_list):
                    if i < len(circle_numbers):  # 确保不超出圈数字符号范围
                        # 普通文本格式
                        text_format += f"{circle_numbers[i]}{news}\n"
                        # HTML格式
                        html_format += f"<blockquote>{circle_numbers[i]}{escape_html_chars(news)}</blockquote>\n"
                    else:
                        # 如果超出20条，使用普通数字
                        text_format += f"{i+1}. {news}\n"
                        html_format += f"<blockquote>{i+1}. {escape_html_chars(news)}</blockquote>\n"
                
                # 根据format_type返回相应格式
                if format_type == "text":
                    return {
                        "date": date,
                        "text": text_format.strip()  # 去掉最后的换行符
                    }
                elif format_type == "html":
                    return {
                        "date": date,
                        "html": html_format.strip()  # 去掉最后的换行符
                    }
                elif format_type == "both":
                    return {
                        "date": date,
                        "text": text_format.strip(),
                        "html": html_format.strip()
                    }
                else:
                    logger.warning(f"未知的格式类型: {format_type}，使用默认文本格式")
                    return text_format.strip()
                    
            else:
                logger.error("❌ API响应中没有找到data字段")
                return None
                
        else:
            logger.error(f"❌ 请求失败，状态码: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"❌ 错误: {e}")
        return None

async def voice_to_text(voice_input: Union[str, BytesIO], language="zh"):
    """
    异步转换语音成文字 - M2 优化版本
    """
    
    # 输入类型验证
    if not isinstance(voice_input, (str, BytesIO)):
        raise ValueError(f"❌ 不支持的输入类型: {type(voice_input)}")
    
    # 处理不同类型的输入
    if isinstance(voice_input, str):
        if not Path(voice_input).exists():
            raise FileNotFoundError(f"❌ 语音文件不存在: {voice_input}")
    elif isinstance(voice_input, BytesIO):
        audio_data = voice_input.getvalue()
        if len(audio_data) == 0:
            raise ValueError("❌ BytesIO 对象为空")
    
    # 设置模型目录
    model_dir = os.path.join(os.path.dirname(__file__), "..", "whisper_model")
    model_dir = os.path.abspath(model_dir)
    os.makedirs(model_dir, exist_ok=True)
    
    def _transcribe_sync():
        """同步转换函数"""
        temp_file = None
        try:
            # 处理输入
            if isinstance(voice_input, str):
                audio_path = voice_input
            elif isinstance(voice_input, BytesIO):
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.ogg')
                audio_data = voice_input.getvalue()
                temp_file.write(audio_data)
                temp_file.close()
                audio_path = temp_file.name
            
            # 获取优化后的模型
            model = _get_model("small", model_dir)
            
            # M2 优化的转录参数
            result = model.transcribe(
                audio_path,  # 使用文件路径
                language=language,
                # initial_prompt="这是微信语音消息，日常对话，请用简体中文转录，若包含英文单词，则英文单词保持原样：",
                temperature=0.0,                                  # 确定性输出
                best_of=1,                                       # 快速处理
                beam_size=1,                                     # 贪婪搜索
                condition_on_previous_text=False,                # 独立处理
                task="transcribe",
                no_speech_threshold=0.6,                         # 适应微信音质
                logprob_threshold=-1.0,                          # 宽松置信度
                compression_ratio_threshold=2.4,                  # 适应压缩格式
                # M2 优化：使用更高效的参数
                fp16=False,  # M2 上 FP16 可能不稳定，使用 FP32
            )
            
            text = result["text"].strip()
            
            return text
            
        except Exception as e:
            logger.error(f"❌ 转换错误: {str(e)}")
            raise e
            
        finally:
            # 清理临时文件
            if temp_file and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                except Exception as e:
                    logger.warning(f"⚠️ 清理临时文件失败: {e}")
    
    # 异步执行
    try:
        text = await asyncio.to_thread(_transcribe_sync)
        return text
    except Exception as e:
        logger.error(f"异步转换失败: {str(e)}")
        raise e
