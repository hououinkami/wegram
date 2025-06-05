#!/usr/bin/env python3
"""
独立的Telegram群组创建模块
复用监控服务中的客户端和bot实例
"""

import logging
import os
import json
import asyncio
import requests
import tempfile
from typing import Dict, Optional
from telethon.tl.functions.messages import CreateChatRequest, EditChatAdminRequest, EditChatPhotoRequest
from telethon.tl.types import InputChatUploadedPhoto
import config

# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

class TelegramGroupCreator:
    def __init__(self):
        # 从配置获取参数
        self.api_id = config.API_ID
        self.api_hash = config.API_HASH
        self.phone_number = config.PHONE_NUMBER
        self.target_folder_name = getattr(config, 'WECHAT_FOLDER_NAME', None)
    
    async def _download_image_from_url(self, url: str) -> Optional[str]:
        """从URL下载图片到临时文件"""
        try:
            logger.info(f"开始下载图片: {url}")
            
            # 设置请求头，模拟浏览器
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            # 在线程池中执行同步请求
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: requests.get(url, headers=headers, timeout=30))
            response.raise_for_status()
            
            # 获取文件扩展名
            content_type = response.headers.get('content-type', '').lower()
            if 'image/jpeg' in content_type or 'image/jpg' in content_type:
                ext = '.jpg'
            elif 'image/png' in content_type:
                ext = '.png'
            elif 'image/webp' in content_type:
                ext = '.webp'
            elif 'image/gif' in content_type:
                ext = '.gif'
            else:
                # 尝试从URL中获取扩展名
                url_lower = url.lower()
                if url_lower.endswith(('.jpg', '.jpeg')):
                    ext = '.jpg'
                elif url_lower.endswith('.png'):
                    ext = '.png'
                elif url_lower.endswith('.webp'):
                    ext = '.webp'
                elif url_lower.endswith('.gif'):
                    ext = '.gif'
                else:
                    ext = '.jpg'  # 默认使用jpg
            
            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_file:
                temp_file.write(response.content)
                logger.info(f"成功下载图片到: {temp_file.name}, 大小: {len(response.content)} bytes")
                return temp_file.name
                
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
            return None
    
    async def _set_group_avatar(self, client, chat_id: int, avatar_url: str) -> bool:
        """设置群组头像"""
        if not avatar_url:
            return True
        
        try:
            logger.info(f"开始设置群组头像: {avatar_url}")
            
            temp_image_path = await self._download_image_from_url(avatar_url)
            
            if not temp_image_path:
                logger.error("下载头像图片失败")
                return False
            
            try:
                # 上传图片并设置为群组头像
                # 对于普通群组，使用 EditChatPhotoRequest
                if chat_id < 0:  # 普通群组ID是负数
                    original_chat_id = abs(chat_id)
                    uploaded_photo = await client.upload_file(temp_image_path)
                    await client(EditChatPhotoRequest(
                        chat_id=original_chat_id,
                        photo=InputChatUploadedPhoto(uploaded_photo)
                    ))
                    logger.info(f"成功设置群组头像")
                
                return True
                
            finally:
                # 清理临时文件
                try:
                    os.unlink(temp_image_path)
                    logger.info(f"已清理临时文件: {temp_image_path}")
                except Exception as e:
                    logger.error(f"清理临时文件失败: {e}")
                    
        except Exception as e:
            logger.error(f"设置群组头像失败: {e}")
            return False
    
    async def _save_chat_wxid_mapping(self, wxid: str, name: str, chat_id: int, avatar_url: str = None):
        """保存群组ID和微信ID的映射关系到contact.json"""
        # 判断是否为群聊
        is_group = wxid.endswith('@chatroom')
        
        try:
            # 获取contact.json路径
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            contact_json_path = os.path.join(parent_dir, 'contact.json')
            
            # 在线程池中执行文件I/O操作
            loop = asyncio.get_event_loop()
            
            def _read_contacts():
                contacts = []
                if os.path.exists(contact_json_path):
                    with open(contact_json_path, 'r', encoding='utf-8') as f:
                        contacts = json.load(f)
                return contacts
            
            def _write_contacts(contacts):
                with open(contact_json_path, 'w', encoding='utf-8') as f:
                    json.dump(contacts, f, ensure_ascii=False, indent=4)
            
            # 异步读取现有映射
            contacts = await loop.run_in_executor(None, _read_contacts)
            
            # 检查是否已存在该映射
            for contact in contacts:
                if contact.get('wxId') == wxid and contact.get('chatId') == chat_id:
                    logger.info(f"映射已存在: {wxid} -> {chat_id}")
                    return
            
            # 添加新映射
            new_contact = {
                "name": name,
                "wxId": wxid,
                "chatId": chat_id,
                "isGroup": is_group,
                "isReceive": True,
                "alias": "",
                "avatarLink": avatar_url
            }
            
            contacts.append(new_contact)
            
            # 异步保存映射
            await loop.run_in_executor(None, _write_contacts, contacts)
                
            logger.info(f"已保存映射: {wxid} -> {chat_id}")
            
        except Exception as e:
            logger.error(f"保存映射关系失败: {e}")
            raise e
    
    async def _check_existing_mapping(self, wxid: str) -> Optional[Dict]:
        """检查是否已有映射"""
        try:
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            contact_json_path = os.path.join(parent_dir, 'contact.json')
            
            # 在线程池中执行文件I/O操作
            loop = asyncio.get_event_loop()
            
            def _read_and_check():
                if os.path.exists(contact_json_path):
                    with open(contact_json_path, 'r', encoding='utf-8') as f:
                        contacts = json.load(f)
                        
                    # 检查是否已经有该微信ID的映射
                    for contact in contacts:
                        if contact.get('wxId') == wxid and contact.get('chatId'):
                            return contact
                return None
            
            return await loop.run_in_executor(None, _read_and_check)
            
        except Exception as e:
            logger.error(f"检查映射失败: {e}")
            return None
    
    async def create_group_with_monitor(self, monitor_instance, wxid: str, contact_name: str, 
                                        description: str = "", avatar_url: str = None) -> Optional[Dict]:
        """使用监控服务的客户端创建群组"""
        try:
            # 检查监控服务状态
            if not monitor_instance.client or not monitor_instance.client.is_connected():
                logger.error("监控服务客户端未连接")
                return {'success': False, 'error': '监控服务客户端未连接'}
            
            if not monitor_instance.bot_entity:
                logger.error("Bot实体未初始化")
                return {'success': False, 'error': 'Bot实体未初始化'}
            
            # 检查是否已经有群组映射
            existing_contact = await self._check_existing_mapping(wxid)
            
            if existing_contact:
                logger.info(f"该微信ID {wxid} 已有群组映射，群组ID: {existing_contact.get('chatId')}")
                return {
                    'success': True, 
                    'chat_id': existing_contact.get('chatId'), 
                    'group_name': existing_contact.get('name'),
                    'group_type': 'group', 
                    'bot_invited': True, 
                    'bot_is_admin': True,
                    'avatar_set': True,
                    'already_exists': True
                }
            
            # 如果没有映射，创建新群组
            group_name = f"{contact_name}"
            client = monitor_instance.client
            bot_entity = monitor_instance.bot_entity
            
            logger.info(f"开始创建群组: {group_name}")
            
            # 创建普通群组
            result = await client(CreateChatRequest(
                users=[bot_entity], 
                title=group_name
            ))
            
            logger.info(f"创建群组返回结果类型: {type(result)}")
            chat_id = None
            
            # 获取群组ID
            if hasattr(result, 'chats') and result.chats:
                chat = result.chats[0]
                chat_id = -chat.id
            elif hasattr(result, 'updates') and hasattr(result.updates, 'chats') and result.updates.chats:
                chat = result.updates.chats[0]
                chat_id = -chat.id
            
            # 如果还是获取不到，通过对话列表查找
            if chat_id is None:
                await asyncio.sleep(1)
                dialogs = await client.get_dialogs(limit=20)
                for dialog in dialogs:
                    if (dialog.title == group_name and 
                        dialog.is_group and 
                        not dialog.is_channel):
                        chat_id = dialog.id
                        break
            
            if chat_id is None:
                logger.error(f"无法获取创建的群组ID")
                return {'success': False, 'error': "无法获取创建的群组ID"}
            
            logger.info(f"成功创建普通群组，ID: {chat_id}")
            
            # 设置 bot 为管理员
            bot_is_admin = False
            try:
                logger.info(f"尝试设置 bot 为管理员...")
                original_chat_id = abs(chat_id)
                
                await client(EditChatAdminRequest(
                    chat_id=original_chat_id,
                    user_id=bot_entity,
                    is_admin=True
                ))
                logger.info(f"成功设置 bot 为管理员")
                bot_is_admin = True
            except Exception as e:
                logger.error(f"设置 bot 为管理员失败: {e}")
            
            # 设置群组头像
            avatar_set = False
            if avatar_url:
                try:
                    avatar_set = await self._set_group_avatar(client, chat_id, avatar_url)
                    if avatar_set:
                        logger.info(f"成功设置群组头像")
                    else:
                        logger.warning(f"设置群组头像失败")
                except Exception as e:
                    logger.error(f"设置群组头像异常: {e}")
            
            # 保存映射关系
            mapping_updated = False
            try:
                await self._save_chat_wxid_mapping(wxid, contact_name, chat_id, avatar_url)
                logger.info(f"已更新 contact.json，添加映射: {wxid} -> {chat_id}")
                mapping_updated = True
            except Exception as e:
                logger.error(f"更新 contact.json 失败: {e}")
            
            return {
                'success': True, 
                'chat_id': chat_id, 
                'group_name': group_name,
                'group_type': 'group', 
                'bot_invited': True, 
                'bot_is_admin': bot_is_admin,
                'avatar_set': avatar_set,
                'mapping_updated': mapping_updated
            }
            
        except Exception as e:
            logger.error(f"创建群组失败: {e}")
            return {'success': False, 'error': str(e)}

