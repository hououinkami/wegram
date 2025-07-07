import asyncio
import logging
import os
import re
import threading
from asyncio import Queue
from io import BytesIO
from typing import Any, Dict, Optional

import ffmpeg
import pilk
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler

import config
from config import LOCALE as locale
from api import wechat_contacts, wechat_download
from api.telegram_sender import telegram_sender
from service.telethon_client import get_client, get_user_id
from utils import tools
from utils import message_formatter
from utils.contact_manager import contact_manager
from utils.group_manager import group_manager
from utils.message_mapper import msgid_mapping
from utils.telegram_callbacks import create_callback_data
from utils.telegram_to_wechat import get_telethon_msg_id

logger = logging.getLogger(__name__)

tg_user_id = get_user_id()
black_list = ['open_chat', 'bizlivenotify', 'qy_chat_update', 74, 'paymsg']

def _get_message_handlers():
    """返回消息类型处理器映射"""
    return {
        1: _forward_text,
        3: _forward_image,
        34: _forward_voice,
        37: _forward_friend_request,
        42: _forward_contact,
        43: _forward_video,
        47: _forward_sticker,
        48: _forward_location,
        10000: _forward_text,
        4: _forward_app_message,
        5: _forward_link,
        6: _forward_file,
        19: _forward_chat_history,
        33: _forward_miniprogram,
        36: _forward_miniprogram,
        51: _forward_channel,
        53: _forward_groupnote,
        57: _forward_quote,
        66: _forward_wecom_contact,
        2000: _forward_transfer,
        "revokemsg": _forward_revoke,
        "pat": _forward_pat,
        "ilinkvoip": _forward_voip,
        "VoIPBubbleMsg": _forward_voip,
        "sysmsgtemplate": _forward_sysmsg
    }

async def _forward_text(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: str, **kwargs) -> dict:
    """处理文本消息"""
    if msg_type == 10000:
        sender_name = ""
        content = f"<blockquote>{content}</blockquote>"

        # 更新群信息
        if from_wxid.endswith("@chatroom"):
            group_manager.update_group_member(from_wxid)

    send_text = f"{sender_name}\n{content}"
    
    return await telegram_sender.send_text(chat_id, send_text)

