import logging

import requests

import config
from api.wechat_api import wechat_api
from api.telegram_sender import telegram_sender
from utils.group_binding import process_avatar_from_url

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
    result = await wechat_api("USER_INFO", body)
    
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
            # 处理图片尺寸
            processed_photo_content = await process_avatar_from_url(photo_url)
            
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

async def get_friends():
    """
    获取所有好友联系人并分类
    
    Args:
        wechat_api: API调用函数
        wxid: 微信ID
        
    Returns:
        tuple: (gh_contacts, friend_contacts)
            - gh_contacts: 以gh_开头的联系人ID列表（公众号）
            - friend_contacts: 非gh_开头的联系人ID列表（个人用户等）
    """
    
    # 初始化变量
    current_wx_seq = 0
    current_chatroom_seq = 0
    continue_flag = 1
    
    # 存储所有联系人
    all_contacts = []
    page_count = 0
    
    # 循环获取直到没有更多数据
    while continue_flag == 1:
        page_count += 1
        logger.info(f"正在获取第 {page_count} 页数据...")
        
        # 构建请求体
        body = {
            "CurrentChatRoomContactSeq": current_chatroom_seq,
            "CurrentWxcontactSeq": current_wx_seq,
            "Wxid": config.MY_WXID
        }
        
        try:
            # 调用API
            response = wechat_api("USER_LIST", body)
            
            # 检查响应是否成功
            if not response.get('Success', False):
                error_msg = response.get('Message', '未知错误')
                logger.info(f"API调用失败: {error_msg}")
                break
            
            # 获取数据
            data = response.get('Data', {})
            
            # 更新分页参数
            continue_flag = data.get('CountinueFlag', 0)
            current_wx_seq = data.get('CurrentWxcontactSeq', 0)
            current_chatroom_seq = data.get('CurrentChatRoomContactSeq', 0)
            
            # 获取当前页的联系人列表
            contact_list = data.get('ContactUsernameList', [])
            contact_count = len(contact_list)
            
            logger.info(f"第 {page_count} 页获取到 {contact_count} 个联系人")
            
            # 添加到总列表
            all_contacts.extend(contact_list)
            
            # 如果没有更多数据，退出循环
            if continue_flag == 0:
                logger.info("已获取所有联系人数据")
                break
                
        except Exception as e:
            logger.info(f"请求第 {page_count} 页时发生错误: {str(e)}")
            break
    
    # 分类联系人
    gh_contacts = []      # 公众号（以gh_开头）
    friend_contacts = []   # 其他联系人
    
    for contact in all_contacts:
        if contact.startswith('gh_'):
            gh_contacts.append(contact)
        else:
            friend_contacts.append(contact)
    
    # 统计信息
    total_count = len(all_contacts)
    gh_count = len(gh_contacts)
    other_count = len(friend_contacts)
    
    logger.info("=" * 50)
    logger.info("获取完成！")
    logger.info(f"总页数: {page_count}")
    logger.info(f"总联系人数: {total_count}")
    logger.info(f"公众号数量 (gh_开头): {gh_count}")
    logger.info(f"其他联系人数量: {other_count}")
    logger.info("=" * 50)
    
    return gh_contacts, friend_contacts
