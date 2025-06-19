import json
import logging
from typing import Any, Dict, Optional

import requests

import config

logger = logging.getLogger(__name__)

# 发送TG消息(停用)
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

# 消息处理（停用）
def telegram_message(method, chat_id, message_id, text=None, parse_mode=None, reply_markup=None):
    """
    Telegram消息操作函数
    
    Args:
        bot_token: Bot令牌
        method: 操作方法 ('edit' 或 'delete')
        chat_id: 聊天ID
        message_id: 消息ID
        text: 消息文本（编辑时必需）
        parse_mode: 解析模式 ('HTML', 'Markdown', 'MarkdownV2')
        reply_markup: 内联键盘字典
        
    Returns:
        dict: API响应结果
    """
    base_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
    
    if method == 'edit':
        if not text:
            return {"ok": False, "error": "编辑消息需要提供text参数"}
        
        url = f"{base_url}/editMessageText"
        data = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text
        }
        
        if parse_mode:
            data['parse_mode'] = parse_mode
        if reply_markup:
            data['reply_markup'] = json.dumps(reply_markup)
            
    elif method == 'delete':
        url = f"{base_url}/deleteMessage"
        data = {
            'chat_id': chat_id,
            'message_id': message_id
        }
    else:
        return {"ok": False, "error": f"不支持的方法: {method}"}
    
    response = requests.post(url, data=data)
    return response.json()
