#!/usr/bin/env python3
"""
微信消息处理器 - 处理从主服务接收的消息和Telegram消息
"""
import logging
from typing import Dict, Any, Optional
import pilk
import ffmpeg
import os
import re
import asyncio
from asyncio import Queue
import threading

# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

from datetime import datetime
import config
from utils.locales import Locale
from api import contact, download
from api.base import telegram_api
from utils.contact import contact_manager
from utils.msgid import msgid_mapping
from utils import format

locale = Locale(config.LANG)
black_list = ['open_chat', 'bizlivenotify', 74]

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
        33: _forward_miniprogram,
        51: _forward_channel,
        2000: _forward_transfer,
        "revokemsg": _forward_revoke,
        "pat": _forward_pat,
        "VoIPBubbleMsg": _forward_voip
    }

def _forward_text(chat_id: int, sender_name: str, content: str, **kwargs) -> dict:
    """处理文本消息"""
    text = format.escape_html_chars(content)
    send_text = f"{sender_name}\n{text}"
    return telegram_api(chat_id, send_text)

def _forward_image(chat_id: int, sender_name: str, msg_id: str, from_wxid: str, content: dict, **kwargs) -> dict:
    """处理图片消息"""
    success, filepath = download.get_image(msg_id, from_wxid, content)
    
    if success:
        return telegram_api(chat_id, filepath, "sendPhoto", caption=sender_name)
    else:
        raise Exception("图片下载失败")

def _forward_video(chat_id: int, sender_name: str, msg_id: str, from_wxid: str, content: dict, **kwargs) -> dict:
    """处理视频消息"""
    success, filepath = download.get_video(msg_id, from_wxid, content)
    
    if success:
        return telegram_api(chat_id, filepath, "sendVideo", caption=sender_name)
    else:
        raise Exception("视频下载失败")

def _forward_voice(chat_id: int, sender_name: str, msg_id: str, content: dict, message_info: dict, **kwargs) -> dict:
    """处理语音消息"""
    success, filepath = download.get_voice(msg_id, message_info['FromUserName'], content)

    if not success:
        raise Exception("语音下载失败")
        
    ogg_path, duration = silk_to_voice(filepath)
    if not ogg_path or not duration:
        raise Exception("语音转换失败")
    
    return telegram_api(chat_id, ogg_path, "sendVoice", caption=sender_name, duration=duration)
    
def _forward_file(chat_id: int, sender_name: str, msg_id: str, from_wxid: str, content: dict, **kwargs) -> dict:
    """处理文件消息"""
    success, filepath = download.get_file(msg_id, from_wxid, content)
    
    if success:
        return telegram_api(chat_id, filepath, "sendDocument", caption=sender_name)
    else:
        raise Exception("文件下载失败")

