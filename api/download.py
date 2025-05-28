import math
import requests
import os
import base64
import json
import logging
from typing import Tuple
import config
from api.base import wechat_api

# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

# 常量定义
WXID = config.MY_WXID
IMAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "image")
VIDEO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "video")
EMOJI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sticker")
FILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "file")

def get_image(msg_id: str, from_wxid: str, data_json) -> Tuple[bool, str]:
    return chunked_download(
        api_path="/Tools/DownloadImg",
        msg_id=msg_id,
        from_wxid=from_wxid,
        data_json=data_json,
        file_key="img",
        file_extension="png",
        save_dir=IMAGE_DIR
    )

def get_video(msg_id: str, from_wxid: str, data_json) -> Tuple[bool, str]:
    return chunked_download(
        api_path="/Tools/DownloadVideo",
        msg_id=msg_id,
        from_wxid=from_wxid,
        data_json=data_json,
        file_key="videomsg",
        file_extension="mp4",
        save_dir=VIDEO_DIR
    )

def get_file(msg_id: str, from_wxid: str, data_json) -> Tuple[bool, str]:
    return chunked_download(
        api_path="/Tools/DownloadFile",
        msg_id=msg_id,
        from_wxid=from_wxid,
        data_json=data_json,
        file_key="appmsg",
        file_extension="",
        save_dir=FILE_DIR
    )
    
def get_emoji(data_json) -> Tuple[bool, str]:
    try:
        md5 = data_json["msg"]["emoji"]["md5"]
        data_length = int(data_json["msg"]["emoji"]["len"])
        url = data_json["msg"]["emoji"]["cdnurl"]

        # 文件名和路径
        filename = f"{md5}.gif"
        filepath = os.path.join(EMOJI_DIR, filename)

        # 检查文件是否已存在
        if os.path.exists(filepath):
            logger.info(f"文件已存在，跳过下载: {filepath}")
            return True, filepath
        
        # 利用API请求下载
        def get_url_by_api():
            # 构建请求参数
            payload = {
                "Md5": md5,
                "Wxid": WXID
            }

            # 发送请求
            response_data = wechat_api("/Tools/EmojiDownload", payload)
            
            # 检查响应数据结构
            if (response_data and "Data" in response_data):
                # 检查是否有直接的url
                if "url" in response_data["Data"]:
                    url = response_data["Data"]["url"]
                # 检查是否有emojiList结构
                elif "emojiList" in response_data["Data"] and response_data["Data"]["emojiList"] and len(response_data["Data"]["emojiList"]) > 0:
                    if "url" in response_data["Data"]["emojiList"][0]:
                        url = response_data["Data"]["emojiList"][0]["url"]
                        return url
                    else:
                        logger.error("emojiList中找不到url字段")
                        return False, "emojiList中找不到url字段"
                else:
                    logger.error("响应数据中找不到url")
                    return False, "响应数据中找不到url"
            else:
                logger.error("响应数据格式不正确")
                return False, "响应数据格式不正确"
        
        if url == "":
            url = get_url_by_api()
        
        # 确保目录存在
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        # 下载文件
        file_response = requests.get(url, stream=True)
        if file_response.status_code == 200:
            with open(filepath, 'wb') as f:
                for chunk in file_response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"表情包已下载到: {filepath}")
            return True, filepath
        else:
            error_msg = f"下载URL失败，HTTP状态码: {file_response.status_code}"
            logger.error(error_msg)
            return False, error_msg
                
    except Exception as e:
        logger.exception(f"下载失败: {str(e)}")
        return False, f"下载失败: {str(e)}"

