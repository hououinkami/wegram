#!/usr/bin/env python3
"""
微信消息处理器 - 处理从主服务接收的消息和Telegram消息
"""
import logging
# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

from datetime import datetime
from typing import Dict, Any, Optional
import config
from api import contact, download
from api.base import telegram_api
from utils.contact import contact_manager
from utils.quote import MappingManager
from utils import xml, format

# 创建映射管理器实例
msgid_mapping = MappingManager()

def process_message(message_data: Dict[str, Any]) -> None:
    """处理微信消息"""
    try:
        msg_type = int(message_data.get('MsgType'))
        msg_id = message_data.get('MsgId')
        from_wxid = message_data.get("FromWxid")
        sender_wxid = message_data.get("SenderWxid")

        # 如果是我自己发送的消息
        if from_wxid == "":
            from_wxid = sender_wxid
            
        group_id = message_data.get("FromWxid")
        user_info = contact.get_user_info(sender_wxid)
        sender_name = format.escape_markdown_chars(user_info.name)
        
        # 原始回调内容
        content = message_data.get('Content')
        # 不是文本则进行XML解析
        if msg_type == 1 or msg_type == 49:
            content = format.escape_markdown_chars(content)
        else:
            content = xml.xml_to_json(content)
        logger.info(f"处理器收到消息: 类型={msg_type}, 发送者={sender_wxid}")
        logger.info(f"{content}")
        
        if not from_wxid or not content or from_wxid == config.MY_WXID:
            logger.warning("缺少发送者ID或消息内容")
            return

        # 读取contact映射
        contact_dic = contact_manager.get_contact(from_wxid)
        if contact_dic and contact_dic["isReceive"]:
            chat_id = contact_dic["chatId"]
        else:
            return
        
        # 非群聊不显示发送者
        if "chatroom" in group_id.lower() or contact_dic["wxId"] == "wxid_not_in_json":
            sender_name = f">{sender_name}"
        else:
            sender_name = ""

        # 根据消息类型进行不同处理
        # 文本消息
        if msg_type == 1:
            # 发送消息到Telegram
            response = telegram_api(
                chat_id=chat_id,
                content=f"{sender_name}\n{content}",
            )
        # 图片消息
        elif msg_type == 3:
            # 下载图片（企业微信用户无法下载）
            if not "openim" in from_wxid:
                success, filepath = download.get_image(
                    msg_id=msg_id,
                    from_wxid=from_wxid,
                    data_json=content
                )
            else:
                success = False

            if success:
                # 发送照片
                response = telegram_api(
                    chat_id=chat_id,
                    content=filepath,
                    method="sendPhoto",
                    additional_payload={
                        "caption": f"{sender_name}"
                    }
                )  
            else:
                response = telegram_api(
                    chat_id=chat_id,
                    content=f"{sender_name}\n\[{config.type(msg_type)}\]"
                )
        
        # 视频消息
        elif msg_type == 43:
            # 下载视频（企业微信用户无法下载）
            if not "openim" in from_wxid:
                success, filepath = download.get_video(
                    msg_id=msg_id,
                    from_wxid=from_wxid,
                    data_json=content
                )
            else:
                success = False

            if success:
                # 发送视频
                response = telegram_api(
                    chat_id=chat_id,
                    content=filepath,
                    method="sendVideo",
                    additional_payload={
                        "caption": f"{sender_name}"
                    }
                )
            else:
                response = telegram_api(
                    chat_id=chat_id,
                    content=f"{sender_name}\n\[{config.type(msg_type)}\]"
                )
                       
        # 公众号消息
        elif msg_type == 6:
            url_items = format.extract_url_items(content)
            logger.warning(f"{url_items}")
            response = telegram_api(
                chat_id=chat_id,
                content=f"{sender_name}\n{url_items}",
            )
                
        # 贴纸消息
        elif msg_type == 47:
            success, filepath = download.get_emoji(content)

            if success:
                # 发送视频
                response = telegram_api(
                    chat_id=chat_id,
                    content=filepath,
                    method="sendAnimation",
                    additional_payload={
                        "caption": f"{sender_name}"
                    }
                )
            else:
                response = telegram_api(
                    chat_id=chat_id,
                    content=f"{sender_name}\n\[{config.type(msg_type)}\]"
                )

        # 聊天记录消息
        elif msg_type == 19:            
            chat_history = process_chathistory(content)
            if chat_history:
                response = telegram_api(
                    chat_id=chat_id,
                    content=f"{sender_name}\n{chat_history}",
                )
            else:
                response = telegram_api(
                    chat_id=chat_id,
                    content=f"{sender_name}\n\[{config.type(msg_type)}\]"
                )

        # 引用消息
        elif msg_type == 49:
            response = telegram_api(
                chat_id=chat_id,
                content=f"{sender_name}\n{content}",
            )

        # 其他消息
        else:
            response = telegram_api(
                chat_id=chat_id,
                content=f"{sender_name}\n\[{config.type(msg_type)}\]"
            )
        
        # 储存消息ID
        tg_msgid = response['result']['message_id']
        msgid_mapping.add(msg_id, tg_msgid)
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}", exc_info=True)

# 处理聊天记录
def process_chathistory(content):
    chat_data = xml.xml_to_json(content["msg"]["appmsg"]["recorditem"])
    chat_json = chat_data["recordinfo"]
    
    # 提取标题和件数
    title = chat_json['title']
    count = chat_json['datalist']['count']
    
    # 提取所有 sourcetime 并转换为日期格式
    data_items = chat_json['datalist']['dataitem']
    sourcetimes = [item['sourcetime'] for item in data_items]
    sourcetimes_formatted = [datetime.strptime(time, "%Y-%m-%d %H:%M:%S") for time in sourcetimes]
    
    # 确定日期范围
    start_date = sourcetimes_formatted[0].strftime("%Y-%m-%d")
    end_date = sourcetimes_formatted[-1].strftime("%Y-%m-%d")
    date_range = f"{start_date} ～ {end_date}" if start_date != end_date else start_date
    
    # 构建聊天记录文本
    chat_history = [f"{format.escape_markdown_chars(title)}\n件数：{count}\n日期：{format.escape_markdown_chars(date_range)}\n**>"]
    
    for item in data_items:
        sourcename = item['sourcename']
        sourcetime = datetime.strptime(item['sourcetime'], "%Y-%m-%d %H:%M:%S").strftime("%m/%d %H:%M")
        datadesc = item.get('datadesc', "[不明]") if item['datatype'] != '1' else item.get('datadesc', "[不明]")
        
        chat_history.append(f">{format.escape_markdown_chars(sourcename)} ({sourcetime})\n>{format.escape_markdown_chars(datadesc)}")

    # 返回格式化后的文本
    return "\n".join(chat_history)
    