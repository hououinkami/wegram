import asyncio
import os
import json
import tempfile
import requests
import logging
import shutil
from typing import Optional, Dict
from telethon import TelegramClient
from telethon.tl.functions.messages import CreateChatRequest
from telethon.tl.functions.messages import EditChatAdminRequest, EditChatPhotoRequest
from telethon.tl.types import InputChatUploadedPhoto

import config

logger = logging.getLogger(__name__)

class TempTelegramClient:
    """临时 Telegram 客户端，用于执行特定操作"""
    
    def __init__(self):
        # 原始session路径
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        self.original_session_path = os.path.join(project_root, 'sessions', 'tg_session.session')
        self.temp_session_path = None
        
    def _create_temp_session(self):
        """创建临时session文件副本"""
        try:
            if not os.path.exists(self.original_session_path):
                logger.warning(f"原始session文件不存在: {self.original_session_path}")
                return None
            
            # 创建临时session文件
            temp_fd, temp_path = tempfile.mkstemp(suffix='.session', prefix='temp_tg_')
            os.close(temp_fd)  # 关闭文件描述符
            
            # 复制session文件
            shutil.copy2(self.original_session_path, temp_path)
            
            # 同时复制可能存在的.session-journal文件
            journal_path = self.original_session_path + '-journal'
            if os.path.exists(journal_path):
                shutil.copy2(journal_path, temp_path + '-journal')
            
            logger.info(f"已创建临时session副本: {temp_path}")
            self.temp_session_path = temp_path
            return temp_path
            
        except Exception as e:
            logger.error(f"创建临时session失败: {e}")
            return None
    
    def _cleanup_temp_session(self):
        """清理临时session文件"""
        if self.temp_session_path and os.path.exists(self.temp_session_path):
            try:
                os.unlink(self.temp_session_path)
                # 清理可能的journal文件
                journal_path = self.temp_session_path + '-journal'
                if os.path.exists(journal_path):
                    os.unlink(journal_path)
                logger.info(f"已清理临时session文件: {self.temp_session_path}")
            except Exception as e:
                logger.warning(f"清理临时session文件失败: {e}")
            finally:
                self.temp_session_path = None

    async def _download_image_from_url(self, url: str) -> Optional[str]:
        """从URL下载图片到临时文件"""
        try:
            logger.info(f"开始下载图片: {url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
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
                    ext = '.jpg'
            
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
            
            # 处理图片尺寸
            processed_image_path = None
            try:
                processed_image_path = await self._process_avatar_image_file(temp_image_path)
                if not processed_image_path:
                    logger.warning("图片处理失败，使用原图")
                    processed_image_path = temp_image_path
                
                if chat_id < 0:  # 普通群组ID是负数
                    original_chat_id = abs(chat_id)
                    uploaded_photo = await client.upload_file(processed_image_path)
                    await client(EditChatPhotoRequest(
                        chat_id=original_chat_id,
                        photo=InputChatUploadedPhoto(uploaded_photo)
                    ))
                    logger.info(f"成功设置群组头像")
                
                return True
                
            finally:
                # 清理临时文件
                for temp_file in [temp_image_path, processed_image_path]:
                    if temp_file and os.path.exists(temp_file):
                        try:
                            os.unlink(temp_file)
                            logger.info(f"已清理临时文件: {temp_file}")
                        except Exception as e:
                            logger.error(f"清理临时文件失败: {e}")
                            
        except Exception as e:
            logger.error(f"设置群组头像失败: {e}")
            return False

    async def _process_avatar_image_file(self, image_path: str, min_size: int = 512) -> str:
        """处理头像图片文件尺寸"""
        try:
            import asyncio
            from PIL import Image
            import tempfile
            import os
            
            def process_image():
                try:
                    # 检查原图尺寸
                    with Image.open(image_path) as img:
                        width, height = img.size
                        logger.info(f"原始图片尺寸: {width}x{height}")
                        
                        # 如果尺寸已经足够，直接返回原文件
                        if width >= min_size and height >= min_size:
                            logger.info("图片尺寸符合要求，无需处理")
                            return image_path
                        
                        # 需要处理的情况
                        logger.info(f"图片尺寸过小，将处理到至少 {min_size}x{min_size}")
                        
                        # 转换为RGB（如果是RGBA）
                        if img.mode == 'RGBA':
                            img = img.convert('RGB')
                        
                        # 如果图片太小，放大到最小尺寸
                        if width < min_size or height < min_size:
                            # 保持纵横比，放大到最小尺寸
                            ratio = max(min_size / width, min_size / height)
                            new_width = int(width * ratio)
                            new_height = int(height * ratio)
                            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                            logger.info(f"放大后尺寸: {new_width}x{new_height}")
                        
                        # 裁剪为正方形（取中心部分）
                        size = min(img.size)
                        left = (img.width - size) // 2
                        top = (img.height - size) // 2
                        img = img.crop((left, top, left + size, top + size))
                        logger.info(f"裁剪后尺寸: {size}x{size}")
                        
                        # 保存处理后的图片到临时文件
                        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
                            img.save(temp_file.name, format='JPEG', quality=95)
                            logger.info(f"处理后的图片保存到: {temp_file.name}")
                            return temp_file.name
                            
                except Exception as e:
                    logger.error(f"图片处理过程中出错: {e}")
                    return None
            
            # 在线程池中处理图片（避免阻塞）
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, process_image)
            return result
            
        except Exception as e:
            logger.error(f"图片处理失败: {e}")
            return None
    
    async def _save_chat_wxid_mapping(self, wxid: str, name: str, chat_id: int, avatar_url: str = None):
        """保存群组ID和微信ID的映射关系到contact.json"""
        is_group = wxid.endswith('@chatroom')
        
        try:
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            contact_json_path = os.path.join(parent_dir, 'contact.json')
            
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
            
            contacts = await loop.run_in_executor(None, _read_contacts)
            
            # 检查是否已存在该映射
            for contact in contacts:
                if contact.get('wxId') == wxid and contact.get('chatId') == chat_id:
                    logger.info(f"映射已存在: {wxid} -> {chat_id}")
                    return
            
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
            
            loop = asyncio.get_event_loop()
            
            def _read_and_check():
                if os.path.exists(contact_json_path):
                    with open(contact_json_path, 'r', encoding='utf-8') as f:
                        contacts = json.load(f)
                        
                    for contact in contacts:
                        if contact.get('wxId') == wxid and contact.get('chatId'):
                            return contact
                return None
            
            return await loop.run_in_executor(None, _read_and_check)
            
        except Exception as e:
            logger.error(f"检查映射失败: {e}")
            return None
        
    async def create_group_with_bot(self, wxid: str, contact_name: str, 
                               description: str = "", avatar_url: str = None) -> Dict:
        """创建群组并添加机器人"""
        client = None
        try:
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
            
            # 创建临时session副本
            temp_session_path = self._create_temp_session()
            if not temp_session_path:
                raise Exception("无法创建临时session文件")
            
            # 使用临时session创建客户端
            client = TelegramClient(
                temp_session_path, 
                config.API_ID, 
                config.API_HASH,
                device_model=getattr(config, 'DEVICE_MODEL', 'WeGram')
            )
            
            await client.start()
            
            # 获取机器人实体 - 使用BOT_TOKEN获取机器人用户名
            bot_entity = None
            try:
                # 从BOT_TOKEN解析机器人ID
                if hasattr(config, 'BOT_TOKEN') and config.BOT_TOKEN:
                    # BOT_TOKEN格式: bot_id:token
                    bot_id = config.BOT_TOKEN.split(':')[0]
                    bot_entity = await client.get_entity(int(bot_id))
                    logger.info(f"通过Token解析获取机器人ID: {bot_id}")
                
                # 尝试从监控服务获取
                else:
                    from service.tg2wx import get_client
                    monitor = get_client()
                    if monitor and hasattr(monitor, 'target_bot_id'):
                        bot_entity = await client.get_entity(monitor.target_bot_id)
                        logger.info(f"从监控服务获取机器人ID: {monitor.target_bot_id}")
                    else:
                        raise Exception("无法获取机器人信息，请在config中设置BOT_USERNAME或确保BOT_TOKEN格式正确")
                        
            except Exception as e:
                logger.error(f"获取机器人实体失败: {e}")
                # 如果都失败了，尝试搜索机器人
                if hasattr(config, 'BOT_TOKEN') and config.BOT_TOKEN:
                    try:
                        # 通过API获取机器人信息
                        import requests
                        bot_token = config.BOT_TOKEN
                        response = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
                        if response.status_code == 200:
                            bot_info = response.json()
                            if bot_info.get('ok'):
                                bot_username = bot_info['result']['username']
                                bot_entity = await client.get_entity(bot_username)
                                logger.info(f"通过API获取机器人用户名: @{bot_username}")
                            else:
                                raise Exception(f"Bot API返回错误: {bot_info}")
                        else:
                            raise Exception(f"Bot API请求失败: {response.status_code}")
                    except Exception as api_error:
                        logger.error(f"通过API获取机器人信息失败: {api_error}")
                        raise Exception("无法获取机器人信息，请检查BOT_TOKEN是否正确")
                else:
                    raise Exception("未配置BOT_TOKEN或BOT_USERNAME")
            
            if not bot_entity:
                raise Exception("无法获取机器人实体")
            
            # 创建群组
            group_name = f"{contact_name}"
            logger.info(f"开始创建群组: {group_name}")
            
            result = await client(CreateChatRequest(
                users=[bot_entity], 
                title=group_name
            ))
            
            # 获取群组ID
            chat_id = None
            if hasattr(result, 'chats') and result.chats:
                chat = result.chats[0]
                chat_id = -chat.id
            elif hasattr(result, 'updates') and hasattr(result.updates, 'chats') and result.updates.chats:
                chat = result.updates.chats[0]
                chat_id = -chat.id
            
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
                raise Exception("无法获取创建的群组ID")
            
            logger.info(f"成功创建普通群组，ID: {chat_id}")
            
            # 设置 bot 为管理员
            bot_is_admin = False
            try:
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
                avatar_set = await self._set_group_avatar(client, chat_id, avatar_url)
            
            # 保存映射关系
            await self._save_chat_wxid_mapping(wxid, contact_name, chat_id, avatar_url)
            
            return {
                'success': True, 
                'chat_id': chat_id, 
                'group_name': group_name,
                'group_type': 'group', 
                'bot_invited': True, 
                'bot_is_admin': bot_is_admin,
                'avatar_set': avatar_set,
                'mapping_updated': True
            }
            
        except Exception as e:
            logger.error(f"创建群组失败: {e}")
            return {'success': False, 'error': str(e)}
            
        finally:
            if client:
                await client.disconnect()
            # 清理临时session文件
            self._cleanup_temp_session()


def create_group_sync(wxid: str, contact_name: str, description: str = "", avatar_url: str = None):
    """同步方式创建群组"""
    temp_client = TempTelegramClient()
    
    # 在新的事件循环中运行
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        result = loop.run_until_complete(
            temp_client.create_group_with_bot(wxid, contact_name, description, avatar_url)
        )
        return result
    finally:
        loop.close()
