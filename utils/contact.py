# contact.py
import logging
logger = logging.getLogger(__name__)

import config
import os
import json
import requests
from io import BytesIO

class ContactManager:
    def __init__(self):
        self.contacts = []
        self.wxid_to_contact = {}
        self.chatid_to_wxid = {} 
        self.last_modified_time = 0
        self.contact_file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contact.json")
        # 初始加载联系人
        self.load_contacts()
    
    def load_contacts(self):
        """加载联系人信息，只有在文件被修改时才重新加载"""
        try:
            # 获取文件的最后修改时间
            current_mtime = os.path.getmtime(self.contact_file_path)
            
            # 如果文件未被修改，直接返回
            if current_mtime <= self.last_modified_time:
                return
            
            # 文件已更新，重新加载
            with open(self.contact_file_path, 'r', encoding='utf-8') as file:
                self.contacts = json.load(file)
                self.wxid_to_contact = {contact["wxId"]: contact for contact in self.contacts}
                
                # 创建chatId到wxId的映射
                self.chatid_to_wxid = {}
                for contact in self.contacts:
                    if "chatId" in contact:
                        self.chatid_to_wxid[contact["chatId"]] = contact["wxId"]
                
                self.last_modified_time = current_mtime
                logger.info("联系人信息已更新")
                
        except FileNotFoundError:
            logger.error(f"找不到contact.json文件: {self.contact_file_path}")
            self.contacts = []
            self.wxid_to_contact = {}
            self.chatid_to_wxid = {}
        except json.JSONDecodeError:
            logger.error("contact.json格式错误")
            # 保持原有数据不变
        except Exception as e:
            logger.error(f"读取contact.json文件失败: {e}")
    
    def get_contact(self, wxid):
        """通过wxId获取联系人信息，先检查文件是否更新"""
        self.load_contacts()
        contact = self.wxid_to_contact.get(wxid)
        if not contact and not "chatroom" in wxid and not "gh_" in wxid:
            contact = self.wxid_to_contact.get("wxid_not_in_json")
        return contact
    
    def get_wxid_by_chatid(self, chat_id):
        """通过chatId获取wxId，先检查文件是否更新"""
        self.load_contacts()
        return self.chatid_to_wxid.get(int(chat_id))
    
    def get_contact_by_chatid(self, chat_id):
        """通过chatId获取联系人完整信息"""
        wxid = self.get_wxid_by_chatid(chat_id)
        if wxid:
            return self.get_contact(wxid)
        return None

# 创建全局的联系人管理器实例
contact_manager = ContactManager()