def _forward_link(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理公众号消息"""
    url_items = format.extract_url_items(content)
    send_text = f"{sender_name}\n{url_items}"
    return telegram_api(chat_id, send_text)

def _forward_sticker(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理贴纸消息"""
    success, filepath = download.get_emoji(content)
    
    if success:
        return telegram_api(chat_id, filepath, "sendAnimation", caption=sender_name)
    else:
        raise Exception("贴纸下载失败")

def _forward_chat_history(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理聊天记录消息"""
    chat_history = f"{process_chathistory(content)}"
    
    if chat_history:
        send_text = f"{sender_name}\n{chat_history}"
        return telegram_api(chat_id, send_text)
    else:
        raise Exception("聊天记录处理失败")

def _forward_quote(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理引用消息"""
    text = format.escape_html_chars(content["msg"]["appmsg"]["title"])
    quote = content["msg"]["appmsg"]["refermsg"]
    quote_newmsgid = quote["svrid"]
    
    quote_tgmsgid = msgid_mapping.wx_to_tg(quote_newmsgid) or 0 if quote_newmsgid else 0
    send_text = f"{sender_name}\n{text}"
    return telegram_api(chat_id, send_text, reply_to_message_id=quote_tgmsgid)

def _forward_miniprogram(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理小程序消息"""
    mini_name = content.get('msg', {}).get('appmsg', {}).get('sourcedisplayname', '')
    mini_title = content.get('msg', {}).get('appmsg', {}).get('title', '')
    send_text = f"{sender_name}\n[{locale.type(kwargs.get('msg_type'))}]\n{mini_name}\n{mini_title}"
    return telegram_api(chat_id, send_text)

def _forward_channel(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理视频号"""
    try:
        finder_feed = content.get("msg", {}).get("appmsg", {}).get("finderFeed", {})
        channel_name = finder_feed["nickname"]
        channel_title = finder_feed["desc"]
        channel_content = format.escape_html_chars(f"[{locale.type(kwargs.get('msg_type'))}]\n{channel_name}\n{channel_title}")
        send_text = f"{sender_name}\n{channel_content}"
        return telegram_api(chat_id, send_text)
    except (KeyError, TypeError) as e:
        raise Exception("视频号信息提取失败")

def _forward_transfer(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理转账"""
    try:
        money = content.get('msg', {}).get('appmsg', {}).get('wcpayinfo', {}).get('feedesc')
        channel_content = format.escape_html_chars(f"[{locale.type(kwargs.get('msg_type'))}]\n{money}")
        send_text = f"{sender_name}\n{channel_content}"
        return telegram_api(chat_id, send_text)
    except (KeyError, TypeError) as e:
        raise Exception("转账信息提取失败")
    
def _forward_revoke(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理撤回消息"""
    revoke_msg = content["sysmsg"]["revokemsg"]
    revoke_text = format.escape_html_chars(revoke_msg["replacemsg"])
    quote_newmsgid = revoke_msg["newmsgid"]

    quote_tgmsgid = msgid_mapping.wx_to_tg(quote_newmsgid) or 0 if quote_newmsgid else 0
    send_text = f"{sender_name}\n{revoke_text}"
    return telegram_api(chat_id, send_text, reply_to_message_id = quote_tgmsgid)

def _forward_pat(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理撤回消息"""
    pat_msg = content["sysmsg"]["pat"]
    pat_template = pat_msg["template"]
    pattern = r'\$\{([^}]+)\}'
    result = re.sub(pattern, lambda m: contact.get_user_info(m.group(1)).name, pat_template)
    pat_text = f"[{format.escape_html_chars(result)}]"
    send_text = f"{sender_name}\n{pat_text}"
    return telegram_api(chat_id, send_text)

def _forward_voip(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理通话消息"""
    voip_msg = content["voipmsg"]["VoIPBubbleMsg"]["msg"]
    send_text = f"{sender_name}\n{voip_msg}"
    return telegram_api(chat_id, send_text)

async def _process_message_async(message_info: Dict[str, Any]) -> None:
    """异步处理单条消息"""

    def _send_message_with_handler(chat_id: int, msg_type: Any, handler_params: dict) -> dict:
        """使用处理器发送消息的通用方法"""
        handlers = _get_message_handlers()
        
        if msg_type in handlers:
            try:
                return handlers[msg_type](**{**handler_params, 'chat_id': chat_id})
            except Exception as e:
                logger.error(f"处理器执行失败 (类型={msg_type}): {e}", exc_info=True)
                type_text = format.escape_html_chars(f"[{locale.type(msg_type)}]")
                send_text = f"{handler_params['sender_name']}\n{type_text}"
                return telegram_api(chat_id, send_text)
        else:
            # 处理未知消息类型
            logger.warning(f"❓未知消息类型: {msg_type}")
            type_text = format.escape_html_chars(f'[{locale.type(msg_type) or locale.type("unknown")}]')
            send_text = f"{handler_params['sender_name']}\n{type_text}"
            return telegram_api(chat_id, send_text)
    
    async def _handle_deleted_group(from_wxid: str, handler_params: dict, content: dict, push_content: str, msg_type: Any) -> Optional[dict]:
        """处理被删除的群组"""
        try:
            # 删除联系人信息
            await contact_manager.delete_contact(from_wxid)
            logger.info(f"已删除联系人信息: {from_wxid}")
            
            # 重新获取或创建聊天群组
            contact_name, avatar_url = await _get_contact_info(from_wxid, content, push_content)
            
            # 创建新群组
            logger.info(f"尝试重新创建群组: {from_wxid}")
            new_chat_id = await _create_group_for_contact(from_wxid, contact_name, avatar_url)
            
            if new_chat_id:
                logger.info(f"群组重新创建成功: {from_wxid} -> {new_chat_id}")
                # 重新发送消息
                return _send_message_with_handler(new_chat_id, msg_type, handler_params)
            else:
                logger.error(f"群组重新创建失败: {from_wxid}")
                return None
                
        except Exception as e:
            logger.error(f"处理群组删除异常: {e}", exc_info=True)
            return None

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
        
        # 处理服务通知
        if from_wxid.endswith('@app'):
            from_wxid = "service_notification"
        
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
        
        # 获取联系人信息
        contact_name, avatar_url = await _get_contact_info(from_wxid, content, push_content)

        # 获取发送者信息
        if sender_wxid == from_wxid:
            sender_name = contact_name
        else:
            sender_name, _ = await _get_contact_info(sender_wxid, content, push_content)

        # 微信上打开联系人对话是否新建关联群组
        if msg_type == 51:
            msg_type = "open_chat"

        # 处理消息内容
        if msg_type != 1:
            content = format.xml_to_json(content)
            # App消息
            if msg_type == 49:
                msg_type = int(content['msg']['appmsg']['type'])
            # 通话信息
            if msg_type == 50:
                msg_type = content['voipmsg']['type']
            # 系统信息
            if msg_type == 10002:
                msg_type = content['sysmsg']['type']
        
        # 避免激活折叠聊天时新建群组
        if from_wxid.endswith('@placeholder_foldgroup') or from_wxid == 'notification_messages':
            return

        # 获取或创建群组
        chat_id = await _get_or_create_chat(from_wxid, contact_name, avatar_url)

        # 跳过指定的不明类型消息
        if not chat_id or msg_type in black_list:
            return
        
        # 输出信息便于调试
        if msg_type not in [1, 5, 19, 57]:
            logger.info(f"💬 类型: {msg_type}, 来自: {from_wxid}, 发送者: {sender_wxid}")
            logger.info(f"💬 内容: {content}")

        # 获取联系人信息用于显示
        contact_dic = await contact_manager.get_contact(from_wxid)
        
        # 设置发送者显示名称
        if "chatroom" in from_wxid or contact_dic["isGroup"]:
            sender_name = f"<blockquote expandable>{format.escape_html_chars(sender_name)}</blockquote>"
        else:
            sender_name = ""
        
        # 准备通用参数
        handler_params = {
            'sender_name': sender_name,
            'content': content,
            'msg_id': msg_id,
            'from_wxid': from_wxid,
            'message_info': message_info,
            'msg_type': msg_type
        }
        
        # 发送消息
        response = _send_message_with_handler(chat_id, msg_type, handler_params)
        
        # 检测群组是否被删除
        if response and not response.get('ok', False):
            description = response.get('description', '')
            
            # 检查是否是群组被删除的错误
            if description == "Forbidden: the group chat was deleted":
                logger.warning(f"检测到群组被删除: {from_wxid}, 错误信息: {description}")
                response = await _handle_deleted_group(from_wxid, handler_params, content, push_content, msg_type)
                
                if not response:
                    return
            else:
                # 其他错误类型的处理
                logger.error(f"Telegram API调用失败: {response}")
                return
        
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

async def _get_contact_info(wxid: str, content: dict, push_content: str) -> tuple:
    """获取联系人显示信息，处理特殊情况"""
    # 先读取已保存的联系人
    contact_saved = await contact_manager.get_contact(wxid)
    if contact_saved:
        contact_name = contact_saved["name"]
        avatar_url = contact_saved["avatarLink"]
    
    # 直接利用API获取联系人
    user_info = contact.get_user_info(wxid)
    contact_name = user_info.name
    avatar_url = user_info.avatar_url

    # 企业微信
    if contact_name == "未知用户" and push_content:
        contact_name = push_content.split(" : ")[0]
    if wxid.endswith('@openim'):
        avatar_url = "https://raw.githubusercontent.com/hououinkami/wechat2tg/refs/heads/wx2tg-mac-dev/qywx.jpg"
        if contact_name == "未知用户":
            contact_name = "企业微信"
            
    # 服务通知
    if wxid == "service_notification":
        if isinstance(content, dict):
            contact_name = content.get('msg', {}).get('appmsg', {}).get('mmreader', {}).get('publisher', {}).get('nickname', '')
        else:
            contact_name = ''

    return contact_name, avatar_url

async def _create_group_for_contact(wxid: str, contact_name: str, avatar_url: str = None) -> Optional[int]:
    """异步创建群组"""
    try:
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
    chat_id = await _create_group_for_contact(from_wxid, sender_name, avatar_url)
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
        data_type_map = {
            1: locale.type(1),
            2: locale.type(3),
            4: locale.type(43),
            5: locale.type(5),
            19: locale.type(36)
        }
        data_type = int(item.get('datatype', 0))
        data_type_name = data_type_map.get(data_type, '')

        datadesc = item.get('datadesc') or ""
        
        if data_type == 1:
            datadesc = item.get('datadesc', '')
        elif data_type == 5:
            link = item.get('link', '')
            title = item.get('datatitle', '')
            datadesc = f'<a href="{link}">{title}</a>'
        elif data_type == 19:
            title = item.get('datatitle', '')
            datadesc = f"[{data_type_name}]\n{title}"
        else:
            datadesc = f'[{data_type_name or locale.type("unknown")}]'

        chat_history.append(f"👤{format.escape_html_chars(sourcename)} ({sourcetime})\n{format.escape_html_chars(datadesc)}")

    # 返回格式化后的文本
    chat_history = "\n".join(chat_history)
    return f"<blockquote expandable>{chat_history}</blockquote>"

def parse_time_without_seconds(time_str):
        """解析时间并忽略秒数"""
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
        for temp_file in [silk_path, pcm_path]:
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
    try:
        message_info = extract_message(message_data)
        if not message_info:
            logger.error("提取消息信息失败")
            return
        
        # 忽略微信官方信息
        if message_info["FromUserName"] == "weixin":
            return
        
        message_processor.add_message(message_info)
            
    except Exception as e:
        logger.error(f"消息处理失败: {e}", exc_info=True)

class MessageProcessor:
    def __init__(self):
        self.queue = None
        self.loop = None
        self._shutdown = False
        self._init_async_env()
    
    def _init_async_env(self):
        """在后台线程中初始化异步环境"""
        def run_async():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.queue = Queue(maxsize=1000)
            
            # 启动队列处理器
            self.loop.create_task(self._process_queue())
            logger.info("消息处理器已启动")
            
            # 运行事件循环
            self.loop.run_forever()
        
        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()
        
        # 等待初始化完成
        import time
        for _ in range(50):
            if self.queue:
                break
            time.sleep(0.1)
    
    async def _process_queue(self):
        """处理队列中的消息"""
        while not self._shutdown:
            try:
                # 等待消息
                message = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                
                # 处理消息
                await _process_message_async(message)
                self.queue.task_done()
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"处理消息失败: {e}", exc_info=True)
    
    def add_message(self, message_info: Dict[str, Any]):
        """添加消息到队列"""
        if not self.loop or not self.queue:
            logger.error("处理器未就绪")
            return
        
        # 线程安全地添加消息
        self.loop.call_soon_threadsafe(
            self.queue.put_nowait, message_info
        )

# 全局实例
message_processor = MessageProcessor()
