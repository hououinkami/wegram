#!/usr/bin/env python3
import logging
import requests
import json
import config
from typing import Dict, Any

logger = logging.getLogger(__name__)

# API请求函数
def wechat_api(api_path: str, body: Dict[str, Any] = None, query_params: Dict[str, Any] = None):
    api_url = f"{config.BASE_URL}{api_path}"
    try:
        response = requests.post(
            url=api_url,
            json=body,  # 请求体数据
            params=query_params  # URL 查询参数
        )
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"API调用失败，状态码: {response.status_code}, 响应: {response.text}")
            return False
    except Exception as e:
        logger.error(f"调用微信API时出错: {e}")
        return False

# 发送TG消息
def telegram_api(chat_id, content=None, method="sendMessage", additional_payload=None, parse_mode="HTML", **kwargs):
    # 检查必要参数
    if not chat_id:
        logger.error("未提供有效的 chat_id")
        return None
    
    # 构建 API URL
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    
    # 根据方法确定内容参数名称
    method_param_mapping = {
        "sendMessage": "text",
        "sendPhoto": "photo",
        "sendDocument": "document",
        "sendVideo": "video",
        "sendAudio": "audio",
        "sendVoice": "voice",
        "sendAnimation": "animation",
        "sendVideoNote": "video_note",
        "sendMediaGroup": "media",
        "sendSticker": "sticker",
        "sendPoll": "question",
        "sendLocation": "latitude"  # 注意：location 需要 latitude 和 longitude
    }
    content_param_name = method_param_mapping.get(method, "text")
        
    
    # 构建基本 payload
    payload = {
        "chat_id": chat_id,
        "parse_mode": parse_mode
    }
    
    # 只有当提供了内容时才添加内容参数
    if content is not None:
        payload[content_param_name] = content
    
    # 合并额外的 payload 参数（如果有）
    payload.update(kwargs)
    
    # 发送请求
    files = None
    try:        
        # 检查是否需要以 multipart/form-data 形式发送
        if method in ["sendPhoto", "sendDocument", "sendVideo", "sendAudio", "sendVoice", "sendAnimation", "sendVideoNote"]:
            # 检查内容是否为本地文件路径
            if content and isinstance(content, str) and not content.startswith(('http://', 'https://')):
                try:
                    # 创建一个不包含文件对象的 payload 副本
                    form_data = payload.copy()
                    # 从 form_data 中移除内容参数，因为它将作为文件发送
                    if content_param_name in form_data:
                        del form_data[content_param_name]
                    # 尝试打开文件
                    files = {content_param_name: open(content, 'rb')}
                    
                    # 发送请求，使用 form_data 而不是 payload
                    response = requests.post(url, data=form_data, files=files)
                except Exception as file_error:
                    logger.error(f"无法打开文件 {content}: {file_error}")
                    # 如果文件打开失败，回退到非文件方式发送
                    files = None
        # 如果不是文件发送或文件打开失败，使用 JSON 方式发送
        if files is None:
            response = requests.post(url, json=payload)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"发送内容失败，状态码: {response.status_code}, 响应: {response.text}")
            return response.json()
    except Exception as e:
        logger.error(f"发送内容时出错: {e}")
        return None
    finally:
        # 关闭打开的文件
        if files:
            for file in files.values():
                if hasattr(file, 'close'):
                    file.close()
