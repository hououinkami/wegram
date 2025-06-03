# contact.py
import logging
logger = logging.getLogger(__name__)

import config
import os
import json
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional

from telethon.tl.functions.messages import CreateChatRequest, AddChatUserRequest, EditChatAdminRequest, EditChatPhotoRequest
from telethon.tl.functions.channels import CreateChannelRequest, InviteToChannelRequest, EditAdminRequest
from telethon.tl.types import ChatAdminRights
from telethon.errors import UserAlreadyParticipantError, FloodWaitError

from utils import telegram

# 全局线程池
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="TelegramCreator")

def _create_group_in_thread(api_id, api_hash, phone_number, session_dir, group_name, bot_token, description, wxid, contact_name):
  """在独立线程中创建群组，并立即更新映射"""
  try:
      # 创建新的事件循环
      loop = asyncio.new_event_loop()
      asyncio.set_event_loop(loop)
      
      async def create_group():
          client = None
          try:
              # 使用管理器获取客户端和bot实体
              client, bot_entity = await telegram.telegram_manager.get_client_and_bot(
                  session_dir, api_id, api_hash, phone_number, bot_token
              )
              
              # 创建普通群组
              try:
                  logger.info(f"尝试创建普通群组: {group_name}")
                  result = await client(CreateChatRequest(
                      users=[bot_entity], title=group_name
                  ))
                  
                  logger.info(f"创建群组返回结果类型: {type(result)}")
                  chat_id = None
                  
                  # 方法1: 尝试从返回结果中直接获取
                  if hasattr(result, 'chats') and result.chats:
                      chat = result.chats[0]
                      chat_id = -chat.id
                  elif hasattr(result, 'updates'):
                      updates = result.updates
                      if hasattr(updates, 'chats') and updates.chats:
                          chat = updates.chats[0]
                          chat_id = -chat.id
                  
                  # 方法2: 如果方法1失败，通过对话列表查找
                  if chat_id is None:
                      await asyncio.sleep(1)  # 稍等一下确保群组已经创建完成
                      dialogs = await client.get_dialogs(limit=20)
                      for dialog in dialogs:
                          if (dialog.title == group_name and 
                              dialog.is_group and 
                              not dialog.is_channel):
                              chat_id = dialog.id
                              break
                  
                  if chat_id is None:
                      logger.error(f"所有方法都失败，无法获取群组ID")
                      return {'success': False, 'error': "无法获取创建的群组ID"}
                  
                  logger.info(f"成功创建普通群组，ID: {chat_id}")
                  
                  # 设置 bot 为管理员
                  try:
                      logger.info(f"尝试设置 bot 为管理员...")
                      # 使用正确的 chat_id（去掉负号）
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
                      bot_is_admin = False
                  
                  # 立即更新 contact.json
                  try:
                      _save_chat_wxid_mapping(os.path.dirname(session_dir), wxid, contact_name, chat_id)
                      logger.info(f"已立即更新 contact.json，添加映射: {wxid} -> {chat_id}")
                      mapping_updated = True
                  except Exception as e:
                      logger.error(f"更新 contact.json 失败: {e}")
                      mapping_updated = False
                  
                  return {
                      'success': True, 
                      'chat_id': chat_id, 
                      'group_name': group_name,
                      'group_type': 'group', 
                      'bot_invited': True, 
                      'bot_is_admin': bot_is_admin,
                      'mapping_updated': mapping_updated
                  }
                  
              except Exception as e:
                  logger.error(f"创建普通群组失败: {e}")
                  return {'success': False, 'error': f"创建普通群组失败: {e}"}

          except Exception as e:
              logger.error(f"获取客户端或bot实体失败: {e}")
              return {'success': False, 'error': f"获取客户端或bot实体失败: {e}"}
          
          finally:
              # 确保客户端被正确断开
              if client and client.is_connected():
                  try:
                      await client.disconnect()
                      logger.info("已断开客户端连接")
                  except Exception as e:
                      logger.error(f"断开客户端连接失败: {e}")
      
      # 运行异步函数
      result = loop.run_until_complete(create_group())
      return result
      
  except Exception as e:
      logger.error(f"线程创建群组异常: {e}")
      return {'success': False, 'error': str(e)}
  finally:
      try:
          loop.close()
      except:
          pass

