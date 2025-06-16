import logging

import requests

import config
from api.base import wechat_api
from api.bot import telegram_sender
from utils.bind import GroupManager

logger = logging.getLogger(__name__)

# 获取用户信息
class UserInfo:
    def __init__(self, name, avatar_url):
        self.name = name
        self.avatar_url = avatar_url

async def get_user_info(towxids):
    # 构建请求体
    body = {
        "Wxid": config.MY_WXID,
        "ChatRoom": "",
        "Towxids": towxids
    }
    
    # 发送请求
    result = await wechat_api("/Friend/GetContractDetail", body)
    
    # 解析响应获取备注名
    if result.get("Success"):
        try:
            contact_list = result["Data"]["ContactList"]
            if contact_list and len(contact_list) > 0:
                name = (contact_list[0].get("Remark", {}).get("string") or 
                       contact_list[0].get("NickName", {}).get("string") or 
                       "未知用户")
                avatar_url = (contact_list[0].get("BigHeadImgUrl") or 
                              contact_list[0].get("SmallHeadImgUrl") or 
                              "")
                return UserInfo(name, avatar_url)
        except (KeyError, IndexError) as e:
            logger.error(f"解析联系人信息时出错: {str(e)}")
    else:
        error_msg = result.get('Message', '未知错误')
        logger.error(f"API请求失败: {error_msg}")
    return None


# 更新群组信息
async def update_info(chat_id, title=None, photo_url=None):
    """更新群组信息（标题和头像）"""
    results = {}
    
    # 更新群组名称
    if title:
        try:
            result = await telegram_sender.set_chat_title(chat_id, title)
            results['title_update'] = {
                'ok': True, 
                'description': 'Title updated successfully',
                'result': result
            }
        except Exception as e:
            results['title_update'] = {
                'ok': False, 
                'error': str(e)
            }
    
    # 更新群组头像
    if photo_url:
        try:
            # 下载图片
            photo_response = requests.get(photo_url)
            photo_response.raise_for_status()
            
            # 处理图片尺寸
            processed_photo_content = GroupManager._process_avatar_image(photo_response.content)
            
            result = await telegram_sender.set_chat_photo(chat_id, processed_photo_content)
            results['photo_update'] = {
                'ok': True, 
                'description': 'Photo updated successfully',
                'result': result
            }
            
        except Exception as e:
            results['photo_update'] = {
                'ok': False, 
                'error': str(e)
            }
    
    return results