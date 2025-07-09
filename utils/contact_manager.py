import asyncio
import json
import logging
import os
from typing import Dict, Optional

from api import wechat_contacts, telegram_sender
from utils.group_binding import create_group

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
    
    async def _save_contacts(self):
        """异步保存联系人信息到文件"""
        try:
            loop = asyncio.get_event_loop()
            
            def _write_file():
                with open(self.contact_file_path, 'w', encoding='utf-8') as file:
                    json.dump(self.contacts, file, ensure_ascii=False, indent=2)
            
            await loop.run_in_executor(None, _write_file)
            
            # 更新修改时间
            self.last_modified_time = await loop.run_in_executor(None, os.path.getmtime, self.contact_file_path)
            
        except Exception as e:
            logger.error(f"保存联系人文件失败: {e}")
            raise
    
    async def delete_contact(self, wxid: str) -> bool:
        """删除联系人信息"""
        try:
            # 先加载最新的联系人信息
            await self.load_contacts()
            
            # 检查联系人是否存在
            if wxid not in self.wxid_to_contact:
                logger.warning(f"联系人不存在: {wxid}")
                return False
            
            # 获取要删除的联系人信息
            contact_to_delete = self.wxid_to_contact[wxid]
            chat_id = contact_to_delete.get("chatId")
            
            # 从内存中删除
            self.contacts = [contact for contact in self.contacts if contact["wxId"] != wxid]
            del self.wxid_to_contact[wxid]
            
            # 如果有chatId，也从映射中删除
            if chat_id and chat_id in self.chatid_to_wxid:
                del self.chatid_to_wxid[chat_id]
            
            # 保存到文件
            await self._save_contacts()
            
            return True
            
        except Exception as e:
            logger.error(f"删除联系人失败: {wxid}, 错误: {e}")
            return False
    
    async def delete_contact_by_chatid(self, chat_id: int) -> bool:
        """通过ChatID删除联系人信息"""
        try:
            # 先通过chatId获取wxId
            wxid = await self.get_wxid_by_chatid(chat_id)
            if not wxid:
                logger.warning(f"未找到ChatID对应的联系人: {chat_id}")
                return False
            
            # 调用删除方法
            return await self.delete_contact(wxid)
            
        except Exception as e:
            logger.error(f"通过ChatID删除联系人失败: {chat_id}, 错误: {e}")
            return False
    
    async def update_contact_by_chatid(self, chat_id: int, updates: dict) -> bool:
        """通过ChatID更新联系人的指定字段"""
        try:
            # 先加载最新的联系人信息
            await self.load_contacts()
            
            # 通过chatId获取wxId
            wxid = self.chatid_to_wxid.get(int(chat_id))
            if not wxid:
                return False
            
            # 找到联系人在列表中的索引
            contact_index = -1
            for i, contact in enumerate(self.contacts):
                if contact["wxId"] == wxid:
                    contact_index = i
                    break
            
            if contact_index == -1:
                return False
            
            # 批量更新字段
            for key, value in updates.items():
                # 特殊处理切换布尔值
                if value == "toggle" and key in ["isReceive", "isGroup"]:
                    current_value = self.contacts[contact_index].get(key, False)
                    value = not current_value
                elif key in ["isReceive", "isGroup"] and isinstance(value, str):
                    # 如果传入字符串，转换为布尔值
                    value = value.lower() in ['true', '1', 'yes', 'on']
                
                # 更新字段
                self.contacts[contact_index][key] = value
                self.wxid_to_contact[wxid][key] = value
            
            # 保存到文件
            await self._save_contacts()
            return True
            
        except Exception as e:
            logger.error(f"更新联系人字段失败 - ChatID: {chat_id}, 更新: {updates}, 错误: {e}")
            return False
    
    # 异步创建群组
    async def create_group_for_contact_async(self, wxid: str, contact_name: str, description: str = "", avatar_url: str = None) -> Optional[Dict]:
        """异步方式创建群组"""        
        try:
            # 删除占位信息
            contact_dic = await contact_manager.get_contact(wxid)
            if contact_dic and contact_dic["chatId"] == -9999999999:
                await contact_manager.delete_contact(wxid)

            # 使用线程池执行同步版本，避免事件循环冲突
            result = await create_group(wxid, contact_name, description, avatar_url)
            
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
    
    async def check_existing_mapping(self, wxid: str) -> Optional[Dict]:
        """检查是否已有映射"""
        await self.load_contacts()
        for contact in self.contacts:
            if contact.get('wxId') == wxid and contact.get('chatId'):
                return contact
        return None

    async def save_chat_wxid_mapping(self, wxid: str, name: str, chat_id: int, avatar_url: str = None):
        """保存群组ID和微信ID的映射关系"""
        await self.load_contacts()
        
        # 检查是否已存在
        for contact in self.contacts:
            if contact.get('wxId') == wxid and contact.get('chatId') == chat_id:
                return
        
        is_group = wxid.endswith('@chatroom')
        new_contact = {
            "name": name,
            "wxId": wxid,
            "chatId": chat_id,
            "isGroup": is_group,
            "isReceive": True,
            "alias": "",
            "avatarLink": avatar_url
        }
        
        self.contacts.append(new_contact)
        self.wxid_to_contact[wxid] = new_contact
        self.chatid_to_wxid[chat_id] = wxid
        
        await self._save_contacts()
    
    async def update_contacts_and_sync_to_json(self, chat_id: int):
        """获取联系人列表并同步到contact.json"""
        try:
            # 发送开始处理的消息
            logger.info("🔄 正在获取联系人列表...")
            
            # 获取联系人列表
            friend_contacts, chatroom_contacts, gh_contacts = await wechat_contacts.get_friends()
            all_contacts = friend_contacts + chatroom_contacts
            if not all_contacts:
                await telegram_sender.send_text(chat_id, "❌ 未获取到好友联系人")
                return
            
            logger.info(f"📋 获取到 {len(all_contacts)} 个好友，正在同步信息...")
            
            # 将all_contacts按每组20个分割
            batch_size = 20
            batches = [all_contacts[i:i + batch_size] for i in range(0, len(all_contacts), batch_size)]
            
            new_contacts_count = 0
            total_batches = len(batches)
            
            # 处理每个批次
            for batch_index, batch in enumerate(batches):
                try:
                    # 发送进度更新
                    if batch_index % 5 == 0 or batch_index == total_batches - 1:  # 每5个批次或最后一个批次更新进度
                        progress = f"⏳ 处理进度: {batch_index + 1}/{total_batches} 批次"
                        logger.info(progress)
                    
                    # 调用get_user_info获取用户信息
                    user_info_dict = await wechat_contacts.get_user_info(batch)
                    
                    if not user_info_dict:
                        logger.warning(f"批次 {batch_index + 1} 未获取到用户信息")
                        continue
                    
                    # 遍历用户信息
                    for wxid, user_info in user_info_dict.items():
                        if user_info is None:
                            logger.warning(f"用户 {wxid} 信息获取失败")
                            continue
                        
                        # 检查wxId是否已存在于contact.json中
                        existing_contact = await self.get_contact(wxid)
                        
                        if existing_contact is None:
                            # 不存在则创建新联系人
                            new_contact = {
                                "name": user_info.name,
                                "wxId": wxid,
                                "chatId": -9999999999,
                                "isGroup": False,
                                "isReceive": True,
                                "alias": "",
                                "avatarLink": user_info.avatar_url if user_info.avatar_url else ""
                            }
                            
                            # 添加到联系人管理器
                            self.contacts.append(new_contact)
                            self.wxid_to_contact[wxid] = new_contact
                            
                            new_contacts_count += 1
                            logger.info(f"添加新联系人: {user_info.name} ({wxid})")
                    
                    # 每处理几个批次休眠一下，避免请求过于频繁
                    if batch_index < total_batches - 1:  # 不是最后一个批次
                        await asyncio.sleep(0.5)  # 休眠500毫秒
                        
                except Exception as e:
                    logger.error(f"处理批次 {batch_index + 1} 时出错: {str(e)}")
                    continue
            
            # 保存所有更改到文件
            if new_contacts_count > 0:
                await self._save_contacts()
                success_msg = f"✅ 同步完成！新增 {new_contacts_count} 个联系人到contact.json"
            else:
                success_msg = "✅ 同步完成！所有联系人已存在，无新增联系人"
            
            logger.info(success_msg)
            
            # 发送统计信息
            stats_msg = f"""
    📊 **同步统计**
    • 总好友数: {len(all_contacts)}
    • 新增联系人: {new_contacts_count}
    • 处理批次: {total_batches}
    • 当前联系人总数: {len(self.contacts)}
            """
            logger.info(stats_msg)
            
        except Exception as e:
            error_msg = f"❌ 更新联系人失败: {str(e)}"
            await telegram_sender.send_text(chat_id, error_msg)
            logger.error(f"update_contacts执行失败: {str(e)}")

# 创建全局实例
contact_manager = ContactManager()
