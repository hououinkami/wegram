#!/usr/bin/env python3
"""
微信消息处理器 - 处理从主服务接收的消息和Telegram消息
"""
import logging
import asyncio
from typing import Dict, Any, Optional
import pilk
import ffmpeg
import os

# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

from datetime import datetime
import config
from api import contact, download
from api.base import telegram_api
from utils.contact import contact_manager
from utils.msgid import msgid_mapping
from utils import format

def _get_message_handlers():
    """返回消息类型处理器映射"""
    return {
        1: _forward_text,
        3: _forward_image,
        43: _forward_video,
        34: _forward_voice,
        6: _forward_file,
        5: _forward_link,
        47: _forward_sticker,
        19: _forward_chat_history,
        57: _forward_quote,
        51: _forward_channel,
        10002: _forward_revoke
    }

def _forward_text(chat_id: int, sender_name: str, content: str, **kwargs) -> dict:
    """处理文本消息"""
    return telegram_api(
        chat_id=chat_id,
        content=f"{sender_name}\n{content}",
    )

def _forward_image(chat_id: int, sender_name: str, msg_id: str, from_wxid: str, content: dict, **kwargs) -> dict:
    """处理图片消息"""
    success, filepath = download.get_image(
        msg_id=msg_id,
        from_wxid=from_wxid,
        data_json=content
    )
    
    if success:
        return telegram_api(
            chat_id=chat_id,
            content=filepath,
            method="sendPhoto",
            additional_payload={
                "caption": f"{sender_name}"
            }
        )
    else:
        return telegram_api(
            chat_id=chat_id,
            content=f"{sender_name}\n\[{config.type(3)}\]"
        )

def _forward_video(chat_id: int, sender_name: str, msg_id: str, from_wxid: str, content: dict, **kwargs) -> dict:
    """处理视频消息"""
    success, filepath = download.get_video(
        msg_id=msg_id,
        from_wxid=from_wxid,
        data_json=content
    )
    
    if success:
        return telegram_api(
            chat_id=chat_id,
            content=filepath,
            method="sendVideo",
            additional_payload={
                "caption": f"{sender_name}"
            }
        )
    else:
        return telegram_api(
            chat_id=chat_id,
            content=f"{sender_name}\n\[{config.type(43)}\]"
        )

def _forward_voice(chat_id: int, sender_name: str, msg_id: str, content: dict, message_info: dict, **kwargs) -> dict:
    """处理语音消息"""
    success, filepath = download.get_voice(
        msg_id=msg_id,
        data_json=content,
        from_user_name=message_info['FromUserName']
    )

    ogg_path, duration = silk_to_voice(filepath)

    if success:
        return telegram_api(
            chat_id=chat_id,
            content=ogg_path,
            method="sendVoice",
            additional_payload={
                "caption": f"{sender_name}",
                "duration": duration
            }
        )
    else:
        return telegram_api(
            chat_id=chat_id,
            content=f"{sender_name}\n\[{config.type(34)}\]"
        )

def _forward_file(chat_id: int, sender_name: str, msg_id: str, from_wxid: str, content: dict, **kwargs) -> dict:
    """处理文件消息"""
    success, filepath = download.get_file(
        msg_id=msg_id,
        from_wxid=from_wxid,
        data_json=content
    )
    
    if success:
        return telegram_api(
            chat_id=chat_id,
            content=filepath,
            method="sendDocument",
            additional_payload={
                "caption": f"{sender_name}"
            }
        )
    else:
        return telegram_api(
            chat_id=chat_id,
            content=f"{sender_name}\n\[{config.type(6)}\]"
        )

