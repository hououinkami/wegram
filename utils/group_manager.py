import asyncio
import logging
import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import aiosqlite

import config
from api.wechat_api import wechat_api

logger = logging.getLogger(__name__)


class GroupMemberManager:
    """群成员管理器 - SQLite优化版本（自动初始化，时间错峰）"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            self.db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "database", 
                "group.db"
            )
        else:
            self.db_path = db_path
        
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # 内存缓存
        self._display_name_cache: Dict[str, Dict[str, Tuple[str, datetime]]] = {}
        self._cache_ttl = timedelta(hours=1)  # 缓存1小时
        
        # 定时更新配置
        self._update_scheduler_task: Optional[asyncio.Task] = None
        self._update_interval = timedelta(hours=6)  # 每6小时更新一次
        self._update_offset_range = timedelta(minutes=30)  # 随机偏移±30分钟
        self._pending_updates: Set[str] = set()
        
        # 初始化标志和锁
        self._initialized = False
        self._initializing = False
        self._init_lock = asyncio.Lock()
    
    async def _ensure_initialized(self):
        """确保已初始化（线程安全）"""
        if self._initialized:
            return
        
        async with self._init_lock:
            if self._initialized:  # 双重检查
                return
            
            if self._initializing:
                # 如果正在初始化，等待完成
                while self._initializing:
                    await asyncio.sleep(0.1)
                return
            
            self._initializing = True
            try:
                await self._init_database()
                await self._start_update_scheduler()
                self._initialized = True
                logger.info("✅ 全局群组管理器 初始化完成")
            except Exception as e:
                logger.error(f"❌ 群组管理器初始化失败: {e}")
                raise
            finally:
                self._initializing = False
    
    async def _init_database(self):
        """初始化数据库表和索引"""
        async with aiosqlite.connect(self.db_path) as db:
            # 创建群组表（删除了 update_priority 字段）
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chatrooms (
                    chatroom_id TEXT PRIMARY KEY,
                    server_version INTEGER DEFAULT 0,
                    member_count INTEGER DEFAULT 0,
                    last_update INTEGER DEFAULT 0,
                    cache_expiry INTEGER DEFAULT 0
                )
            """)
            
            # 创建成员表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chatroom_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    nickname TEXT,
                    displayname TEXT,
                    FOREIGN KEY (chatroom_id) REFERENCES chatrooms(chatroom_id) ON DELETE CASCADE,
                    UNIQUE(chatroom_id, username)
                )
            """)
            
            # 创建优化索引（删除了优先级索引）
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_members_fast_lookup 
                ON members(chatroom_id, username)
            """)
            
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_members_chatroom 
                ON members(chatroom_id)
            """)
            
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_chatrooms_expiry 
                ON chatrooms(cache_expiry)
            """)
            
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_chatrooms_update 
                ON chatrooms(last_update ASC)
            """)
            
            await db.commit()
            logger.debug("数据库表和索引创建完成")
    
    async def get_display_name(self, chatroom_id: str, username: str) -> str:
        """获取群成员显示名称 - 高性能版本（自动初始化）"""
        # 自动初始化
        await self._ensure_initialized()
        
        if not chatroom_id or not username:
            return "未知用户"
        
        # 1. 检查内存缓存
        if chatroom_id in self._display_name_cache:
            if username in self._display_name_cache[chatroom_id]:
                display_name, cache_time = self._display_name_cache[chatroom_id][username]
                if datetime.now() - cache_time < self._cache_ttl:
                    return display_name
        
        # 2. 查询数据库
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT m.displayname, m.nickname, c.cache_expiry
                    FROM members m 
                    JOIN chatrooms c ON m.chatroom_id = c.chatroom_id
                    WHERE m.chatroom_id = ? AND m.username = ?
                """, (chatroom_id, username)) as cursor:
                    row = await cursor.fetchone()
                    
                    if row:
                        display_name, nickname, cache_expiry = row
                        current_time = int(datetime.now().timestamp())
                        
                        # 检查缓存是否过期
                        if cache_expiry > current_time:
                            result = display_name or nickname or "未知用户"
                            self._cache_display_name(chatroom_id, username, result)
                            return result
        
        except Exception as e:
            logger.error(f"❌ 查询显示名称失败: {e}")
        
        # 3. 缓存未命中，立即更新群组信息
        logger.debug(f"缓存未命中，立即更新群组: {chatroom_id}")
        update_success = await self.update_group_members(chatroom_id, force=True)
        
        if update_success:
            # 更新后再次查询
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    async with db.execute("""
                        SELECT m.displayname, m.nickname
                        FROM members m 
                        WHERE m.chatroom_id = ? AND m.username = ?
                    """, (chatroom_id, username)) as cursor:
                        row = await cursor.fetchone()
                        
                        if row:
                            display_name, nickname = row
                            result = display_name or nickname or "未知用户"
                            self._cache_display_name(chatroom_id, username, result)
                            return result
            except Exception as e:
                logger.error(f"❌ 更新后查询显示名称失败: {e}")
        
        return "未知用户"
    
    async def update_group_members(self, chatroom_id: str, force: bool = False) -> bool:
        """更新群成员信息（使用正确的API调用方式，增强时间错峰）"""
        # 自动初始化
        await self._ensure_initialized()
        
        if not chatroom_id:
            return False
        
        try:
            # 检查是否需要更新
            if not force and not await self._should_update_group(chatroom_id):
                logger.debug(f"群组 {chatroom_id} 缓存仍有效，跳过更新")
                return True
            
            # 构建payload - 使用您原文件的方式
            payload = {
                "QID": chatroom_id,
                "Wxid": config.MY_WXID  # 需要导入config
            }
            
            # 获取群成员信息 - 使用您原文件的API调用方式
            logger.debug(f"开始更新群组成员信息: {chatroom_id}")
            group_member_response = await wechat_api("GROUP_MEMBER", payload)
            
            if not group_member_response or "Data" not in group_member_response:
                logger.warning(f"⚠️ 获取群成员信息失败: {chatroom_id}")
                return False
            
            # 提取数据 - 使用您原文件的数据结构
            data = group_member_response["Data"]
            server_version = data.get("ServerVersion", 0)
            member_count = data.get("NewChatroomData", {}).get("MemberCount", 0)
            members_data = data.get("NewChatroomData", {}).get("ChatRoomMember", {})
            
            if not members_data:
                logger.warning(f"⚠️ 群成员数据为空: {chatroom_id}")
                return False
            
            # 解析成员信息
            members = []
            for member in members_data:
                if member:
                    members.append({
                        "username": member.get("UserName", ""),
                        "nickname": member.get("NickName", ""),
                        "displayname": member.get("DisplayName", "")
                    })
            
            if not members:
                logger.warning(f"⚠️ 未解析到有效成员数据: {chatroom_id}")
                return False
            
            # 批量更新数据库（增强时间错峰）
            current_time = int(datetime.now().timestamp())
            # 缓存24小时 + 随机0-1小时偏移，实现时间错峰
            cache_expiry = current_time + (24 * 3600) + random.randint(0, 3600)
            
            async with aiosqlite.connect(self.db_path) as db:
                # 开始事务
                await db.execute("BEGIN TRANSACTION")
                
                try:
                    # 更新或插入群组信息（删除了 update_priority）
                    await db.execute("""
                        INSERT OR REPLACE INTO chatrooms 
                        (chatroom_id, server_version, member_count, last_update, cache_expiry)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        chatroom_id,
                        server_version,
                        len(members),
                        current_time,
                        cache_expiry
                    ))
                    
                    # 删除旧成员数据
                    await db.execute("DELETE FROM members WHERE chatroom_id = ?", (chatroom_id,))
                    
                    # 批量插入新成员数据
                    member_data = [
                        (chatroom_id, member["username"], member["nickname"], member["displayname"])
                        for member in members
                    ]
                    
                    await db.executemany("""
                        INSERT INTO members (chatroom_id, username, nickname, displayname)
                        VALUES (?, ?, ?, ?)
                    """, member_data)
                    
                    # 提交事务
                    await db.commit()
                    
                    # 清理内存缓存
                    if chatroom_id in self._display_name_cache:
                        del self._display_name_cache[chatroom_id]
                    
                    # 从待更新列表中移除
                    self._pending_updates.discard(chatroom_id)
                    
                    logger.info(f"✅ 群组 {chatroom_id} 成员信息更新完成，共 {len(members)} 人 (ServerVersion: {server_version})")
                    return True
                    
                except Exception as e:
                    # 回滚事务
                    await db.execute("ROLLBACK")
                    raise e
            
        except Exception as e:
            logger.error(f"❌ 更新群成员信息失败 {chatroom_id}: {e}")
            return False
    
    async def _should_update_group(self, chatroom_id: str) -> bool:
        """检查群组是否需要更新"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT cache_expiry FROM chatrooms WHERE chatroom_id = ?
                """, (chatroom_id,)) as cursor:
                    row = await cursor.fetchone()
                    
                    if not row:
                        return True  # 新群组需要更新
                    
                    cache_expiry = row[0]
                    current_time = int(datetime.now().timestamp())
                    return cache_expiry <= current_time
        
        except Exception as e:
            logger.error(f"❌ 检查群组更新状态失败: {e}")
            return True  # 出错时默认需要更新
    
    async def _start_update_scheduler(self):
        """启动定时更新调度器"""
        if self._update_scheduler_task:
            return
        
        self._update_scheduler_task = asyncio.create_task(self._update_scheduler_loop())
        logger.info("⏰ 群组定时更新调度器已启动")
    
    async def _update_scheduler_loop(self):
        """定时更新循环（增强时间错峰）"""
        while True:
            try:
                # 随机延迟，错峰更新
                delay = random.randint(300, 1800)  # 5-30分钟随机延迟
                await asyncio.sleep(delay)
                
                # 执行批量更新
                await self._batch_update_groups()
                
                # 等待下一个更新周期（增强随机偏移）
                base_interval = self._update_interval.total_seconds()
                offset = random.randint(
                    -int(self._update_offset_range.total_seconds()),
                    int(self._update_offset_range.total_seconds())
                )
                next_update_delay = base_interval + offset
                
                logger.info(f"⏰ 下次群组批量更新将在 {next_update_delay/3600:.1f} 小时后执行")
                await asyncio.sleep(next_update_delay)
            
            except asyncio.CancelledError:
                logger.info("🔴 定时更新调度器已停止")
                break
            except Exception as e:
                logger.error(f"⚠️ 定时更新调度器异常: {e}")
                await asyncio.sleep(300)  # 出错后等待5分钟再继续
    
    async def _batch_update_groups(self):
        """批量更新群组（删除优先级，纯时间错峰）"""
        try:
            # 获取需要更新的群组（按更新时间排序，删除优先级）
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT chatroom_id, cache_expiry
                    FROM chatrooms 
                    WHERE cache_expiry <= ?
                    ORDER BY last_update ASC
                    LIMIT 20
                """, (int(datetime.now().timestamp()),)) as cursor:
                    groups_to_update = await cursor.fetchall()
            
            if not groups_to_update:
                logger.debug("没有需要更新的群组")
                return
            
            logger.debug(f"开始批量更新 {len(groups_to_update)} 个群组")
            
            # 分批更新，避免API调用过于频繁
            batch_size = 5
            for i in range(0, len(groups_to_update), batch_size):
                batch = groups_to_update[i:i + batch_size]
                
                # 并发更新当前批次
                tasks = [
                    self.update_group_members(chatroom_id, force=True)
                    for chatroom_id, _ in batch  # 注意这里改为两个变量
                ]
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # 统计结果
                success_count = sum(1 for r in results if r is True)
                logger.info(f"✅ 批次更新完成: {success_count}/{len(batch)} 成功")
                
                # 批次间延迟（增强随机性）
                if i + batch_size < len(groups_to_update):
                    delay = random.randint(10, 30)  # 10-30秒随机延迟
                    await asyncio.sleep(delay)
            
            logger.info("✅ 批量更新完成")
        
        except Exception as e:
            logger.error(f"❌ 批量更新群组失败: {e}")
    
    def _cache_display_name(self, chatroom_id: str, username: str, display_name: str):
        """缓存显示名称到内存"""
        if chatroom_id not in self._display_name_cache:
            self._display_name_cache[chatroom_id] = {}
        
        self._display_name_cache[chatroom_id][username] = (display_name, datetime.now())
        
        # 限制缓存大小，防止内存泄漏
        if len(self._display_name_cache[chatroom_id]) > 1000:
            # 移除最旧的50%缓存
            items = list(self._display_name_cache[chatroom_id].items())
            items.sort(key=lambda x: x[1][1])  # 按时间排序
            keep_count = len(items) // 2
            self._display_name_cache[chatroom_id] = dict(items[-keep_count:])
    
    async def get_group_info(self, chatroom_id: str) -> Optional[Dict]:
        """获取群组信息（自动初始化）"""
        await self._ensure_initialized()
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT chatroom_id, server_version, member_count, last_update, cache_expiry
                    FROM chatrooms WHERE chatroom_id = ?
                """, (chatroom_id,)) as cursor:
                    row = await cursor.fetchone()
                    
                    if not row:
                        return None
                    
                    return {
                        'chatroom_id': row[0],
                        'server_version': row[1],
                        'member_count': row[2],
                        'last_update': datetime.fromtimestamp(row[3]),
                        'cache_expiry': datetime.fromtimestamp(row[4]),
                        'is_expired': row[4] <= int(datetime.now().timestamp())
                    }
        
        except Exception as e:
            logger.error(f"❌ 获取群组信息失败: {e}")
            return None
    
    async def get_group_members(self, chatroom_id: str) -> List[Dict]:
        """获取群组所有成员（自动初始化）"""
        await self._ensure_initialized()
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT username, nickname, displayname
                    FROM members WHERE chatroom_id = ?
                    ORDER BY displayname, nickname, username
                """, (chatroom_id,)) as cursor:
                    rows = await cursor.fetchall()
                    
                    return [
                        {
                            'username': row[0],
                            'nickname': row[1],
                            'displayname': row[2]
                        }
                        for row in rows
                    ]
        
        except Exception as e:
            logger.error(f"❌ 获取群组成员失败: {e}")
            return []
    
    async def search_user_across_groups(self, keyword: str, limit: int = 50) -> List[Dict]:
        """跨群搜索用户（自动初始化）"""
        await self._ensure_initialized()
        
        if not keyword:
            return []
        
        try:
            search_pattern = f"%{keyword}%"
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT DISTINCT m.username, m.nickname, m.displayname, m.chatroom_id
                    FROM members m
                    WHERE m.displayname LIKE ? OR m.nickname LIKE ? OR m.username LIKE ?
                    ORDER BY m.displayname, m.nickname
                    LIMIT ?
                """, (search_pattern, search_pattern, search_pattern, limit)) as cursor:
                    rows = await cursor.fetchall()
                    
                    return [
                        {
                            'username': row[0],
                            'nickname': row[1],
                            'displayname': row[2],
                            'chatroom_id': row[3]
                        }
                        for row in rows
                    ]
        
        except Exception as e:
            logger.error(f"❌ 跨群搜索用户失败: {e}")
            return []
    
    async def get_statistics(self) -> Dict:
        """获取统计信息（自动初始化）"""
        await self._ensure_initialized()
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 群组统计
                async with db.execute("SELECT COUNT(*) FROM chatrooms") as cursor:
                    total_groups = (await cursor.fetchone())[0]
                
                # 成员统计
                async with db.execute("SELECT COUNT(*) FROM members") as cursor:
                    total_members = (await cursor.fetchone())[0]
                
                # 过期群组统计
                current_time = int(datetime.now().timestamp())
                async with db.execute("""
                    SELECT COUNT(*) FROM chatrooms WHERE cache_expiry <= ?
                """, (current_time,)) as cursor:
                    expired_groups = (await cursor.fetchone())[0]
                
                return {
                    'total_groups': total_groups,
                    'total_members': total_members,
                    'expired_groups': expired_groups,
                    'cache_hit_groups': total_groups - expired_groups,
                    'memory_cache_size': sum(len(cache) for cache in self._display_name_cache.values()),
                    'pending_updates': len(self._pending_updates),
                    'initialized': self._initialized
                }
        
        except Exception as e:
            logger.error(f"❌ 获取统计信息失败: {e}")
            return {'initialized': self._initialized}
    
    async def cleanup_expired_cache(self):
        """清理过期缓存（自动初始化，删除优先级条件）"""
        await self._ensure_initialized()
        
        try:
            current_time = int(datetime.now().timestamp())
            
            async with aiosqlite.connect(self.db_path) as db:
                # 删除过期的群组和成员数据（删除优先级条件）
                await db.execute("""
                    DELETE FROM chatrooms WHERE cache_expiry <= ?
                """, (current_time - 7 * 24 * 3600,))  # 删除过期7天以上的数据
                
                await db.commit()
            
            # 清理内存缓存
            now = datetime.now()
            for chatroom_id in list(self._display_name_cache.keys()):
                cache = self._display_name_cache[chatroom_id]
                expired_users = [
                    username for username, (_, cache_time) in cache.items()
                    if now - cache_time > self._cache_ttl
                ]
                
                for username in expired_users:
                    del cache[username]
                
                if not cache:
                    del self._display_name_cache[chatroom_id]
            
            logger.info("🗑️ 过期缓存清理完成")
        
        except Exception as e:
            logger.error(f"❌ 清理过期缓存失败: {e}")
    
    async def shutdown(self):
        """优雅关闭"""
        
        if self._update_scheduler_task:
            self._update_scheduler_task.cancel()
            try:
                await self._update_scheduler_task
            except asyncio.CancelledError:
                pass
        
        # 清理缓存
        self._display_name_cache.clear()
        self._pending_updates.clear()
        self._initialized = False
        
        logger.info("🔴 群组管理器已关闭")


# 全局实例 - 自动初始化
group_manager = GroupMemberManager()


# 兼容性函数（可选）
async def initialize_group_manager():
    """手动初始化群管理器（可选，因为已经自动初始化）"""
    await group_manager._ensure_initialized()


# 优雅关闭函数
async def shutdown_group_manager():
    """关闭群管理器"""
    await group_manager.shutdown()