def chunked_download(api_path: str, msg_id: str, from_wxid: str, data_json: dict, file_key: str, file_extension: str, save_dir: str) -> Tuple[bool, str]:
    try:
        # 提取文件信息
        file_info = data_json["msg"][file_key]
        md5 = file_info["md5"]
        data_length = int(file_info.get("length") or file_info.get("appattach").get("totallen"))
        file_title = (file_info.get("title") or "")
        
        # 文件名和路径
        if not file_title:
            filename = f"{md5}.{file_extension}"
        else:
            filename = f"{file_title}"
        filepath = os.path.join(save_dir, filename)
        
        # 检查文件是否已存在
        if os.path.exists(filepath):
            logger.info(f"文件已存在，跳过下载: {filepath}")
            return True, filepath
        
        # 确保保存目录存在
        os.makedirs(save_dir, exist_ok=True)

        # 用于存储所有分段的二进制数据
        all_binary_data = bytearray()
        cdn_success = False

        # 优先使用cdn下载（仅对图片）
        if file_key == "img":
            try:
                aeskey = data_json["msg"]["img"]["aeskey"]
                # 按优先级顺序获取第一个非空的CDN URL
                cdnurl = (data_json["msg"]["img"].get("cdnbigimgurl") or 
                        data_json["msg"]["img"].get("cdnmidimgurl") or 
                        data_json["msg"]["img"].get("cdnthumburl") or 
                        "")
                                
                if cdnurl:
                    cdn_body = {
                        "FileAesKey": aeskey,
                        "FileNo": cdnurl,
                        "Wxid": WXID
                    }
                    response_data = wechat_api("/Tools/CdnDownloadImage", cdn_body)
                
                    # 检查响应数据结构
                    if (response_data and "Data" in response_data and "Image" in response_data["Data"]):
                        cdn_base64 = response_data["Data"]["Image"]

                    if cdn_base64:
                        # 移除可能的base64头部
                        if ',' in cdn_base64:
                            cdn_base64 = cdn_base64.split(',', 1)[1]
                        # 解码base64为二进制数据
                        cdn_binary_data = base64.b64decode(cdn_base64)
                        all_binary_data.extend(cdn_binary_data)
                        cdn_success = True
                        logger.info(f"CDN下载成功，大小: {len(cdn_binary_data)} 字节")
            except Exception as e:
                logger.info(f"CDN下载失败，将使用分段下载: {str(e)}")
                cdn_success = False

        # 如果CDN下载失败或不是图片，使用分段下载
        if not cdn_success:
            # 配置分段大小
            chunk_size = 256 * 256
            total_chunks = math.ceil(data_length / chunk_size)
            
            logger.info(f"开始下载文件: {filename}, 初始大小: {data_length}, 分段数: {total_chunks}")
            
            # 初始化分段参数
            chunk_index = 1
            next_start_pos = 0
            next_chunk_size = min(chunk_size, data_length)
            
            # 循环下载所有分段
            retry_attempted = False  # 添加重试标志
            while True:
                logger.debug(f"下载分段 {chunk_index}/{total_chunks}, 起始位置: {next_start_pos}, 大小: {next_chunk_size}")
                
                # 构建请求参数
                if file_key == "appmsg":
                    payload = {
                        "AppID": file_info["appid"],
                        "AttachId": file_info["appattach"]["attachid"],
                        "DataLen": data_length,
                        "Section": {
                            "DataLen": next_chunk_size,
                            "StartPos": next_start_pos
                        },
                        "UserName": "",
                        "Wxid": WXID
                    }
                else:
                    payload = {
                        "CompressType": 0,
                        "DataLen": data_length,
                        "MsgId": msg_id,
                        "Section": {
                            "DataLen": next_chunk_size,
                            "StartPos": next_start_pos
                        },
                        "Wxid": WXID,
                        "ToWxid": from_wxid
                    }
                
                # 发送请求
                response_data = wechat_api(api_path, payload)
                
                # 解析响应JSON
                try:
                    if 'Data' in response_data and 'data' in response_data['Data'] and 'buffer' in response_data['Data']['data']:
                        # 获取base64字符串
                        base64_data = response_data['Data']['data']['buffer']
                        
                        # 移除可能存在的base64头部
                        if ',' in base64_data:
                            base64_data = base64_data.split(',', 1)[1]
                        
                        # 解码base64为二进制
                        binary_chunk = base64.b64decode(base64_data)
                        
                        # 添加到总数据中
                        all_binary_data.extend(binary_chunk)
                        logger.debug(f"成功接收分段 {chunk_index}, 大小: {len(binary_chunk)} 字节")
                    else:
                        # 当第一次请求获取不到buffer时，尝试更改payload重新请求
                        if chunk_index == 1 and not retry_attempted:
                            retry_attempted = True  # 标记已尝试重试
                            logger.warning("第一次请求未获取到buffer，尝试更改请求参数...")
                            
                            # 临时更改payload
                            if file_key == "appmsg":
                                temp_payload = {
                                    "AppID": file_info["appid"],
                                    "AttachId": file_info["appattach"]["attachid"],
                                    "Section": {
                                        "DataLen": chunk_size,
                                        "StartPos": 0
                                    },
                                    "UserName": "",
                                    "Wxid": WXID
                                }
                            else:
                                temp_payload = {
                                    "CompressType": 0,
                                    "MsgId": msg_id,
                                    "Section": {
                                        "DataLen": chunk_size,
                                        "StartPos": 0
                                    },
                                    "Wxid": WXID,
                                    "ToWxid": from_wxid
                                }
                            
                            # 发送临时请求
                            temp_data = wechat_api(api_path, temp_payload)
                            
                            # 尝试获取totalLen
                            if 'Data' in temp_data and 'totalLen' in temp_data['Data']:
                                # 更新data_length
                                new_data_length = temp_data['Data']['totalLen']
                                logger.info(f"获取到新的数据长度: {new_data_length}，原长度: {data_length}")
                                
                                # 使用新的data_length重新计算参数
                                data_length = new_data_length
                                total_chunks = math.ceil(data_length / chunk_size)
                                next_chunk_size = min(chunk_size, data_length)
                                
                                # 重新开始下载
                                logger.info(f"使用新的数据长度重新开始下载，总大小: {data_length}, 分段数: {total_chunks}")
                                continue
                            else:
                                logger.error("临时请求未能获取到totalLen，终止下载")
                                return False, "临时请求未能获取到totalLen"
                        else:
                            if chunk_index == 1:
                                logger.error("重试后仍无法获取buffer，终止下载")
                                return False, "重试后仍无法获取buffer"
                            else:
                                logger.error(f"响应格式错误: 找不到Data.data.buffer字段")
                                return False, f"响应格式错误: 找不到Data.data.buffer字段"
                except Exception as e:
                    logger.error(f"处理响应数据时出错: {str(e)}")
                    return False, f"处理响应数据时出错: {str(e)}"
                
                # 检查是否已下载完所有分段
                if chunk_index == total_chunks:
                    break
                    
                # 准备下一个请求
                chunk_index += 1
                next_start_pos = chunk_size * (chunk_index - 1)
                remaining_data = data_length - next_start_pos
                next_chunk_size = min(chunk_size, remaining_data)
        
        # 将完整的二进制数据写入文件
        with open(filepath, 'wb') as f:
            f.write(all_binary_data)
            
        logger.info(f"文件下载完成，保存至: {filepath}, 总大小: {len(all_binary_data)} 字节")
        return True, filepath
        
    except Exception as e:
        logger.exception(f"下载失败: {str(e)}")
        return False, f"下载失败: {str(e)}"