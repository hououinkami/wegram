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
    message_info = extract_message(message_data)
    if not message_info:
        return
    """处理微信消息"""
    try:
        msg_type = int(message_info['MsgType'])
        msg_id = message_info['MsgId']
        from_wxid = message_info['FromUserName']
        content = message_info['Content']

        # 判断是否为群聊消息
        if from_wxid.endswith('@chatroom'):
            # 群聊消息格式处理
            if ':\n' in content:
                # 分割消息内容
                sender_part, content_part = content.split('\n', 1)
                # 提取发送者ID（去掉最后的冒号）
                sender_wxid = sender_part.rstrip(':')
                # 更新content为实际消息内容
                content = content_part
            else:
                # 如果没有换行符，可能是系统消息
                sender_wxid = ''
        else:
            # 私聊消息，发送者就是FromUserName
            sender_wxid = from_wxid

        user_info = contact.get_user_info(sender_wxid)
        sender_name = format.escape_markdown_chars(user_info.name)
        
        # 不是文本则进行XML解析
        if msg_type == 1:
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
        if "chatroom" in from_wxid or contact_dic["wxId"] == "wxid_not_in_json":
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
            chat_history = f"\[{config.type(msg_type)}\]\n{process_chathistory(content)}"
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
        if response and response.get('ok', False):
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
    chat_history = [f">{format.escape_markdown_chars(title)}\n>件数：{count}\n>日期：{format.escape_markdown_chars(date_range)}\n**>测试"]
    
    for item in data_items:
        sourcename = item['sourcename']
        sourcetime = datetime.strptime(item['sourcetime'], "%Y-%m-%d %H:%M:%S").strftime("%m/%d %H:%M")
        datadesc = item.get('datadesc', "[不明]") if item['datatype'] != '1' else item.get('datadesc', "[不明]")
        chat_history.append(f">👤{format.escape_markdown_chars(sourcename)} \({sourcetime}\)\n>{format.escape_markdown_chars(datadesc)}")

    # 返回格式化后的文本
    return "\n".join(chat_history)

# 提取回调信息
def extract_message(data):
    """安全地提取第一条消息的关键信息"""
    try:
        if data.get('Message') == "当前未有新消息":
            return None
        # 检查是否有消息
        add_msgs = data.get('Data', {}).get('AddMsgs', [])
        if not add_msgs:
            print("没有新消息")
            return None
        
        # 获取第一条消息
        first_msg = add_msgs[0]
        
        # 提取所需字段
        message_info = {
            'MsgId': first_msg.get('MsgId'),
            'FromUserName': first_msg.get('FromUserName', {}).get('string', ''),
            'ToUserName': first_msg.get('ToUserName', {}).get('string', ''),
            'MsgType': first_msg.get('MsgType'),
            'Content': first_msg.get('Content', {}).get('string', '')
        }
        
        return message_info
        
    except Exception as e:
        print(f"提取消息信息失败: {e}")
        return None
    