# 全局群组创建器实例
group_creator = TelegramGroupCreator()

# 异步函数 - 关键修改：确保在监控服务的事件循环中执行
async def create_group_async(wxid: str, contact_name: str, description: str = "", avatar_url: str = None) -> Optional[Dict]:
    """异步方式创建群组 - 必须在监控服务的事件循环中调用"""
    try:
        # 尝试导入监控服务
        try:
            from service.tg2wx import get_client
            monitor = get_client()
            
            # 检查监控服务是否可用
            if (monitor and monitor.client and monitor.client.is_connected() 
                and monitor.bot_entity and monitor.is_running):
                
                logger.info("使用监控服务的连接创建群组")
                # 直接调用，不创建新任务，确保在同一事件循环中
                return await group_creator.create_group_with_monitor(monitor, wxid, contact_name, description, avatar_url)
                
        except ImportError:
            logger.warning("无法导入监控服务，将使用独立连接")
        except Exception as e:
            logger.warning(f"使用监控服务连接失败: {e}，将使用独立连接")
        
        # 如果监控服务不可用，返回错误
        logger.error("监控服务不可用，无法创建群组")
        return {'success': False, 'error': '监控服务不可用'}
        
    except Exception as e:
        logger.error(f"异步创建群组失败: {e}")
        return {'success': False, 'error': str(e)}

