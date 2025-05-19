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
SAVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "image")

def get_image(msg_id: str, from_wxid: str, data_length: int) -> Tuple[bool, str]:
    try:
        # 文件名和路径
        filename = f"{msg_id}_{from_wxid}.png"
        filepath = os.path.join(SAVE_DIR, filename)
        
        # 确保保存目录存在
        os.makedirs(SAVE_DIR, exist_ok=True)
        
        # 配置分段大小
        chunk_size = 256 * 256
        total_chunks = math.ceil(data_length / chunk_size)
        
        logger.info(f"开始下载图片: {filename}, 初始大小: {data_length}, 分段数: {total_chunks}")
        
        # 初始化分段参数
        chunk_index = 1
        next_start_pos = 0
        next_chunk_size = min(chunk_size, data_length)
            
        # 用于存储所有分段的二进制数据
        all_binary_data = bytearray()
        
        # 循环下载所有分段
        while True:
            logger.debug(f"下载分段 {chunk_index}/{total_chunks}, 起始位置: {next_start_pos}, 大小: {next_chunk_size}")
            
            # 构建请求参数
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
            response_data = wechat_api("/Tools/DownloadImg", payload)
            
            # 解析响应JSON
            try:                
                # 提取Data.data.buffer部分
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
                    if chunk_index == 1:
                        logger.warning("第一次请求未获取到buffer，尝试更改请求参数...")
                        
                        # 临时更改payload
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
                        temp_data = wechat_api("/Tools/DownloadImg", temp_payload)
                        
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
                            logger.error("临时请求未能获取到totalLen")
                            return False, "临时请求未能获取到totalLen"
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
            
        logger.info(f"图片下载完成，保存至: {filepath}, 总大小: {len(all_binary_data)} 字节")
        return True, filepath
        
    except Exception as e:
        logger.exception(f"下载失败: {str(e)}")
        return False, f"下载失败: {str(e)}"
