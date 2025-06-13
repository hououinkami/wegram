import logging
import requests
import json
import config
import os
from io import BytesIO
from api.base import wechat_api
from utils.bind import TempTelegramClient

# 获取模块专用的日志记录器
logger = logging.getLogger(__name__)

# 获取用户信息
class UserInfo:
    def __init__(self, name, avatar_url):
        self.name = name
        self.avatar_url = avatar_url

def get_user_info(towxids):
    # 构建请求体
    body = {
        "Wxid": config.MY_WXID,
        "ChatRoom": "",
        "Towxids": towxids
    }
    
    # 发送请求
    result = wechat_api("/Friend/GetContractDetail", body)
    
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
def update_info(chat_id, title=None, photo_url=None):
    base_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
    results = {}
    
    # 更新群组名称
    if title:
        set_title_url = f"{base_url}/setChatTitle"
        title_params = {
            'chat_id': chat_id,
            'title': title
        }
        title_response = requests.post(set_title_url, data=title_params)
        results['title_update'] = title_response.json()
    
    # 更新群组头像
    if photo_url:
        try:
            # 下载图片
            photo_response = requests.get(photo_url)
            photo_response.raise_for_status()  # 确保请求成功
            
            # 处理图片尺寸
            processed_photo_content = TempTelegramClient._process_avatar_image(photo_response.content)

            # 发送请求更新头像
            set_photo_url = f"{base_url}/setChatPhoto"
            files = {
                'photo': ('group_photo.jpg', processed_photo_content, 'image/jpeg')
            }
            data = {
                'chat_id': chat_id
            }
            photo_update_response = requests.post(set_photo_url, data=data, files=files)
            results['photo_update'] = photo_update_response.json()
            
        except Exception as e:
            results['photo_update'] = {'ok': False, 'error': str(e)}
    
    return results