# 异步版本的ContactManager类
class ContactManager:
    def __init__(self):
        self.contacts = []
        self.wxid_to_contact = {}
        self.chatid_to_wxid = {} 
        self.last_modified_time = 0
        self.contact_file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contact.json")
        
        # 初始加载联系人（同步方式，用于初始化）
        self._load_contacts_sync()
    
    def _load_contacts_sync(self):
        """同步加载联系人信息（仅用于初始化）"""
        try:
            if not os.path.exists(self.contact_file_path):
                self.contacts = []
                self.wxid_to_contact = {}
                self.chatid_to_wxid = {}
                return
                
            current_mtime = os.path.getmtime(self.contact_file_path)
            if current_mtime <= self.last_modified_time:
                return
            
            with open(self.contact_file_path, 'r', encoding='utf-8') as file:
                self.contacts = json.load(file)
                self.wxid_to_contact = {contact["wxId"]: contact for contact in self.contacts}
                self.chatid_to_wxid = {contact["chatId"]: contact["wxId"] for contact in self.contacts if "chatId" in contact}
                self.last_modified_time = current_mtime
                
            logger.info(f"联系人信息已更新，共 {len(self.contacts)} 个联系人")
                
        except Exception as e:
            logger.error(f"读取联系人文件失败: {e}")
            self.contacts = []
            self.wxid_to_contact = {}
            self.chatid_to_wxid = {}
    
    async def load_contacts(self):
        """异步加载联系人信息"""
        try:
            if not os.path.exists(self.contact_file_path):
                self.contacts = []
                self.wxid_to_contact = {}
                self.chatid_to_wxid = {}
                return
            
            # 异步获取文件修改时间
            loop = asyncio.get_event_loop()
            current_mtime = await loop.run_in_executor(None, os.path.getmtime, self.contact_file_path)
            
            if current_mtime <= self.last_modified_time:
                return
            
            # 异步读取文件
            def _read_file():
                with open(self.contact_file_path, 'r', encoding='utf-8') as file:
                    return json.load(file)
            
            contacts = await loop.run_in_executor(None, _read_file)
            
            self.contacts = contacts
            self.wxid_to_contact = {contact["wxId"]: contact for contact in self.contacts}
            self.chatid_to_wxid = {contact["chatId"]: contact["wxId"] for contact in self.contacts if "chatId" in contact}
            self.last_modified_time = current_mtime
                
            logger.info(f"联系人信息已更新，共 {len(self.contacts)} 个联系人")
                
        except Exception as e:
            logger.error(f"读取联系人文件失败: {e}")
            self.contacts = []
            self.wxid_to_contact = {}
            self.chatid_to_wxid = {}
    
    async def create_group_for_contact_async(self, wxid: str, contact_name: str, bot_token: str = None, 
                                            description: str = "", avatar_url: str = None) -> Optional[Dict]:
        """异步方式创建群组 - 确保在监控服务的事件循环中执行"""
        logger.info(f"开始创建群组: {contact_name}")
        
        # 直接调用，不使用 asyncio.create_task，确保在同一事件循环中执行
        result = await create_group_async(wxid, contact_name, description, avatar_url)
        
        logger.info(f"群组创建结果: {result}")
        return result
    
    async def get_contact(self, wxid):
        """异步获取联系人信息"""
        await self.load_contacts()
        contact = self.wxid_to_contact.get(wxid)
        return contact
    
    async def get_wxid_by_chatid(self, chat_id):
        """异步通过chatId获取wxId"""
        await self.load_contacts()
        return self.chatid_to_wxid.get(int(chat_id))
    
    async def get_contact_by_chatid(self, chat_id):
        """异步通过chatId获取联系人完整信息"""
        wxid = await self.get_wxid_by_chatid(chat_id)
        return await self.get_contact(wxid) if wxid else None

# 创建全局实例
contact_manager = ContactManager()
