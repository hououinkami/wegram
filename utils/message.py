#!/usr/bin/env python3
"""
微信消息处理器 - 处理从主服务接收的消息和Telegram消息
"""
import logging
# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

from datetime import datetime
from typing import Dict, Any
import config
from api import contact, download
from api.base import telegram_api
from utils.contact import contact_manager
from utils import xml, format


def process_message(message_data: Dict[str, Any]) -> None:
    """处理微信消息"""
    try:
        msg_type = message_data.get('MsgType')
        msg_id = message_data.get('MsgId')
        from_wxid = message_data.get("FromWxid")
        sender_wxid = message_data.get("SenderWxid")

        # 如果是我自己发送的消息
        if from_wxid == "":
            from_wxid = sender_wxid
            
        group_id = message_data.get("FromWxid")
        user_info = contact.get_user_info(sender_wxid)
        sender_name = format.escape_markdown_chars(user_info.name)
        
        if "chatroom" in group_id.lower():
            sender_name = f">{sender_name}"
        else:
            sender_name = ""
        # 原始回调内容
        content = message_data.get('Content')
        # 不是文本则进行XML解析
        if msg_type == 1:
            content = format.escape_markdown_chars(content)
        else:
            content = xml.xml_to_json(content)
        logger.info(f"处理器收到消息: 类型={msg_type}, 发送者={sender_wxid}")
        
        if not from_wxid or not content:
            logger.warning("缺少发送者ID或消息内容")
            return

        # 读取contact映射
        contact_dic = contact_manager.get_contact(from_wxid)
        if contact_dic and contact_dic["isReceive"]:
            chat_id = contact_dic["chatId"]
        else:
            return
        
        # 根据消息类型进行不同处理
        # 文本消息
        logger.warning(f"{content}")
        if msg_type == 1:
            telegram_api(
                chat_id=chat_id,
                content=f"{sender_name}\n{content}",
            )
        # 图片消息
        elif msg_type == 3:
            # 下载图片
            success, filepath = download.get_image(
                msg_id=msg_id,
                from_wxid=from_wxid,
                data_length=int(content["msg"]["img"]["length"])
            )
            if success:
                # 发送照片
                telegram_api(
                    chat_id=chat_id,
                    content=filepath,
                    method="sendPhoto",
                    additional_payload={
                        "caption": f"{sender_name}"
                    }
                )
            else:
                telegram_api(
                    chat_id=chat_id,
                    content=f"{sender_name}\n[{config.type(msg_type)}]"
                )
        # 公众号消息
        elif msg_type == 6:
            logger.warning(f"{content}")
            url_items = format.extract_url_items(content)
            logger.warning(f"{url_items}")
            telegram_api(
                chat_id=chat_id,
                content=f"{sender_name}\n{url_items}",
            )
        else:
            telegram_api(
                chat_id=chat_id,
                content=f"{sender_name}\n[{config.type(msg_type)}]"
            )
        # 添加其他消息类型的处理...
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
