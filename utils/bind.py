import asyncio
import json
import logging
import os
import shutil
import tempfile
from io import BytesIO
from typing import Dict, Optional

import requests
from PIL import Image
from telethon import TelegramClient
from telethon.tl.functions.messages import CreateChatRequest, EditChatAdminRequest, EditChatPhotoRequest, GetDialogFiltersRequest, UpdateDialogFilterRequest
from telethon.tl.types import InputChatUploadedPhoto, InputPeerChat, InputPeerChannel, DialogFilter, TextWithEntities

import config
from service.userbot import get_client

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
            except Exception as e:
                logger.warning(f"清理临时session文件失败: {e}")
            finally:
                self.temp_session_path = None

    async def _process_image_from_url(self, url: str) -> Optional[BytesIO]:
        """从URL下载图片并处理为BytesIO对象"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            # 下载图片
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: requests.get(url, headers=headers, timeout=30))
            response.raise_for_status()
            
            # 处理图片
            processed_image = await loop.run_in_executor(
                None,
                self._process_avatar_image,
                response.content
            )
            
            return processed_image
            
        except Exception as e:
            logger.error(f"下载处理图片失败: {e}")
            return None

    def _process_avatar_image(self, image_data: bytes, min_size: int = 512) -> BytesIO:
        """处理头像图片内容"""
        try:
            # 打开图片
            img = Image.open(BytesIO(image_data))
            
            # 获取原始尺寸
            width, height = img.size
            
            # 检查是否需要处理
            needs_processing = False
            
            # 转换为RGB（如果是RGBA或其他格式）
            if img.mode != 'RGB':
                img = img.convert('RGB')
                needs_processing = True
            
            # 如果图片太小，放大到最小尺寸
            if width < min_size or height < min_size:
                # 保持纵横比，放大到最小尺寸
                ratio = max(min_size / width, min_size / height)
                new_width = int(width * ratio)
                new_height = int(height * ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                needs_processing = True
            
            # 裁剪为正方形（如果不是正方形）
            if img.width != img.height:
                size = min(img.size)
                left = (img.width - size) // 2
                top = (img.height - size) // 2
                img = img.crop((left, top, left + size, top + size))
                needs_processing = True
            
            # 强制处理为 JPEG 格式（Telegram 头像要求）
            needs_processing = True
            
            # 保存处理后的图片到BytesIO
            output = BytesIO()
            img.save(output, format='JPEG', quality=95)
            output.seek(0)
            return output
            
        except Exception as e:
            logger.error(f"图片处理失败: {e}")
            # 尝试简单的格式转换作为fallback
            try:
                img = Image.open(BytesIO(image_data))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                output = BytesIO()
                img.save(output, format='JPEG', quality=95)
                output.seek(0)
                return output
            except Exception as fallback_error:
                logger.error(f"Fallback处理也失败: {fallback_error}")
                return BytesIO(image_data)

    async def _set_group_avatar(self, client, chat_id: int, avatar_url: str) -> bool:
        """设置群组头像"""
        if not avatar_url:
            return True
        
        try:
            # 下载并处理图片
            processed_image_data = await self._process_image_from_url(avatar_url)
            
            if not processed_image_data:
                logger.error("下载或处理头像图片失败")
                return False
            
            try:
                if chat_id < 0:  # 普通群组ID是负数
                    original_chat_id = abs(chat_id)
                    
                    # 确保BytesIO指针在开始位置
                    processed_image_data.seek(0)
                    
                    # 直接使用 BytesIO 上传，指定正确的文件名
                    uploaded_photo = await client.upload_file(
                        processed_image_data,
                        file_name="avatar.jpg"
                    )
                    
                    await client(EditChatPhotoRequest(
                        chat_id=original_chat_id,
                        photo=InputChatUploadedPhoto(uploaded_photo)
                    ))
                
                return True
                
            except Exception as e:
                logger.error(f"上传头像失败: {e}")
                return False
                            
        except Exception as e:
            logger.error(f"设置群组头像失败: {e}")
            return False
    
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
                # 尝试从监控服务获取
                else:
                    monitor = get_client()
                    if monitor and hasattr(monitor, 'target_bot_id'):
                        bot_entity = await client.get_entity(monitor.target_bot_id)
                    else:
                        raise Exception("无法获取机器人信息，请在config中设置BOT_USERNAME或确保BOT_TOKEN格式正确")
                        
            except Exception as e:
                logger.error(f"获取机器人实体失败: {e}")
                # 如果都失败了，尝试搜索机器人
                if hasattr(config, 'BOT_TOKEN') and config.BOT_TOKEN:
                    try:
                        # 通过API获取机器人信息
                        bot_token = config.BOT_TOKEN
                        response = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
                        if response.status_code == 200:
                            bot_info = response.json()
                            if bot_info.get('ok'):
                                bot_username = bot_info['result']['username']
                                bot_entity = await client.get_entity(bot_username)
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
            
            # 设置 bot 为管理员
            bot_is_admin = False
            try:
                original_chat_id = abs(chat_id)
                await client(EditChatAdminRequest(
                    chat_id=original_chat_id,
                    user_id=bot_entity,
                    is_admin=True
                ))
                bot_is_admin = True
            except Exception as e:
                logger.error(f"设置 bot 为管理员失败: {e}")
            
            # 设置群组头像
            avatar_set = False
            if avatar_url:
                avatar_set = await self._set_group_avatar(client, chat_id, avatar_url)
            
            # 将群组移动到 WeChat 文件夹
            moved_to_folder = False
            try:
                moved_to_folder = await self._move_chat_to_folder(client, chat_id, config.WECHAT_FOLDER_NAME)
                if not moved_to_folder:
                    logger.warning(f"移动群组到文件夹失败，但群组创建成功")
            except Exception as folder_error:
                logger.error(f"移动群组到文件夹时出错: {folder_error}")

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
    
    async def _move_chat_to_folder(self, client, chat_id: int, folder_name: str = config.WECHAT_FOLDER_NAME) -> bool:
        """将聊天移动到指定文件夹"""
        try:            
            # 获取现有文件夹
            filters_result = await client(GetDialogFiltersRequest())
            
            # 查找目标文件夹（排除默认文件夹）
            target_filter = None
            for filter_obj in filters_result.filters:
                # 跳过默认文件夹类型
                if filter_obj.__class__.__name__ == 'DialogFilterDefault':
                    continue
                if hasattr(filter_obj, 'title'):
                    # 处理 TextWithEntities 类型的标题
                    title_text = filter_obj.title.text if hasattr(filter_obj.title, 'text') else str(filter_obj.title)
                    if title_text == folder_name:
                        target_filter = filter_obj
                        break
            
            # 获取聊天实体
            chat_entity = await client.get_entity(chat_id)
            
            # 根据聊天类型创建适当的 InputPeer
            if hasattr(chat_entity, 'access_hash'):
                # 超级群组或频道
                input_peer = InputPeerChannel(chat_entity.id, chat_entity.access_hash)
            else:
                # 普通群组
                input_peer = InputPeerChat(abs(chat_id))
            
            # 如果文件夹不存在，创建新的
            if target_filter is None:
                # 生成新的filter ID
                existing_ids = []
                for f in filters_result.filters:
                    if hasattr(f, 'id') and f.__class__.__name__ != 'DialogFilterDefault':
                        existing_ids.append(f.id)
                
                new_id = max(existing_ids) + 1 if existing_ids else 1
                
                # 创建 TextWithEntities 对象作为标题
                title_obj = TextWithEntities(text=folder_name, entities=[])
                
                # 创建新的 DialogFilter
                target_filter = DialogFilter(
                    id=new_id,
                    title=title_obj,  # 使用 TextWithEntities 对象
                    emoticon="📱",
                    pinned_peers=[],
                    include_peers=[input_peer],  # 直接包含我们的聊天
                    exclude_peers=[],
                    contacts=False,
                    non_contacts=False,
                    groups=True,
                    broadcasts=False,
                    bots=False,
                    exclude_muted=False,
                    exclude_read=False,
                    exclude_archived=False
                )
                
                # 创建新文件夹
                await client(UpdateDialogFilterRequest(
                    id=new_id,
                    filter=target_filter
                ))
                
                return True
            
            else:
                # 文件夹已存在，检查群组是否已经在其中
                peer_already_exists = False
                for existing_peer in target_filter.include_peers:
                    try:
                        if hasattr(existing_peer, 'chat_id') and hasattr(input_peer, 'chat_id'):
                            if existing_peer.chat_id == input_peer.chat_id:
                                peer_already_exists = True
                                break
                        elif hasattr(existing_peer, 'channel_id') and hasattr(input_peer, 'channel_id'):
                            if existing_peer.channel_id == input_peer.channel_id:
                                peer_already_exists = True
                                break
                    except:
                        continue
                
                if peer_already_exists:
                    return True
                
                # 添加群组到现有文件夹
                new_include_peers = list(target_filter.include_peers)
                new_include_peers.append(input_peer)
                
                # 创建更新的文件夹对象，保持原有的 TextWithEntities 标题
                updated_filter = DialogFilter(
                    id=target_filter.id,
                    title=target_filter.title,  # 保持原有的 TextWithEntities 对象
                    emoticon=getattr(target_filter, 'emoticon', "📱"),
                    pinned_peers=list(target_filter.pinned_peers),
                    include_peers=new_include_peers,
                    exclude_peers=list(target_filter.exclude_peers),
                    contacts=getattr(target_filter, 'contacts', False),
                    non_contacts=getattr(target_filter, 'non_contacts', False),
                    groups=getattr(target_filter, 'groups', True),
                    broadcasts=getattr(target_filter, 'broadcasts', False),
                    bots=getattr(target_filter, 'bots', False),
                    exclude_muted=getattr(target_filter, 'exclude_muted', False),
                    exclude_read=getattr(target_filter, 'exclude_read', False),
                    exclude_archived=getattr(target_filter, 'exclude_archived', False)
                )
                
                # 更新文件夹
                await client(UpdateDialogFilterRequest(
                    id=target_filter.id,
                    filter=updated_filter
                ))
                
                return True
            
        except Exception as e:
            logger.error(f"移动群组到文件夹失败: {e}")
            
            return False


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
