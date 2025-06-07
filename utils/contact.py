#!/usr/bin/env python3
"""
独立的Telegram群组创建模块
复用监控服务中的客户端和bot实例
"""

import logging
import os
import json
import asyncio
from typing import Dict, Optional
from utils.bind import create_group_sync

# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

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
    
    # 修改这个方法
    async def create_group_for_contact_async(self, wxid: str, contact_name: str, bot_token: str = None, 
                                            description: str = "", avatar_url: str = None) -> Optional[Dict]:
        """异步方式创建群组"""
        logger.info(f"开始创建群组: {contact_name}")
        
        try:
            # 使用线程池执行同步版本，避免事件循环冲突
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                create_group_sync, 
                wxid, contact_name, description, avatar_url
            )
            
            logger.info(f"群组创建结果: {result}")
            
            # 创建成功后重新加载联系人信息
            if result.get('success'):
                await self.load_contacts()
            
            return result
            
        except Exception as e:
            logger.error(f"创建群组失败: {e}")
            return {'success': False, 'error': str(e)}
    
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