def _save_chat_wxid_mapping(parent_dir, wxid, name, chat_id):
    """保存群组ID和微信ID的映射关系到contact.json"""
    # 判断是否为群聊
    if wxid.endswith('@chatroom'):
        is_group = True
    else:
        is_group = False
    try:
        contact_json_path = os.path.join(parent_dir, 'contact.json')
        
        # 读取现有映射
        contacts = []
        if os.path.exists(contact_json_path):
            with open(contact_json_path, 'r', encoding='utf-8') as f:
                contacts = json.load(f)
        
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
            "avatarLink": ""
        }
        
        contacts.append(new_contact)
        
        # 保存映射
        with open(contact_json_path, 'w', encoding='utf-8') as f:
            json.dump(contacts, f, ensure_ascii=False, indent=4)
            
        logger.info(f"已保存映射: {wxid} -> {chat_id}")
    except Exception as e:
        logger.error(f"保存映射关系失败: {e}")
        raise e


class ContactManager:
    def __init__(self):
        self.contacts = []
        self.wxid_to_contact = {}
        self.chatid_to_wxid = {} 
        self.last_modified_time = 0
        self.contact_file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contact.json")
        
        # Telegram配置
        self.api_id = getattr(config, 'TELEGRAM_API_ID', None)
        self.api_hash = getattr(config, 'TELEGRAM_API_HASH', None)
        self.phone_number = getattr(config, 'TELEGRAM_PHONE', None)
        
        # 会话目录
        self.session_dir = os.path.join(os.path.dirname(__file__), '..', 'sessions')
        os.makedirs(self.session_dir, exist_ok=True)
        
        # 初始加载联系人
        self.load_contacts()
    
    def load_contacts(self):
        """加载联系人信息"""
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
    
    def create_group_for_contact_sync(self, wxid: str, contact_name: str, bot_token: str, description: str = "") -> Optional[Dict]:
        """同步方式创建群组"""
        try:
            # 检查是否已经有群组映射
            contact_json_path = os.path.join(os.path.dirname(self.session_dir), 'contact.json')
            if os.path.exists(contact_json_path):
                with open(contact_json_path, 'r', encoding='utf-8') as f:
                    contacts = json.load(f)
                    
                # 检查是否已经有该微信ID的映射
                for contact in contacts:
                    if contact.get('wxId') == wxid and contact.get('isGroup', False):
                        logger.info(f"该微信ID {wxid} 已有群组映射，群组ID: {contact.get('chatId')}")
                        return {
                            'success': True, 
                            'chat_id': contact.get('chatId'), 
                            'group_name': contact.get('name'),
                            'group_type': 'group', 
                            'bot_invited': True, 
                            'bot_is_admin': True,
                            'already_exists': True
                        }
            
            # 如果没有映射，创建新群组
            group_name = f"{contact_name}"
            
            # 创建线程执行异步操作
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(
                _create_group_in_thread,
                self.api_id, self.api_hash, self.phone_number,
                self.session_dir, group_name, bot_token, description,
                wxid, contact_name
            )
            
            # 等待线程完成
            result = future.result()
            
            return result
            
        except Exception as e:
            logger.error(f"创建群组异常: {e}")
            return None

    def get_telegram_client_info(self) -> dict:
        """获取 Telegram 客户端信息（用于调试）"""
        return telegram.get_client_info()

    def disconnect_telegram_client(self):
        """断开 Telegram 客户端连接"""
        try:
            # 创建新的事件循环来执行断开操作
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(telegram.disconnect_client())
            loop.close()
            logger.info("已断开 Telegram 客户端连接")
        except Exception as e:
            logger.error(f"断开客户端连接失败: {e}")

    def get_contact(self, wxid):
        """获取联系人信息"""
        self.load_contacts()
        contact = self.wxid_to_contact.get(wxid)
        if not contact and not "chatroom" in wxid and not "gh_" in wxid:
            contact = self.wxid_to_contact.get("wxid_not_in_json")
        return contact
    
    def get_wxid_by_chatid(self, chat_id):
        """通过chatId获取wxId"""
        self.load_contacts()
        return self.chatid_to_wxid.get(int(chat_id))
    
    def get_contact_by_chatid(self, chat_id):
        """通过chatId获取联系人完整信息"""
        wxid = self.get_wxid_by_chatid(chat_id)
        return self.get_contact(wxid) if wxid else None

# 创建全局实例
contact_manager = ContactManager()