def _forward_link(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理公众号消息"""
    url_items = format.extract_url_items(content)
    return telegram_api(
        chat_id=chat_id,
        content=f"{sender_name}\n{url_items}",
    )

def _forward_sticker(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理贴纸消息"""
    success, filepath = download.get_emoji(content)
    
    if success:
        return telegram_api(
            chat_id=chat_id,
            content=filepath,
            method="sendAnimation",
            additional_payload={
                "caption": f"{sender_name}"
            }
        )
    else:
        return telegram_api(
            chat_id=chat_id,
            content=f"{sender_name}\n\[{config.type(47)}\]"
        )

def _forward_chat_history(chat_id: int, sender_name_no_md: str, content: dict, **kwargs) -> dict:
    """处理聊天记录消息"""
    chat_history = f"{process_chathistory(content)}"
    
    if chat_history:
        return telegram_api(
            chat_id=chat_id,
            content=f"{sender_name_no_md}\n{chat_history}",
            parse_mode="HTML"
        )
    else:
        return telegram_api(
            chat_id=chat_id,
            content=f"{sender_name_no_md}\n\[{config.type(19)}\]"
        )

def _forward_quote(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理引用消息"""
    send_text = format.escape_markdown_chars(content["msg"]["appmsg"]["title"])
    quote = content["msg"]["appmsg"]["refermsg"]
    quote_newmsgid = quote["svrid"]
    
    additional_payload = {}
    if quote_newmsgid:
        quote_tgmsgid = msgid_mapping.wx_to_tg(quote_newmsgid)
        if quote_tgmsgid:
            additional_payload = {"reply_to_message_id": quote_tgmsgid}
    
    return telegram_api(
        chat_id=chat_id,
        content=f"{sender_name}\n{send_text}",
        additional_payload=additional_payload
    )

def _forward_revoke(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理撤回消息"""
    revoke_msg = content["sysmsg"]["revokemsg"]
    send_text = revoke_msg["replacemsg"]
    quote_newmsgid = revoke_msg["newmsgid"]
    
    additional_payload = {}
    if quote_newmsgid:
        quote_tgmsgid = msgid_mapping.wx_to_tg(quote_newmsgid)
        if quote_tgmsgid:
            additional_payload = {"reply_to_message_id": quote_tgmsgid}
    
    return telegram_api(
        chat_id=chat_id,
        content=f"{sender_name}\n{send_text}",
        additional_payload=additional_payload
    )

def _forward_channel(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理视频号"""
    try:
        finder_feed = content.get("msg", {}).get("appmsg", {}).get("finderFeed", {})
        channel_name = format.escape_markdown_chars(finder_feed["nickname"])
        channel_title = format.escape_markdown_chars(finder_feed["desc"])

        return telegram_api(
            chat_id=chat_id,
            content=f"{sender_name}\n\[{config.type(51)}\]\n{channel_name}\n{channel_title}",
        )
    except (KeyError, TypeError) as e:
        if content.get("msg", {}).get("appmsg", {}).get("finderFeed"):
            logger.info(f"解析视频号消息失败: {e}")
            return telegram_api(
                chat_id=chat_id,
                content=f"{sender_name}\n\[{config.type(51)}\]",
            )

async def _process_message_async(message_info: Dict[str, Any]) -> None:
    """异步处理单条消息"""
    try:
        msg_type = int(message_info['MsgType'])
        msg_id = message_info['MsgId']
        new_msg_id = message_info['NewMsgId']
        from_wxid = message_info['FromUserName']
        to_wxid = message_info['ToUserName']
        content = message_info['Content']
        push_content = message_info['PushContent']
        create_time = message_info['CreateTime']
        
        # 转发自己的消息
        if from_wxid == config.MY_WXID:
            from_wxid = to_wxid
            
        # 处理群聊消息格式
        if from_wxid.endswith('@chatroom'):
            if ':\n' in content:
                sender_part, content_part = content.split('\n', 1)
                sender_wxid = sender_part.rstrip(':')
                content = content_part
            else:
                sender_wxid = message_info['FromUserName'] if message_info['FromUserName'] == config.MY_WXID else ""
        else:
            sender_wxid = from_wxid

        # 获取发送者信息
        user_info = contact.get_user_info(sender_wxid)
        sender_name = format.escape_markdown_chars(user_info.name)
        if sender_name == "未知用户" and push_content:
            sender_name = push_content.split(" : ")[0]
        
        # 处理消息内容
        if msg_type == 1:
            content = format.escape_markdown_chars(content)
        else:
            content = format.xml_to_json(content)
            if msg_type == 49:
                msg_type = int(content['msg']['appmsg']['type'])

        logger.info(f"处理器收到消息: 类型={msg_type}, 发送者={sender_wxid}")
        logger.info(f"调试：：：{content}")
        
        if not from_wxid or not content:
            logger.warning("缺少发送者ID或消息内容")
            return

        # 获取或创建群组
        chat_id = await _get_or_create_chat(from_wxid, sender_name, user_info.avatar_url)
        if not chat_id:
            return
        
        # 获取联系人信息用于显示
        contact_dic = await contact_manager.get_contact(from_wxid)
        
        # 设置发送者显示名称
        if "chatroom" in from_wxid or (contact_dic and contact_dic["wxId"] == "wxid_not_in_json"):
            sender_name = f">{sender_name}"
            sender_name_no_md = f"<blockquote expandable>{format.escape_html_chars(user_info.name)}</blockquote>"
        else:
            sender_name = ""
            sender_name_no_md = ""

        # 跳过未知消息类型
        if not config.type(msg_type):
            return
        
        # 获取消息处理器
        handlers = _get_message_handlers()
        
        # 准备通用参数
        handler_params = {
            'chat_id': chat_id,
            'sender_name': sender_name,
            'sender_name_no_md': sender_name_no_md,
            'content': content,
            'msg_id': msg_id,
            'from_wxid': from_wxid,
            'message_info': message_info,
            'msg_type': msg_type
        }
        
        # 调用对应的处理器
        if msg_type in handlers:
            response = handlers[msg_type](**handler_params)
        else:
            # 处理其他未知消息类型
            response = telegram_api(
                chat_id=chat_id,
                content=f"{sender_name}\n\[{config.type(msg_type)}\]"
            )
        
        # 储存消息ID
        if response and response.get('ok', False):
            tg_msgid = response['result']['message_id']
            msgid_mapping.add(
                tg_msg_id=tg_msgid,
                from_wx_id=sender_wxid,
                to_wx_id=to_wxid,
                wx_msg_id=new_msg_id,
                client_msg_id=0,
                create_time=create_time,
                content=content if msg_type == 1 else ""
            )
            
    except Exception as e:
        logger.error(f"异步消息处理失败: {e}", exc_info=True)

async def _create_group_for_contact_async(wxid: str, contact_name: str, avatar_url: str = None) -> Optional[int]:
    """异步创建群组"""
    try:
        logger.info(f"开始为 {wxid} 创建群组，名称: {contact_name}")
        
        if not wxid or not contact_name:
            logger.error(f"参数无效: wxid={wxid}, contact_name={contact_name}")
            return None
        
        result = await contact_manager.create_group_for_contact_async(
            wxid=wxid,
            contact_name=contact_name,
            avatar_url=avatar_url
        )
        
        if result and result.get('success'):
            chat_id = result['chat_id']
            logger.info(f"群组创建成功: {wxid} -> {chat_id}")
            return chat_id
        else:
            error_msg = result.get('error', '未知错误') if result else '返回结果为空'
            logger.error(f"群组创建失败: {wxid}, 错误: {error_msg}")
            return None
            
    except Exception as e:
        logger.error(f"创建群组异常: {e}", exc_info=True)
        return None

async def _get_or_create_chat(from_wxid: str, sender_name: str, avatar_url: str) -> Optional[int]:
    """获取或创建聊天群组"""
    # 读取contact映射
    contact_dic = await contact_manager.get_contact(from_wxid)
    
    if contact_dic and not contact_dic["isReceive"]:
        return None
        
    if contact_dic and contact_dic["isReceive"]:
        return contact_dic["chatId"]
    
    # 检查是否允许自动创建群组
    auto_create = getattr(config, 'AUTO_CREATE_GROUPS', True)
    if not auto_create or from_wxid == config.MY_WXID:
        logger.info(f"自动创建群组已禁用，跳过: {from_wxid}")
        return None
    
    # 创建群组
    logger.info(f"未找到映射关系，为 {from_wxid} 创建群组")
    chat_id = await _create_group_for_contact_async(from_wxid, sender_name, avatar_url)
    if not chat_id:
        logger.warning(f"无法创建聊天群组: {from_wxid}")
        return None
    
    return chat_id

# 处理聊天记录
def process_chathistory(content):
    chat_data = format.xml_to_json(content["msg"]["appmsg"]["recorditem"])
    chat_json = chat_data["recordinfo"]
    
    # 提取标题和件数
    title = content["msg"]["appmsg"]['title']
    count = chat_json['datalist']['count']
    
    # 提取所有 sourcetime 并转换为 datetime 对象
    data_items = chat_json['datalist']['dataitem']        
    sourcetimes_dt = [parse_time_without_seconds(item['sourcetime']) for item in data_items]
    
    # 确定日期范围
    start_date = sourcetimes_dt[0].strftime("%Y/%m/%d")
    end_date = sourcetimes_dt[-1].strftime("%Y/%m/%d")
    date_range = f"{start_date} ～ {end_date}" if start_date != end_date else start_date

    # 构建聊天记录文本
    chat_history = [f"{format.escape_html_chars(title)}\n件数：{count}\n日期：{format.escape_html_chars(date_range)}"]
    
    # 判断起止日期是否相同
    dates = {dt.date() for dt in sourcetimes_dt}
    same_date = len(dates) == 1

    for i, item in enumerate(data_items):
        sourcename = item['sourcename']
        dt = sourcetimes_dt[i]

        # 根据是否同一天选择格式
        sourcetime = dt.strftime("%H:%M" if same_date else "%m/%d %H:%M")
    
        datadesc = item.get('datadesc', "[不明]") if item['datatype'] != '1' else item.get('datadesc', "[不明]")
        chat_history.append(f"👤{format.escape_html_chars(sourcename)} ({sourcetime})\n{format.escape_html_chars(datadesc)}")

    # 返回格式化后的文本
    chat_history = "\n".join(chat_history)
    return f"<blockquote expandable>{chat_history}</blockquote>"

def parse_time_without_seconds(time_str):
        """解析时间并忽略秒数"""
        import re
        time_str = re.sub(r'(\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{1,2}):\d{1,2}', r'\1', time_str)
        
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        except ValueError:
            logger.warning(f"无法解析时间格式: {time_str}，使用当前时间")
            return datetime.now()

def silk_to_voice(silk_path):
    """转换微信语音为Telegram语音"""
    pcm_path = silk_path + '.pcm'
    ogg_path = silk_path + '.ogg'
    
    try:
        # silk -> pcm
        duration = pilk.decode(silk_path, pcm_path)
        logger.info(f"语音时长: {duration}s")
        
        # pcm -> ogg opus
        (
            ffmpeg
            .input(pcm_path, format='s16le', acodec='pcm_s16le', ar=24000, ac=1)
            .output(ogg_path, acodec='libopus', audio_bitrate='64k')
            .overwrite_output()
            .run(quiet=True)
        )

        return ogg_path, int(duration)
            
    except Exception as e:
       logger.error(f"语音转换失败: {e}")
       return None, None
    
    finally:
        # 清理可能存在的临时文件
        for temp_file in [silk_path, pcm_path, ogg_path]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError as e:
                    logger.warning(f"清理临时文件失败 {temp_file}: {e}")
        
# 提取回调信息
def extract_message(data):
    try:
        # 提取所需字段
        message_info = {
            'MsgId': data.get('MsgId'),
            'NewMsgId': data.get('NewMsgId'),
            'FromUserName': data.get('FromUserName', {}).get('string', ''),
            'ToUserName': data.get('ToUserName', {}).get('string', ''),
            'MsgType': data.get('MsgType'),
            'Content': data.get('Content', {}).get('string', ''),
            'PushContent': data.get('PushContent', ''),
            'CreateTime': data.get('CreateTime'),
        }
        
        return message_info
        
    except Exception as e:
        logger.error(f"提取消息信息失败: {e}")
        return None

def process_message(message_data: Dict[str, Any]) -> None:
    """处理微信消息"""
    logger.info(f"调试：：：{message_data}")
    
    try:
        message_info = extract_message(message_data)
        if not message_info:
            logger.error("提取消息信息失败")
            return
        
        if message_info["FromUserName"] == "weixin":
            logger.info("跳过微信官方消息")
            return
        
        # 简化的异步处理
        try:
            loop = asyncio.get_running_loop()
            # 如果有运行的循环，创建异步任务
            loop.create_task(_process_message_async(message_info))
        except RuntimeError:
            # 没有运行的循环时，直接运行
            asyncio.run(_process_message_async(message_info))
            
    except Exception as e:
        logger.error(f"消息处理失败: {e}", exc_info=True)

