import json
import logging
import os
import time
from typing import Dict, List, Optional, Any, Callable

import config
from api.wechat_api import wechat_api

logger = logging.getLogger(__name__)

class GroupMemberManager:
    """群成员管理器 - 支持多群组数据存储和查询"""
    
    def __init__(self, json_file_path: str = None, cache_expire_hours: int = 2):
        """
        初始化群成员管理器
        
        Args:
            json_file_path: JSON文件路径，如果不指定则使用默认路径
            cache_expire_hours: 缓存过期时间（小时）
        """
        if json_file_path is None:
            # 使用用户指定的默认路径: 项目根目录/group.json
            self.json_file_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "group.json"
            )
        else:
            self.json_file_path = json_file_path
        
        self.cache_expire_seconds = cache_expire_hours * 3600
        
        # 确保目录存在
        os.makedirs(os.path.dirname(self.json_file_path), exist_ok=True)
        
        # 加载现有数据
        self.data = self.load_from_json()
    
    def load_from_json(self) -> Dict[str, Dict[str, Any]]:
        """从JSON文件加载数据"""
        try:
            if os.path.exists(self.json_file_path):
                with open(self.json_file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.info(f"⚠️ 加载JSON文件失败: {e}")
            return {}
    
    def save_to_json(self, new_data: Dict[str, Dict[str, Any]] = None):
        """保存数据到JSON文件"""
        try:
            data_to_save = new_data if new_data is not None else self.data
            
            # 如果有新数据，合并到现有数据中
            if new_data is not None:
                self.data.update(new_data)
                data_to_save = self.data
            
            with open(self.json_file_path, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            logger.debug(f"✅ 数据已保存到: {self.json_file_path}")
            return True
        except Exception as e:
            logger.info(f"❌ 保存JSON文件失败: {e}")
            return False
    
    def extract_members(self, response: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """提取API响应，包含ServerVersion信息"""
        data = response["Data"]
        chatroom_name = data.get("ChatroomUserName", "")
        server_version = data.get("ServerVersion", 0)
        member_count = data.get("NewChatroomData", {}).get("MemberCount", 0)
        members_data = data.get("NewChatroomData", {}).get("ChatRoomMember", {})
        
        members = []
        for member in members_data:
            if member:
                members.append({
                    "username": member.get("UserName", ""),
                    "nickname": member.get("NickName", ""),
                    "displayname": member.get("DisplayName", "")
                })
        
        current_time = int(time.time())
        
        return {
            chatroom_name: {
                "serverVersion": server_version,
                "memberCount": member_count,
                "lastUpdate": current_time,
                "cacheExpiry": current_time + self.cache_expire_seconds,
                "members": members
            }
        }
    
    def is_cache_valid(self, chatroom_id: str) -> bool:
        """检查缓存是否有效（未过期）"""
        if chatroom_id not in self.data:
            return False
        
        current_time = int(time.time())
        cache_expiry = self.data[chatroom_id].get("cacheExpiry", 0)
        
        return current_time < cache_expiry
    
    def need_update(self, chatroom_id: str, new_server_version: int) -> bool:
        """判断是否需要更新数据"""
        # 如果缓存不存在，需要更新
        if chatroom_id not in self.data:
            return True
        
        # 如果缓存已过期，需要更新
        if not self.is_cache_valid(chatroom_id):
            return True
        
        # 如果ServerVersion发生变化，需要更新
        cached_version = self.data[chatroom_id].get("serverVersion", 0)
        if new_server_version != cached_version:
            logger.info(f"🔄 检测到ServerVersion变化: {cached_version} -> {new_server_version}")
            return True
        
        return False

    async def update_group_member(self, chatroom_id: str, force_update: bool = False) -> bool:
        """
        使用微信API函数更新群组信息
        
        Args:
            chatroom_id: 群组ID
            force_update: 是否强制更新（忽略缓存）
            
        Returns:
            bool: 更新是否成功
        """
        try:
            # 构建payload
            payload = {
                "QID": chatroom_id,
                "Wxid": config.MY_WXID
            }
            
            # 如果不是强制更新，先检查是否需要更新
            if not force_update:
                # 先获取当前数据以检查ServerVersion
                group_member_response = await wechat_api("GROUP_MEMBER", payload)
                current_server_version = group_member_response["Data"].get("ServerVersion", 0)
                
                # 检查是否需要更新
                if not self.need_update(chatroom_id, current_server_version):
                    return True
                
            else:
                # 强制更新时直接获取数据
                group_member_response = await wechat_api("GROUP_MEMBER", payload)
            
            # 提取成员信息
            extracted_data = self.extract_members(group_member_response)
            
            if extracted_data:
                # 保存到JSON
                self.save_to_json(extracted_data)
                chatroom_data = list(extracted_data.values())[0]
                member_count = len(chatroom_data["members"])
                server_version = chatroom_data["serverVersion"]
                logger.debug(f"✅ 成功更新群 {chatroom_id}，共 {member_count} 名成员 (ServerVersion: {server_version})")
                return True
            else:
                logger.error(f"❌ 未能从响应中提取到成员信息")
                return False
                
        except Exception as e:
            logger.error(f"❌ 更新群组信息失败: {e}")
            return False
    
    async def delete_group(self, chatroom_id: str) -> bool:
        """
        删除指定群组的信息
        
        Args:
            chatroom_id: 要删除的群组ID
            
        Returns:
            bool: 删除是否成功
        """
        try:
            if chatroom_id in self.data:                
                # 从内存中删除
                del self.data[chatroom_id]
                
                # 保存到JSON文件
                if self.save_to_json():
                    logger.debug(f"✅ 成功删除群组 {chatroom_id}")
                    return True
                else:
                    logger.error(f"❌ 删除群组 {chatroom_id} 后保存文件失败")
                    return False
            else:
                logger.warning(f"⚠️ 群组 {chatroom_id} 不存在，无需删除")
                return False
                
        except Exception as e:
            logger.error(f"❌ 删除群组 {chatroom_id} 时发生错误: {e}")
            return False
    
    async def get_display_name(self, chatroom_id: str, username: str) -> str:
        """获取用户在指定群中的显示名称"""
        # 检查缓存并更新（如果需要）
        await self.update_group_member(chatroom_id)
        
        if chatroom_id in self.data:
            members = self.data[chatroom_id].get("members", [])
            for member in members:
                if member["username"] == username:
                    return member["displayname"] if member["displayname"] else member["nickname"]
        
        return ""
    
    def get_all_members(self, chatroom_id: str) -> List[Dict[str, str]]:
        """获取指定群的所有成员"""
        if chatroom_id in self.data:
            return self.data[chatroom_id].get("members", [])
        return []
    
    def search_user_across_groups(self, username: str) -> Dict[str, str]:
        """跨群查询用户，返回用户在各个群中的显示名"""
        result = {}
        
        for chatroom_id, group_data in self.data.items():
            members = group_data.get("members", [])
            for member in members:
                if member["username"] == username:
                    display_name = member["displayname"] if member["displayname"] else member["nickname"]
                    result[chatroom_id] = display_name
                    break
        
        return result
    
    def get_chatroom_list(self) -> List[str]:
        """获取所有群组ID列表"""
        return list(self.data.keys())
    
    def get_total_groups(self) -> int:
        """获取总群组数量"""
        return len(self.data)
    
    def get_total_members(self) -> int:
        """获取所有群组的总成员数（可能有重复用户）"""
        total = 0
        for group_data in self.data.values():
            total += len(group_data.get("members", []))
        return total
    
    def get_unique_users(self) -> set:
        """获取所有唯一用户的集合"""
        unique_users = set()
        for group_data in self.data.values():
            members = group_data.get("members", [])
            for member in members:
                unique_users.add(member["username"])
        return unique_users
    
    def get_cache_info(self, chatroom_id: str) -> Dict[str, Any]:
        """获取指定群组的缓存信息"""
        if chatroom_id not in self.data:
            return {}
        
        group_data = self.data[chatroom_id]
        current_time = int(time.time())
        
        return {
            "serverVersion": group_data.get("serverVersion", 0),
            "memberCount": group_data.get("memberCount", 0),
            "lastUpdate": group_data.get("lastUpdate", 0),
            "cacheExpiry": group_data.get("cacheExpiry", 0),
            "isValid": self.is_cache_valid(chatroom_id),
            "remainingSeconds": max(0, group_data.get("cacheExpiry", 0) - current_time)
        }
    
    async def batch_update_groups(self, chatroom_ids: List[str], force_update: bool = False) -> Dict[str, bool]:
        """
        批量更新多个群组
        
        Args:
            chatroom_ids: 群组ID列表
            force_update: 是否强制更新所有群组
            
        Returns:
            Dict[str, bool]: 每个群组的更新结果
        """
        results = {}
        
        logger.info(f"🚀 开始批量更新 {len(chatroom_ids)} 个群组...")
        
        for i, chatroom_id in enumerate(chatroom_ids, 1):
            logger.info(f"\n[{i}/{len(chatroom_ids)}] 处理群组: {chatroom_id}")
            results[chatroom_id] = await self.update_group_member(chatroom_id, force_update)
        
        # 统计结果
        success_count = sum(1 for success in results.values() if success)
        logger.info(f"\n📊 批量更新完成:")
        logger.info(f"   ✅ 成功: {success_count}")
        logger.info(f"   ❌ 失败: {len(chatroom_ids) - success_count}")
        
        return results

group_manager = GroupMemberManager()