async def _forward_image(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理图片消息"""
    # 异步下载图片
    success, file, _ = await wechat_download.get_image(msg_id, from_wxid, content)
    
    if success:
        return await telegram_sender.send_photo(chat_id, file, sender_name)
    else:
        raise Exception("图片下载失败")

async def _forward_voice(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, msg_id: str, message_info: dict, **kwargs) -> dict:
    """处理语音消息"""
    success, file = await wechat_download.get_voice(msg_id, message_info['FromUserName'], content)

    if not success:
        raise Exception("语音下载失败")
        
    loop = asyncio.get_event_loop()
    ogg_path, duration = await loop.run_in_executor(None, silk_to_voice, file)
    if not ogg_path or not duration:
        raise Exception("语音转换失败")
    
    return await telegram_sender.send_voice(chat_id, ogg_path, sender_name, duration)

async def _forward_friend_request(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理好友添加"""
    friend_msg = content.get('msg', {})
    from_nickname = friend_msg.get('fromnickname') or friend_msg.get('nickName') or ''
    from_wxid = friend_msg.get('fromusername', '')
    encrypt_username = friend_msg.get('encryptusername') or friend_msg.get('v3') or ''
    ticket = friend_msg.get('ticket') or friend_msg.get('v4') or ''
    avatar_url = friend_msg.get('bigheadimgurl') or friend_msg.get('BigHeadImgUrl') or friend_msg.get('bigHeadImgUrl') or friend_msg.get('smallheadimgurl') or friend_msg.get('SmallHeadImgUrl') or friend_msg.get('smallHeadImgUrl') or ''
    content = friend_msg.get('content', '')
    scene = friend_msg.get('scene')

    # 准备回调数据
    callback_data = {
        'Scene': int(scene),
        'V1': encrypt_username,
        'V2': ticket,
        'Wxid': config.MY_WXID,
        **kwargs
    }

    if avatar_url:
        processed_photo_content = await tools.get_image_from_url(avatar_url)

    keyboard = [
        [InlineKeyboardButton(
            f"{locale.common('agree_accept')}", 
            callback_data=create_callback_data("agree_accept", callback_data)
        )]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    send_text = f"<blockquote>{locale.type(msg_type)}: {from_nickname}</blockquote>\n{content}"
    return await telegram_sender.send_photo(tg_user_id, processed_photo_content, send_text, reply_markup=reply_markup)

async def _forward_contact(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理名片信息"""
    contact_msg = content.get('msg', {})
    contact_nickname = contact_msg.get('nickname', '')
    contact_username = contact_msg.get('username', '')
    contact_ticket = contact_msg.get('antispamticket', '')
    contact_avatar = contact_msg.get('bigheadimgurl') or contact_msg.get('smallheadimgurl') or ''
    scene = int(contact_msg.get('scene')) or 0

    # 已经是好友
    if not contact_ticket:
        button_text = locale.common('user_added')
    else:
        button_text = locale.common('add_contact')
    
    # 准备回调数据
    callback_data = {
        "Opcode": 2,
        "Scene": scene,
        "V1": contact_username,
        "V2": contact_ticket,
        "VerifyContent": "",
        "Wxid": config.MY_WXID
    }

    if contact_avatar:
        processed_photo_content = await tools.get_image_from_url(contact_avatar)

    keyboard = [
        [InlineKeyboardButton(
            f"{button_text}", 
            callback_data=create_callback_data("add_contact", callback_data)
        )]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    send_text = f"{sender_name}\n<blockquote>[{locale.type(msg_type)}]{contact_nickname}</blockquote>"
    return await telegram_sender.send_photo(chat_id, processed_photo_content, send_text, reply_markup=reply_markup)

async def _forward_video(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, msg_id: str, **kwargs) -> dict:
    """处理视频消息"""
    success, file, _ = await wechat_download.get_video(msg_id, from_wxid, content)
    if success:
        return await telegram_sender.send_video(chat_id, file, sender_name)
    else:
        raise Exception("视频下载失败")

async def _forward_sticker(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理贴纸消息"""
    success, file = await wechat_download.get_emoji(content)
    
    if success:
        return await telegram_sender.send_animation(chat_id, file, sender_name, filename=f"[{locale.type(msg_type)}].gif")
    else:
        raise Exception("贴纸下载失败")

async def _forward_location(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理定位"""
    try:
        location = content.get('msg', {}).get('location', {})
        latitude = float(location.get('x'))
        longitude = float(location.get('y'))
        label = location.get('label', '')
        poiname = location.get('poiname', '')
        
        return await telegram_sender.send_location(chat_id, latitude, longitude, poiname, label)
    except (KeyError, TypeError) as e:
        raise Exception("定位信息提取失败")

async def _forward_app_message(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理App消息"""
    app_msg = content.get('msg', {}).get('appmsg', {})
    app_title = app_msg.get('title', '')
    app_des = app_msg.get('des', '')
    app_url = app_msg.get('url', '')
    app_name = app_msg.get('appinfo', {}).get('appname', '')
    if app_name:
        app = f"[{app_name}]"
    else:
        app = ""

    send_text = f'{sender_name}\n<a href="{app_url}">{app}{app_title}</a>\n<blockquote>{app_des}</blockquote>'
    
    return await telegram_sender.send_text(chat_id, send_text)

async def _forward_link(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理公众号消息"""
    url_items, main_cover_url = message_formatter.extract_url_items(content)

    send_text = f"{sender_name}\n{url_items}"
    
    if main_cover_url:
        main_cover = await tools.get_image_from_url(main_cover_url)
        return await telegram_sender.send_photo(chat_id, main_cover, send_text)
    else:
        return await telegram_sender.send_text(chat_id, send_text)

async def _forward_file(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, msg_id: str, **kwargs) -> dict:
    """处理文件消息"""
    success, file, filename = await wechat_download.get_file(msg_id, from_wxid, content)
    
    if success:
        return await telegram_sender.send_document(chat_id, file, sender_name, filename=filename)
    else:
        raise Exception("文件下载失败")

async def _forward_chat_history(chat_id: int, sender_name: str, content: dict, **kwargs) -> dict:
    """处理聊天记录消息"""
    loop = asyncio.get_event_loop()
    chat_history = await loop.run_in_executor(None, process_chathistory, content)
    
    if chat_history:
        send_text = f"{sender_name}\n{chat_history}"
        return await telegram_sender.send_text(chat_id, send_text)
    else:
        raise Exception("聊天记录处理失败")

async def _forward_miniprogram(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理小程序消息"""
    mini_name = content.get('msg', {}).get('appmsg', {}).get('sourcedisplayname', '')
    mini_title = content.get('msg', {}).get('appmsg', {}).get('title', '')
    send_text = f"{sender_name}\n<blockquote>[{locale.type(msg_type)}: {mini_name}]</blockquote>\n{mini_title}"
    
    return await telegram_sender.send_text(chat_id, send_text)

async def _forward_channel(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理视频号"""
    try:
        finder_feed = content.get("msg", {}).get("appmsg", {}).get("finderFeed", {})
        channel_name = finder_feed["nickname"]
        channel_title = finder_feed["desc"]
        channel_content = f"<blockquote>[{locale.type(msg_type)}: {channel_name}]</blockquote>\n{channel_title}"
        send_text = f"{sender_name}\n{channel_content}"
        
        return await telegram_sender.send_text(chat_id, send_text)
    except (KeyError, TypeError) as e:
        raise Exception("视频号信息提取失败")

async def _forward_groupnote(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理群接龙"""
    try:
        groupnote_title = content.get('msg', {}).get('appmsg', {}).get('title', '')
        groupnote_content = f"[{locale.type(msg_type)}]\n{groupnote_title}"
        send_text = f"{sender_name}\n<blockquote expandable>{groupnote_content}</blockquote>"
        
        return await telegram_sender.send_text(chat_id, send_text)
    except (KeyError, TypeError) as e:
        raise Exception("群接龙信息提取失败")

async def _forward_quote(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理引用消息"""
    text = content["msg"]["appmsg"]["title"]
    quote = content["msg"]["appmsg"]["refermsg"]
    quote_newmsgid = quote["svrid"]
    
    quote_tgmsgid = msgid_mapping.wx_to_tg(quote_newmsgid) or 0 if quote_newmsgid else 0
    send_text = f"{sender_name}\n{text}"
    
    return await telegram_sender.send_text(chat_id, send_text, reply_to_message_id=quote_tgmsgid)

async def _forward_wecom_contact(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理企业微信名片信息"""
    contact_msg = content.get('msg', {})
    contact_nickname = contact_msg.get('nickname', '')
    contact_username = contact_msg.get('username', '')
    contact_company = contact_msg.get('openimdesc', '')
    contact_ticket = contact_msg.get('ticket', '')
    contact_avatar = contact_msg.get('bigheadimgurl') or contact_msg.get('smallheadimgurl') or ''
    
    # 准备回调数据
    callback_data = {
        "Username": contact_username,
        "V1": contact_ticket,
        "Wxid": config.MY_WXID
    }

    if contact_avatar:
        processed_photo_content = await tools.get_image_from_url(contact_avatar)

    keyboard = [
        [InlineKeyboardButton(
            locale.common('add_contact'), 
            callback_data=create_callback_data("add_wecom_contact", callback_data)
        )]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    send_text = f"{sender_name}\n<blockquote>[{locale.type(msg_type)}]{contact_nickname}@{contact_company}</blockquote>"
    return await telegram_sender.send_photo(chat_id, processed_photo_content, send_text, reply_markup=reply_markup)

async def _forward_transfer(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理转账"""
    try:
        transfer_info = content.get('msg', {}).get('appmsg', {}).get('wcpayinfo', {})
        transfer_money = transfer_info.get('feedesc')
        transfer_type = transfer_info.get('paysubtype')

        if transfer_type == 1:
            transfer_title = f"[{locale.type(msg_type)}]"
        elif transfer_type == 3:
            transfer_title = f"[{locale.type(msg_type)}{locale.common('transfer_out')}]"
        else:
            transfer_title = f"[{locale.type(msg_type)}]"

        transfer_content = f"<blockquote>{transfer_title}</blockquote>\n{transfer_money}"
        send_text = f"{sender_name}\n{transfer_content}"
        
        return await telegram_sender.send_text(chat_id, send_text)
    except (KeyError, TypeError) as e:
        raise Exception("转账信息提取失败")

async def _forward_revoke(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理撤回消息"""
    revoke_msg = content["sysmsg"]["revokemsg"]
    revoke_text = revoke_msg["replacemsg"]
    quote_newmsgid = revoke_msg["newmsgid"]

    quote_tgmsgid = msgid_mapping.wx_to_tg(quote_newmsgid) or 0 if quote_newmsgid else 0
    send_text = f"{sender_name}\n<blockquote>{revoke_text}</blockquote>"
    
    return await telegram_sender.send_text(chat_id, send_text, reply_to_message_id=quote_tgmsgid)

async def _forward_pat(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理拍一拍消息"""
    pat_msg = content["sysmsg"]["pat"]
    pat_template = pat_msg["template"]
    pattern = r'\$\{([^}]+)\}'

    # 处理模板中的用户信息替换
    matches = re.findall(pattern, pat_template)
    result = pat_template
    for match in matches:
        if not from_wxid.endswith('@chatroom'):
            user_info = await wechat_contacts.get_user_info(match)
            user_name = user_info.name
        else:
            user_name = await _get_sender_info(from_wxid, match)
        result = result.replace(f"${{{match}}}", user_name)
    
    send_text = f"{sender_name}\n<blockquote>{result}</blockquote>"
    
    return await telegram_sender.send_text(chat_id, send_text)

async def _forward_voip(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理通话消息"""
    if msg_type == "ilinkvoip":
        voip_invite = content.get('sysmsg', {}).get('voipmt', {}).get('invite', "")
        voip_cancle = content.get('sysmsg', {}).get('voipmt', {}).get('cancel', "")
        voip_miss = content.get('sysmsg', {}).get('voipmt', {}).get('dismissapns', "")
        if voip_invite:
            voip_msg = locale.type('ilinkvoip')
        else:
            return

    if msg_type == "VoIPBubbleMsg":
        voip_msg = content["voipmsg"]["VoIPBubbleMsg"]["msg"]
    
    send_text = f"{sender_name}\n<blockquote>{voip_msg}</blockquote>"
    
    return await telegram_sender.send_text(chat_id, send_text)

async def _forward_sysmsg(chat_id: int, msg_type: int, from_wxid: str, sender_name: str, content: dict, **kwargs) -> dict:
    """处理加入群聊消息"""
    try:
        content_template = content.get('sysmsg', {}).get('sysmsgtemplate', {}).get('content_template', {})
        template = content_template.get('template', "")
        link_list = content_template.get('link_list', {}).get('link', [])

        variable_mapping = {}
        for link in link_list:
            var_name = link['name']
            nickname = link['memberlist']['member']['nickname']
            variable_mapping[var_name] = nickname
        
        # 替换template中的变量
        result_template = template
        for var_name, nickname in variable_mapping.items():
            placeholder = f"${var_name}$"
            result_template = result_template.replace(placeholder, nickname)
        
        send_text = f"<blockquote>{result_template}</blockquote>"

        return await telegram_sender.send_text(chat_id, send_text)
    except (KeyError, TypeError) as e:
        raise Exception("加入群聊信息提取失败")

async def _get_contact_info(wxid: str, content: dict, push_content: str) -> tuple:
    """获取联系人显示信息，处理特殊情况"""
    # 先读取已保存的联系人
    contact_saved = await contact_manager.get_contact(wxid)
    if contact_saved:
        contact_name = contact_saved["name"]
        avatar_url = contact_saved["avatarLink"]
    else:
        # 异步获取联系人信息
        user_info = await wechat_contacts.get_user_info(wxid)
        contact_name = user_info.name
        avatar_url = user_info.avatar_url

    # 从推送内容获取用户名称
    if (contact_name == "未知用户" or contact_name == "企业微信") and push_content:
        contact_name = push_content.split(" : ")[0].split("さん")[0]
            
    # 服务通知
    if wxid == "service_notification":
        contact_name = (
            content.get('msg', {}).get('appinfo', {}).get('appname') or 
            content.get('msg', {}).get('appmsg', {}).get('mmreader', {}).get('publisher', {}).get('nickname') or 
            content.get('msg', {}).get('appmsg', {}).get('mmreader', {}).get('category', {}).get('name') or 
            content.get('msg', {}).get('appmsg', {}).get('mmreader', {}).get('category', {}).get('item', {}).get('sources', {}).get('source', {}).get('name') or
            ''
        )

    return contact_name, avatar_url

async def _get_sender_info(from_wxid: str, sender_wxid: str, contact_name: str = "") -> str:
    if sender_wxid == from_wxid:    # 私聊
        sender_name = contact_name
    else:   # 群聊
        contact_saved = await contact_manager.get_contact(sender_wxid)
        if contact_saved:
            sender_name = contact_saved["name"]
        else:
            sender_name = await group_manager.get_display_name(from_wxid, sender_wxid)
            if not sender_name:
                    sender_name = "未知用户"

    return sender_name

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

async def _get_or_create_chat(from_wxid: str, sender_name: str, avatar_url: str, message_for_log = None) -> Optional[int]:
    """获取或创建聊天群组"""
    # 读取contact映射
    contact_dic = await contact_manager.get_contact(from_wxid)
    
    if contact_dic and not contact_dic["isReceive"]:
        return None
        
    if contact_dic and contact_dic["isReceive"]:
        return contact_dic["chatId"]
    
    # 检查是否允许自动创建群组
    auto_create = getattr(config, 'AUTO_CREATE_GROUPS', True)

    # 指定不创建群组的情况
    if not auto_create or from_wxid == config.MY_WXID:
        return None
    
    # 创建群组
    logger.warning(f"触发新建群组：{message_for_log}")
    chat_id = await _create_group_for_contact(from_wxid, sender_name, avatar_url)
    if not chat_id:
        logger.warning(f"无法创建聊天群组: {from_wxid}")
        return None
    
    return chat_id

# 处理聊天记录 - 保持同步，因为主要是数据处理
def process_chathistory(content):
    chat_data = message_formatter.xml_to_json(content["msg"]["appmsg"]["recorditem"])
    chat_json = chat_data["recordinfo"]
    
    # 提取标题和件数
    title = content["msg"]["appmsg"]['title']
    count = chat_json['datalist']['count']
    
    # 提取所有 sourcetime 并转换为 datetime 对象
    data_items = chat_json['datalist']['dataitem']

    # 确保data_items是列表
    if not isinstance(data_items, list):
        data_items = [data_items]  # 单个项目包装成列表
        
    sourcetimes_dt = [tools.parse_time_without_seconds(item['sourcetime']) for item in data_items]
    
    # 确定日期范围
    start_date = sourcetimes_dt[0].strftime("%Y/%m/%d")
    end_date = sourcetimes_dt[-1].strftime("%Y/%m/%d")
    date_range = f"{start_date} ～ {end_date}" if start_date != end_date else start_date

    # 构建聊天记录文本
    chat_history = [f"{title}\n件数：{count}\n日期：{date_range}"]
    
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

        chat_history.append(f"👤{sourcename} ({sourcetime})\n{datadesc}")

    # 返回格式化后的文本
    chat_history = "\n".join(chat_history)
    return f"<blockquote expandable>{chat_history}</blockquote>"

def silk_to_voice(silk_path):
    """转换微信语音为Telegram语音 - 保持同步，因为是CPU密集型任务"""
    pcm_path = silk_path + '.pcm'
    
    try:
        # silk -> pcm
        duration = pilk.decode(silk_path, pcm_path)
        
        # pcm -> ogg opus
        process = (
            ffmpeg
            .input(pcm_path, format='s16le', acodec='pcm_s16le', ar=24000, ac=1)
            .output('pipe:', acodec='libopus', audio_bitrate='64k', format='ogg')
            .run_async(pipe_stdout=True, pipe_stderr=True, quiet=True)
        )
        
        # 获取输出数据
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            raise Exception(f"FFmpeg转换失败: {stderr.decode() if stderr else 'Unknown error'}")
        
        # 创建BytesIO对象
        audio_buffer = BytesIO(stdout)
        audio_buffer.seek(0)  # 重置指针到开头
        
        return audio_buffer, int(duration)
            
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

def silk_to_voice_file(silk_path):
    """转换微信语音为Telegram语音 - 保持同步，因为是CPU密集型任务"""
    pcm_path = silk_path + '.pcm'
    ogg_path = silk_path + '.ogg'
    
    try:
        # silk -> pcm
        duration = pilk.decode(silk_path, pcm_path)
        
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

# 提取回调信息 - 保持同步，纯数据处理
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

async def _process_message_async(message_info: Dict[str, Any]) -> None:
    """异步处理单条消息"""

    async def _send_message_with_handler(chat_id: int, msg_type: Any, handler_params: dict) -> dict:
        """使用处理器发送消息的通用方法"""
        handlers = _get_message_handlers()
        
        if msg_type in handlers:
            try:
                return await handlers[msg_type](**{**handler_params, 'chat_id': chat_id})
            except Exception as e:
                logger.error(f"处理器执行失败 (类型={msg_type}): {e}", exc_info=True)
                type_text = f"[{locale.type(msg_type)}]"
                send_text = f"{handler_params['sender_name']}\n{type_text}"
                return await telegram_sender.send_text(chat_id, send_text)
        else:
            # 处理未知消息类型
            logger.warning(f"❓未知消息类型: {msg_type}")
            type_text = f'[{locale.type(msg_type) or locale.type("unknown")}]'
            send_text = f"{handler_params['sender_name']}\n{type_text}"

            # 调试输出
            logger.info(f"💬 类型: {msg_type}, 来自: {handler_params['from_wxid']}")
            logger.info(f"💬 内容: {handler_params['content']}")
            
            return await telegram_sender.send_text(chat_id, send_text)
    
    async def _handle_deleted_group(from_wxid: str, handler_params: dict, content: dict, push_content: str, msg_type: Any) -> Optional[dict]:
        """处理被删除的群组"""
        try:
            # 删除联系人信息
            await contact_manager.delete_contact(from_wxid)
            
            # 重新获取或创建聊天群组
            contact_name, avatar_url = await _get_contact_info(from_wxid, content, push_content)
            
            # 创建新群组
            new_chat_id = await _create_group_for_contact(from_wxid, contact_name, avatar_url)
            
            if new_chat_id:
                # 重新发送消息
                return await _send_message_with_handler(new_chat_id, msg_type, handler_params)
            else:
                logger.error(f"群组重新创建失败: {from_wxid}")
                return None
                
        except Exception as e:
            logger.error(f"处理群组删除异常: {e}", exc_info=True)
            return None

    try:
        # ========== 消息基础信息解析 ==========
        msg_type = int(message_info['MsgType'])
        msg_id = message_info['MsgId']
        new_msg_id = message_info['NewMsgId']
        from_wxid = message_info['FromUserName']
        to_wxid = message_info['ToUserName']
        content = message_info['Content']
        push_content = message_info['PushContent']
        create_time = message_info['CreateTime']
        
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

        # 转发自己的消息
        if from_wxid == config.MY_WXID:
            from_wxid = to_wxid
        
        # ========== 特殊消息类型处理 ==========
        # 微信上打开联系人对话
        if msg_type == 51:
            msg_type = "open_chat"
        
        # 处理非文本消息
        if msg_type != 1 and msg_type != 10000:
            content = message_formatter.xml_to_json(content)
            if msg_type == 49:  # App消息
                msg_type = int(content['msg']['appmsg']['type'])
            elif msg_type == 50:  # 通话信息
                msg_type = content['voipmsg']['type']
            elif msg_type == 10002:  # 系统信息
                msg_type = content['sysmsg']['type']

        # ========== 早期过滤不需要处理的消息 ==========
        if (from_wxid.endswith('@placeholder_foldgroup') or # 激活折叠聊天
            from_wxid == 'notification_messages' or # 系统通知
            msg_type in black_list or # 黑名单类型
            (sender_wxid == config.MY_WXID and msg_type == "revokemsg")): # 自己撤回的消息
            return

        # ========== 获取联系人和发送者信息 ==========
        # 获取联系人信息
        contact_name, avatar_url = await _get_contact_info(from_wxid, content, push_content)

        # 获取发送者信息
        sender_name = await _get_sender_info(from_wxid, sender_wxid, contact_name)

        # 获取或创建群组
        chat_id = await _get_or_create_chat(from_wxid, contact_name, avatar_url, message_info)
        if not chat_id:
            return
        
        # ========== 设置发送者显示格式 ==========
        # 获取联系人信息用于显示
        contact_dic = await contact_manager.get_contact(from_wxid)
        
        # 设置发送者显示名称
        if "chatroom" in from_wxid or contact_dic["isGroup"]:
            sender_name = f"<blockquote expandable>{sender_name}: </blockquote>"
        else:
            sender_name = ""
        
        # 调试输出未知类型消息
        types_keys = [k for k in locale.type_map.keys()]
        if msg_type not in types_keys:
            logger.info(f"💬 类型: {msg_type}, 来自: {from_wxid}, 发送者: {sender_wxid}")
            logger.info(f"💬 内容: {content}")

        # ========== 准备发送参数并发送消息 ==========
        # 准备通用参数
        handler_params = {
            'msg_type': msg_type,
            'msg_id': msg_id,
            'from_wxid': from_wxid,
            'sender_name': sender_name,
            'content': content,
            'push_content': push_content,
            'message_info': message_info
        }
        
        # 发送消息并处理响应
        response = await _send_message_with_handler(chat_id, msg_type, from_wxid, sender_name, content, handler_params)

        # ========== 存储消息ID映射 ==========
        if response and not from_wxid.startswith('gh_'):
            tg_msgid = response.message_id

            # 获取Telethon消息ID
            telethon_msg_id = 0
            if config.TG_MODE == "telethon":
                try:
                    message_text = response.text if response.text else ""
                    bot_id = int(config.BOT_TOKEN.split(':')[0])
                    telethon_client = get_client()
                    telethon_msg_id = await get_telethon_msg_id(
                        telethon_client, abs(int(chat_id)), bot_id, message_text, response.date
                    )
                except Exception as e:
                    logger.error(f"获取Telethon消息ID失败: {e}")

            msgid_mapping.add(
                tg_msg_id=tg_msgid,
                from_wx_id=sender_wxid,
                to_wx_id=to_wxid,
                wx_msg_id=new_msg_id,
                client_msg_id=0,
                create_time=create_time,
                content=content if msg_type == 1 else f"[{locale.type(msg_type)}]",
                telethon_msg_id=telethon_msg_id
            )
    except TelegramError as e:
        # ========== Telegram错误处理 ==========
        error_msg = str(e).lower()

        # 群组删除相关错误
        group_deleted_keywords = [
            "the group chat was deleted", 
            "chat not found", 
            "group chat was deactivated",
            "bot was kicked", 
            "not a member"
        ]
        
        if any(keyword in error_msg for keyword in group_deleted_keywords):
            if "bot was kicked" in error_msg or "not a member" in error_msg:
                logger.warning(f"Bot被踢出群组或不是成员: {from_wxid}, 错误信息: {e}")
            else:
                logger.warning(f"检测到群组被删除: {from_wxid}, 错误信息: {e}")
            
            response = await _handle_deleted_group(from_wxid, handler_params, content, push_content, msg_type)
            if not response:
                return
        else:
            logger.error(f"Telegram API调用失败: {e}")
            return
                
    except Exception as e:
        logger.error(f"异步消息处理失败: {e}", exc_info=True)

async def process_rabbitmq_message(message_data: Dict[str, Any]) -> None:
    """处理微信RabbitMQ消息"""
    try:
        message_info = extract_message(message_data)
        if not message_info:
            logger.error("提取消息信息失败")
            return
        
        # 忽略微信官方信息
        if message_info["FromUserName"] == "weixin":
            return
        
        # 直接调用核心处理函数
        await _process_message_async(message_info)
        
        return True
            
    except Exception as e:
        logger.error(f"处理微信消息时出错: {e}")

async def process_callback_message(message_data: Dict[str, Any]) -> None:
    """处理微信回调消息"""
    try:
        message_info = extract_message(message_data)
        if not message_info:
            logger.error("提取消息信息失败")
            return
        
        # 忽略微信官方信息
        if message_info["FromUserName"] == "weixin":
            return
        
        await message_processor.add_message_async(message_info)
            
    except Exception as e:
        logger.error(f"消息处理失败: {e}", exc_info=True)

class MessageProcessor:
    def __init__(self):
        self.queue = None
        self.loop = None
        self._shutdown = False
        self._task = None
        self._init_complete = asyncio.Event()
        self._init_async_env()
    
    def _init_async_env(self):
        """在后台线程中初始化异步环境"""       
        def run_async():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.queue = Queue(maxsize=1000)
            
            # 启动队列处理器
            self._task = self.loop.create_task(self._process_queue())
            logger.info("消息处理器已启动")
            
            # 标记初始化完成
            self.loop.call_soon_threadsafe(self._init_complete.set)
            
            # 运行事件循环
            try:
                self.loop.run_forever()
            except Exception as e:
                logger.error(f"消息处理器事件循环异常: {e}")
        
        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()
    
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
        """添加消息到队列 - 同步版本（兼容性）"""
        if not self.loop or not self.queue:
            logger.error("处理器未就绪")
            return
        
        # 线程安全地添加消息
        try:
            self.loop.call_soon_threadsafe(
                self.queue.put_nowait, message_info
            )
        except Exception as e:
            logger.error(f"添加消息到队列失败: {e}")
    
    async def add_message_async(self, message_info: Dict[str, Any]):
        """添加消息到队列"""
        # 等待初始化完成
        if not self._init_complete.is_set():
            await asyncio.wait_for(self._init_complete.wait(), timeout=5.0)
        
        if not self.queue:
            logger.error("处理器未就绪")
            return
        
        try:
            # 如果在同一个事件循环中，直接添加
            if asyncio.get_event_loop() == self.loop:
                await self.queue.put(message_info)
            else:
                # 跨线程调用
                future = asyncio.run_coroutine_threadsafe(
                    self.queue.put(message_info), self.loop
                )
                await asyncio.wrap_future(future)
        except Exception as e:
            logger.error(f"异步添加消息到队列失败: {e}")
    
    async def shutdown(self):
        """优雅关闭处理器"""
        logger.info("正在关闭消息处理器...")
        self._shutdown = True
        
        if self.queue:
            # 等待队列处理完成
            try:
                await asyncio.wait_for(self.queue.join(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("等待队列处理完成超时")
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        
        logger.info("消息处理器已关闭")
    
    def get_queue_size(self) -> int:
        """获取队列大小"""
        if self.queue:
            return self.queue.qsize()
        return 0

# 全局实例
message_processor = MessageProcessor()

# 为了向后兼容，保留原有的同步接口
def add_message_sync(message_info: Dict[str, Any]):
    """同步添加消息接口（向后兼容）"""
    message_processor.add_message(message_info)

# 优雅关闭函数
async def shutdown_message_processor():
    """关闭消息处理器"""
    await message_processor.shutdown()

# 获取处理器状态
def get_processor_status() -> Dict[str, Any]:
    """获取处理器状态"""
    return {
        "queue_size": message_processor.get_queue_size(),
        "is_running": message_processor.loop is not None and message_processor.loop.is_running(),
        "is_shutdown": message_processor._shutdown
    }