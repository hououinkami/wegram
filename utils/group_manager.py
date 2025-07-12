import json
import logging
import os
from typing import Dict, List, Optional, Any, Callable

import config
from api.wechat_api import wechat_api

logger = logging.getLogger(__name__)

class GroupMemberManager:
    """群成员管理器 - 支持多群组数据存储和查询"""
    
    def __init__(self, json_file_path: str = None):
        """
        初始化群成员管理器
        
        Args:
            json_file_path: JSON文件路径，如果不指定则使用默认路径
        """
        if json_file_path is None:
            # 使用用户指定的默认路径: 项目根目录/group.json
            self.json_file_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "group.json"
            )
        else:
            self.json_file_path = json_file_path
        
        # 确保目录存在
        os.makedirs(os.path.dirname(self.json_file_path), exist_ok=True)
        
        # 加载现有数据
        self.data = self.load_from_json()
    
    def load_from_json(self) -> Dict[str, List[Dict[str, str]]]:
        """从JSON文件加载数据"""
        try:
            if os.path.exists(self.json_file_path):
                with open(self.json_file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.info(f"⚠️ 加载JSON文件失败: {e}")
            return {}
    
    def save_to_json(self, new_data: Dict[str, List[Dict[str, str]]] = None):
        """保存数据到JSON文件"""
        try:
            data_to_save = new_data if new_data is not None else self.data
            
            # 如果有新数据，合并到现有数据中
            if new_data is not None:
                self.data.update(new_data)
                data_to_save = self.data
            
            with open(self.json_file_path, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ 数据已保存到: {self.json_file_path}")
            return True
        except Exception as e:
            logger.info(f"❌ 保存JSON文件失败: {e}")
            return False
    
    def extract_members(self, response: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
        """提取API响应"""
        data = response["Data"]
        chatroom_name = data.get("ChatroomUserName", "")
        members_data = data.get("NewChatroomData", {}).get("ChatRoomMember")
        
        members = []
        for member in members_data:
            if member:
                members.append({
                    "username": member.get("UserName", ""),
                    "nickname": member.get("NickName", ""),
                    "displayname": member.get("DisplayName", "")
                })
        
        return {chatroom_name: members}
    
    async def update_group_member(self, chatroom_id: str) -> bool:
        """
        使用微信API函数更新群组信息
        
        Args:
            wechat_api_func: 你的wechat_api函数
            chatroom_id: 群组ID
            
        Returns:
            bool: 更新是否成功
        """
        try:
            # 构建payload
            payload = {
                "QID": chatroom_id,
                "Wxid": config.MY_WXID
            }
            group_member_response = await wechat_api("GROUP_MEMBER", payload)
            
            # 提取成员信息
            extracted_data = self.extract_members(group_member_response)
            
            if extracted_data:
                # 保存到JSON
                self.save_to_json(extracted_data)
                member_count = len(list(extracted_data.values())[0])
                logger.info(f"✅ 成功更新群 {chatroom_id}，共 {member_count} 名成员")
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
                    logger.info(f"✅ 成功删除群组 {chatroom_id}")
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
        if chatroom_id not in self.data:
            payload = {
                "QID": chatroom_id,
                "Wxid": config.MY_WXID
            }
            group_member_response = await wechat_api("GROUP_MEMBER", payload)

            new_data = self.extract_members(group_member_response)
            self.save_to_json(new_data)
        
        if chatroom_id in self.data:
            for member in self.data[chatroom_id]:
                if member["username"] == username:
                    return member["displayname"] if member["displayname"] else member["nickname"]
        
        return ""
    
    def get_all_members(self, chatroom_id: str) -> List[Dict[str, str]]:
        """获取指定群的所有成员"""
        return self.data.get(chatroom_id, [])
    
    def search_user_across_groups(self, username: str) -> Dict[str, str]:
        """跨群查询用户，返回用户在各个群中的显示名"""
        result = {}
        
        for chatroom_id, members in self.data.items():
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
        for members in self.data.values():
            total += len(members)
        return total
    
    def get_unique_users(self) -> set:
        """获取所有唯一用户的集合"""
        unique_users = set()
        for members in self.data.values():
            for member in members:
                unique_users.add(member["username"])
        return unique_users
    
    def batch_update_groups(self, wechat_api_func: Callable, chatroom_ids: List[str]) -> Dict[str, bool]:
        """
        批量更新多个群组
        
        Args:
            wechat_api_func: 你的wechat_api函数
            chatroom_ids: 群组ID列表
            
        Returns:
            Dict[str, bool]: 每个群组的更新结果
        """
        results = {}
        
        logger.info(f"🚀 开始批量更新 {len(chatroom_ids)} 个群组...")
        
        for i, chatroom_id in enumerate(chatroom_ids, 1):
            logger.info(f"\n[{i}/{len(chatroom_ids)}] 处理群组: {chatroom_id}")
            results[chatroom_id] = self.update_group_with_wechat_api(wechat_api_func, chatroom_id)
        
        # 统计结果
        success_count = sum(1 for success in results.values() if success)
        logger.info(f"\n📊 批量更新完成:")
        logger.info(f"   ✅ 成功: {success_count}")
        logger.info(f"   ❌ 失败: {len(chatroom_ids) - success_count}")
        
        return results

group_manager = GroupMemberManager()
