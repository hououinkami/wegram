import asyncio
import base64
import logging
import os
import re
import tempfile
import warnings
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional, Union, BinaryIO

import aiohttp
import whisper
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
