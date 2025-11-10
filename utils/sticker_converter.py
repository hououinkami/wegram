import gzip
import json
import logging
import os
import tempfile
from io import BytesIO
from typing import Optional, Union, Dict, Any


import ffmpeg
from lottie import objects, parsers
from lottie.exporters import gif
from PIL import Image, ImageSequence

# 假设你有这些常量定义
class WxLimitConstants:
    MAX_GIF_SIZE = 1024 * 1024  # 1MB
    IS_ZIP = True

logger = logging.getLogger(__name__)

class ConverterHelper:
    def __init__(self):
        """初始化转换器"""
        # Python 的 ffmpeg-python 不需要设置路径，直接使用系统的 ffmpeg
        pass
    
    def _generate_output_filename(self, input_file: Union[str, bytes], default_name: str = "output") -> str:
        """
        根据输入文件生成默认的 GIF 输出文件名
        
        Args:
            input_file: 输入文件路径或字节数据
            default_name: 当输入为字节数据时的默认文件名
            
        Returns:
            生成的 GIF 文件路径
        """
        if isinstance(input_file, str):
            # 获取文件名（不含路径）并替换后缀为 .gif
            base_name = os.path.splitext(os.path.basename(input_file))[0]
            directory = os.path.dirname(input_file)
            if directory:
                return os.path.join(directory, f"{base_name}.gif")
            else:
                return f"{base_name}.gif"
        else:
            # 字节数据情况下使用默认名称
            return f"{default_name}.gif"
    
    async def webp_to_gif(self, input_file: Union[str, bytes], output_file: Optional[str] = None) -> str:
        """
        将 WebP 转换为 GIF（使用 FFmpeg，完美保留透明）
        
        Args:
            input_file: 输入文件路径或字节数据
            output_file: 输出文件路径
            
        Returns:
            生成的 GIF 文件路径
        """
        try:
            if output_file is None:
                output_file = self._generate_output_filename(input_file, "webp_converted")
    
            if isinstance(input_file, bytes):
                # 如果是字节数据，先保存为临时文件
                with tempfile.NamedTemporaryFile(suffix='.webp', delete=False) as temp_file:
                    temp_file.write(input_file)
                    temp_input = temp_file.name
            else:
                temp_input = input_file
            
            # 🚀 使用 FFmpeg 转换（自动保留透明）
            try:
                (
                    ffmpeg
                    .input(temp_input)
                    .output(
                        output_file,
                        vf='split[s0][s1];[s0]palettegen=reserve_transparent=1[p];[s1][p]paletteuse=alpha_threshold=128',
                        f='gif'
                    )
                    .overwrite_output()
                    .run(quiet=True, capture_stderr=True)
                )
                
                logger.info('WebP to GIF conversion finished (FFmpeg)')
                
            except ffmpeg.Error as e:
                # FFmpeg 失败，回退到 PIL
                logger.warning(f'FFmpeg conversion failed, trying PIL: {e}')
                return await self._webp_to_gif_by_pil(temp_input, output_file)
            
            # 清理临时文件
            if isinstance(input_file, bytes) and os.path.exists(temp_input):
                os.unlink(temp_input)
            
            return output_file
            
        except Exception as err:
            logger.error(f'Error during WebP to GIF conversion: {err}')
            raise err
    
    async def _webp_to_gif_by_pil(self, input_file: str, output_file: str) -> str:
        """PIL 备用方法"""
        with Image.open(input_file) as img:
            if getattr(img, 'is_animated', False):
                frames = []
                durations = []
                
                for frame_idx in range(img.n_frames):
                    img.seek(frame_idx)
                    frame = img.copy().convert('RGBA')
                    frames.append(frame)
                    durations.append(img.info.get('duration', 100))
                
                if frames:
                    frames[0].save(
                        output_file,
                        'GIF',
                        save_all=True,
                        append_images=frames[1:],
                        duration=durations,
                        loop=0,
                        optimize=True,
                        transparency=0,
                        disposal=2
                    )
            else:
                img.convert('RGBA').save(
                    output_file,
                    'GIF',
                    transparency=0,
                    optimize=True
                )
        
        return output_file
    
    async def webm_to_gif(self, input_file: Union[str, bytes], output_file: Optional[str] = None) -> str:
        """
        将 WebM 转换为 GIF
        
        Args:
            input_file: 输入文件路径或字节数据
            output_file: 输出文件路径
            
        Returns:
            生成的 GIF 文件路径
        """
        try:
            if output_file is None:
                output_file = self._generate_output_filename(input_file, "webm_converted")

            if isinstance(input_file, bytes):
                # 如果是字节数据，先保存为临时文件
                with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as temp_file:
                    temp_file.write(input_file)
                    temp_input = temp_file.name
            else:
                temp_input = input_file
            
            def convert_with_params(resolution: int, fps: int) -> bool:
                """递归转换函数"""
                try:
                    # 构建 scale 参数
                    if resolution < 410:
                        scale_filter = f'scale={resolution}:-1:flags=lanczos'
                    else:
                        scale_filter = 'scale=iw:-1:flags=lanczos'
                    
                    # 使用 ffmpeg-python 进行转换
                    (
                        ffmpeg
                        .input(temp_input)
                        .output(
                            output_file,
                            vf=f'fps={fps},{scale_filter}',
                            f='gif'
                        )
                        .overwrite_output()
                        .run(quiet=True)
                    )
                    
                    logger.info('WebM to GIF conversion finished successfully')
                    
                    # 检查文件大小
                    if os.path.exists(output_file) and WxLimitConstants.IS_ZIP:
                        file_size = os.path.getsize(output_file)
                        
                        if file_size > WxLimitConstants.MAX_GIF_SIZE:
                            logger.info(f'文件大小 {file_size} 超过 1MB，重新调整参数')
                            if resolution > 100 and fps > 1:
                                # 递归调用，降低分辨率和帧率
                                return convert_with_params(resolution - 50, fps - 1)
                            else:
                                raise Exception('无法将文件压缩到 1MB 以下')
                        else:
                            logger.info(f'文件大小 {file_size} 满足要求')
                            return True
                    else:
                        return True
                    
                    return False
                    
                except ffmpeg.Error as e:
                    logger.info(f'FFmpeg error: {e}')
                    raise e
                except Exception as e:
                    logger.info(f'Conversion error: {e}')
                    raise e
            
            # 初始参数
            initial_resolution = 360 + 50  # 410
            initial_fps = 16 + 1  # 17
            
            # 开始转换
            success = convert_with_params(initial_resolution, initial_fps)
            
            # 清理临时文件
            if isinstance(input_file, bytes) and os.path.exists(temp_input):
                os.unlink(temp_input)
            
            if not success:
                raise Exception('WebM to GIF conversion failed')
            
            return output_file
                
        except Exception as err:
            logger.info(f'Error during WebM to GIF conversion: {err}')
            raise err
    
    async def tgs_to_gif(self, input_file: Union[str, bytes], output_file: Optional[str] = None, 
                        lottie_config: Optional[Dict[str, int]] = None) -> str:
        """
        将 TGS (Telegram 贴纸) 转换为 GIF
        
        Args:
            input_file: 输入文件路径或字节数据
            output_file: 输出文件路径
            lottie_config: Lottie 配置参数
            
        Returns:
            生成的 GIF 文件路径
        """
        try:
            if output_file is None:
                output_file = self._generate_output_filename(input_file, "tgs_converted")

            # 默认配置
            default_config = {
                'width': 512,
                'height': 512,
                'fps': 30
            }
            
            if lottie_config:
                default_config.update(lottie_config)

            # 处理输入文件
            if isinstance(input_file, bytes):
                # 如果是字节数据，先保存为临时文件
                with tempfile.NamedTemporaryFile(suffix='.tgs', delete=False) as temp_file:
                    temp_file.write(input_file)
                    temp_input = temp_file.name
            else:
                temp_input = input_file

            # 使用 lottie-python（推荐）
            success = await self._convert_tgs_with_lottie(temp_input, output_file, default_config)
            
            # 清理临时文件
            if isinstance(input_file, bytes) and os.path.exists(temp_input):
                os.unlink(temp_input)

            if not success:
                raise Exception('All TGS conversion methods failed')

            logger.info(f'TGS to GIF conversion finished! Output: {output_file}')
            return output_file
            
        except Exception as err:
            logger.error(f'Error during TGS to GIF conversion: {err}')
            raise err

    async def _convert_tgs_with_lottie(self, input_file: str, output_file: str, config: Dict[str, int]) -> bool:
        """使用 lottie-python 转换（智能背景处理）"""
        try:            
            # 解压 TGS 文件
            with gzip.open(input_file, 'rt') as f:
                lottie_data = json.load(f)
            
            # 解析 Lottie 动画
            animation = parsers.tgs.parse_tgs(lottie_data)
            
            # 导出到临时文件
            temp_output = output_file + '.tmp.gif'
            
            # 🆕 尝试使用 bg_color 参数（如果支持）
            try:
                gif.export_gif(
                    animation, 
                    temp_output,
                    width=config['width'],
                    height=config['height'],
                    fps=config['fps'],
                    bg_color=(255, 255, 255, 0)  # 尝试透明背景
                )
            except TypeError:
                # 不支持 bg_color，使用默认
                gif.export_gif(
                    animation, 
                    temp_output,
                    width=config['width'],
                    height=config['height'],
                    fps=config['fps']
                )
            
            # 🆕 后处理：替换黑色背景为透明
            with Image.open(temp_output) as img:
                frames = []
                durations = []
                
                for frame in ImageSequence.Iterator(img):
                    # 转换为 RGBA
                    frame = frame.convert('RGBA')
                    
                    # 获取像素数据
                    pixels = frame.load()
                    width, height = frame.size
                    
                    # 替换黑色为透明（优化版）
                    for y in range(height):
                        for x in range(width):
                            r, g, b, a = pixels[x, y]
                            # 如果是接近黑色的像素（容差 10）
                            if r < 10 and g < 10 and b < 10:
                                pixels[x, y] = (0, 0, 0, 0)  # 设为透明
                    
                    frames.append(frame)
                    
                    # 获取帧持续时间
                    duration = frame.info.get('duration', int(1000 / config['fps']))
                    durations.append(duration)
                
                # 保存为 GIF（透明背景）
                if frames:
                    frames[0].save(
                        output_file,
                        'GIF',
                        save_all=True,
                        append_images=frames[1:],
                        duration=durations,
                        loop=0,
                        optimize=True,
                        transparency=0,
                        disposal=2
                    )
            
            # 删除临时文件
            if os.path.exists(temp_output):
                os.unlink(temp_output)
            
            logger.info('TGS converted with transparent background')
            return True
            
        except ImportError:
            logger.warning('lottie-python not installed')
            return False
        except Exception as e:
            logger.error(f'lottie-python conversion failed: {e}')
            return False

    async def gif_to_webm(self, input_file: Union[str, bytes, BytesIO], output_file: Optional[str] = None) -> str:
        """
        将 GIF 转换为 WebM (Telegram 视频贴纸格式)
        """
        pass

    def extract_thumbnail(self, video_path: str, output_image: str, time: str = '00:00:01') -> str:
        """
        从视频中提取缩略图
        
        Args:
            video_path: 视频文件路径
            output_image: 输出图片文件名
            time: 提取时间点
            
        Returns:
            输出文件路径
        """
        try:
            (
                ffmpeg
                .input(video_path, ss=time)
                .output(
                    output_image,
                    vframes=1,
                    vf='scale=320:-1'  # 限制宽度，高度自适应
                )
                .overwrite_output()
                .run(quiet=True)
            )
            
            logger.info(f'Thumbnail extracted: {output_image}')
            return output_image
            
        except ffmpeg.Error as e:
            logger.info(f'Error extracting thumbnail: {e}')
            raise e
    
    def get_video_duration(self, video_path: str) -> int:
        """
        获取视频时长
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            视频时长（秒）
        """
        try:
            probe = ffmpeg.probe(video_path)
            duration = float(probe['format']['duration'])
            return int(duration)  # 返回整数秒
            
        except ffmpeg.Error as e:
            logger.info(f'Error getting video duration: {e}')
            raise e
        except KeyError as e:
            logger.info(f'Duration information not found: {e}')
            raise e

converter = ConverterHelper()