import asyncio
import logging
import os
import random
import sqlite3
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Any, Union

import aiohttp
import jwt

import config
from api.telegram_sender import telegram_sender
from api.wechat_api import wechat_api
from service.telethon_client import get_user_id

logger = logging.getLogger(__name__)

@dataclass
class QWeatherError(Exception):
    """和风天气API错误类"""
    status_code: int
    error_type: str
    title: str
    detail: str
    invalid_params: Optional[list] = None
    
    def __str__(self):
        return f"QWeatherError({self.status_code}): {self.title} - {self.detail}"

class ExponentialBackoff:
    """指数退避算法实现"""
    
    def __init__(self, base_delay: float = 1.0, max_delay: float = 60.0, max_retries: int = 5):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.attempt = 0
    
    def should_retry(self, status_code: int) -> bool:
        """判断是否应该重试"""
        return status_code in [429, 500] and self.attempt < self.max_retries
    
    def get_delay(self) -> float:
        """计算下次重试的延迟时间"""
        if self.attempt == 0:
            delay = 0
        else:
            delay = min(
                self.base_delay * (2 ** (self.attempt - 1)) + random.uniform(0, 1),
                self.max_delay
            )
        
        self.attempt += 1
        return delay
    
    def reset(self):
        """重置重试计数器"""
        self.attempt = 0

async def generate_jwt():
    """生成JWT token"""
    private_key = f"""-----BEGIN PRIVATE KEY-----
{config.QWEATHER_PRIVATE_KEY}
-----END PRIVATE KEY-----"""

    payload = {
        'iat': int(time.time()) - 30,
        'exp': int(time.time()) + 900,
        'sub': config.QWEATHER_PROJECT_ID
    }
    headers = {
        'kid': config.QWEATHER_KEY_ID
    }

    jwt_encoded = jwt.encode(payload, private_key, algorithm='EdDSA', headers=headers)
    return jwt_encoded

def parse_qweather_error(status_code: int, response_data: Union[Dict, str]) -> QWeatherError:
    """解析和风天气API错误响应"""
    if isinstance(response_data, str):
        return QWeatherError(
            status_code=status_code,
            error_type="UNKNOWN",
            title="HTTP Error",
            detail=response_data or f"HTTP {status_code} Error"
        )
    
    if isinstance(response_data, dict) and "error" in response_data:
        error_info = response_data["error"]
        return QWeatherError(
            status_code=error_info.get("status", status_code),
            error_type=error_info.get("type", "UNKNOWN"),
            title=error_info.get("title", "Unknown Error"),
            detail=error_info.get("detail", "No detail provided"),
            invalid_params=error_info.get("invalidParams")
        )
    
    return QWeatherError(
        status_code=status_code,
        error_type="UNKNOWN",
        title="Unknown Error",
        detail=str(response_data)
    )

async def qweather_api_request(
    path: str,
    path_params: Optional[Dict[str, Any]] = None,
    query_params: Optional[Dict[str, Any]] = None,
    method: str = "GET",
    timeout: int = 30,
    enable_retry: bool = True
    ) -> Dict[str, Any]:
    """异步请求和风天气API，支持指数退避重试"""
    
    if path_params:
        for key, value in path_params.items():
            path = path.replace(f"{{{key}}}", str(value))
    
    url = f"{config.QWEATHER_HOST.rstrip('/')}{path}"
    backoff = ExponentialBackoff() if enable_retry else None
    
    while True:
        try:
            jwt_token = await generate_jwt()
            
            headers = {
                'Authorization': f'Bearer {jwt_token}',
                'Content-Type': 'application/json',
                'User-Agent': 'QWeather-API-Client/1.0'
            }
            
            timeout_config = aiohttp.ClientTimeout(total=timeout)
            
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    params=query_params
                ) as response:
                    
                    if response.status == 200:
                        if backoff:
                            backoff.reset()
                        return await response.json()
                    
                    try:
                        if response.content_type == 'application/problem+json':
                            error_data = await response.json()
                        else:
                            error_data = await response.text()
                    except:
                        error_data = f"HTTP {response.status} Error"
                    
                    qweather_error = parse_qweather_error(response.status, error_data)
                    
                    if response.status == 400:
                        logger.error(f"客户端错误: {qweather_error}")
                        if qweather_error.invalid_params:
                            logger.error(f"无效参数: {qweather_error.invalid_params}")
                        raise qweather_error
                    
                    elif response.status == 401:
                        logger.error(f"认证失败: {qweather_error}")
                        raise qweather_error
                    
                    elif response.status == 403:
                        logger.error(f"权限错误: {qweather_error}")
                        raise qweather_error
                    
                    elif response.status == 404:
                        logger.error(f"资源不存在: {qweather_error}")
                        raise qweather_error
                    
                    elif response.status == 429:
                        logger.warning(f"请求过多: {qweather_error}")
                        if backoff and backoff.should_retry(response.status):
                            delay = backoff.get_delay()
                            logger.info(f"将在 {delay:.2f} 秒后重试 (第 {backoff.attempt} 次)")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            raise qweather_error
                    
                    elif response.status >= 500:
                        logger.warning(f"服务器错误: {qweather_error}")
                        if backoff and backoff.should_retry(response.status):
                            delay = backoff.get_delay()
                            logger.info(f"将在 {delay:.2f} 秒后重试 (第 {backoff.attempt} 次)")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            raise qweather_error
                    
                    else:
                        logger.error(f"未知错误: {qweather_error}")
                        raise qweather_error
                        
        except asyncio.TimeoutError:
            error_msg = f"请求超时: {url}"
            logger.error(error_msg)
            if backoff and backoff.should_retry(408):
                delay = backoff.get_delay()
                logger.info(f"超时重试，将在 {delay:.2f} 秒后重试 (第 {backoff.attempt} 次)")
                await asyncio.sleep(delay)
                continue
            else:
                raise asyncio.TimeoutError(error_msg)
                
        except aiohttp.ClientError as e:
            error_msg = f"网络请求错误: {str(e)}"
            logger.error(error_msg)
            if backoff and backoff.should_retry(500):
                delay = backoff.get_delay()
                logger.info(f"网络错误重试，将在 {delay:.2f} 秒后重试 (第 {backoff.attempt} 次)")
                await asyncio.sleep(delay)
                continue
            else:
                raise aiohttp.ClientError(error_msg)

# 便捷的包装函数
async def get_weather_now(location: str, lang: str = "zh") -> Dict[str, Any]:
    """获取实时天气"""
    return await qweather_api_request(
        path="/v7/weather/now",
        query_params={"location": location, "lang": lang}
    )

async def get_weather_forecast(location: str, days: int = 3, lang: str = "zh") -> Dict[str, Any]:
    """获取天气预报"""
    return await qweather_api_request(
        path=f"/v7/weather/{days}d",
        query_params={"location": location, "lang": lang}
    )

async def get_air_quality(location: str, lang: str = "zh") -> Dict[str, Any]:
    """获取空气质量"""
    return await qweather_api_request(
        path="/v7/air/now",
        query_params={"location": location, "lang": lang}
    )

async def get_hourly_forecast(location: str, hours: int = 24, lang: str = "zh") -> Dict[str, Any]:
    """获取逐小时天气预报"""
    valid_hours = [24, 72, 168]
    if hours not in valid_hours:
        raise QWeatherError(
            status_code=400,
            error_type="INVALID_PARAMETER",
            title="Invalid Hours Parameter",
            detail=f"Hours must be one of {valid_hours}, got {hours}",
            invalid_params=["hours"]
        )
    
    return await qweather_api_request(
        path=f"/v7/weather/{hours}h",
        query_params={"location": location, "lang": lang}
    )

async def get_minutely_rain(location: str, lang: str = "zh") -> Dict[str, Any]:
    """获取分钟级降水预报"""    
    return await qweather_api_request(
        path=f"/v7/minutely/5m",
        query_params={"location": location, "lang": lang}
    )

async def get_weather_warning(location: str = config.LOCATION_ID, lang: str = "zh") -> Dict[str, Any]:
    """获取天气预警"""
    return await qweather_api_request(
        path="/v7/warning/now",
        query_params={"location": location, "lang": lang}
    )

async def get_weather_warning_list(range_type: str = "cn", lang: str = "zh") -> Dict[str, Any]:
    """获取天气预警列表"""
    return await qweather_api_request(
        path="/v7/warning/list",
        query_params={"range": range_type, "lang": lang}
    )

async def get_complete_weather_info(
    location: str, 
    include_hourly: bool = True,
    include_warning: bool = True,
    forecast_days: int = 3,
    hourly_hours: int = 24,
    lang: str = "zh"
    ) -> Dict[str, Any]:
    """获取完整的天气信息"""
    tasks = []
    task_names = []
    
    # 实时天气
    tasks.append(get_weather_now(location, lang))
    task_names.append("current")
    
    # 天气预报
    tasks.append(get_weather_forecast(location, forecast_days, lang))
    task_names.append("forecast")
    
    # 逐小时预报
    if include_hourly:
        tasks.append(get_hourly_forecast(location, hourly_hours, lang))
        task_names.append("hourly")
    
    # 天气预警
    if include_warning:
        tasks.append(get_weather_warning(location, lang))
        task_names.append("warning")
    
    # 并发执行所有请求
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 组装结果
    weather_info = {}
    for name, result in zip(task_names, results):
        if isinstance(result, Exception):
            logger.error(f"获取{name}数据失败: {result}")
            weather_info[name] = {"error": str(result)}
        else:
            weather_info[name] = result
    
    return weather_info

# ==================== 预警监控系统 ====================

@dataclass
class WeatherWarning:
    """天气预警数据类"""
    id: str
    sender: str
    pub_time: str
    title: str
    start_time: str
    end_time: str
    status: str
    level: str
    severity: str
    severity_color: str
    type: str
    type_name: str
    urgency: str
    certainty: str
    text: str
    related: str = ""

class WeatherWarningDB:
    """天气预警数据库管理类"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    @contextmanager
    def get_connection(self):
        """数据库连接上下文管理器"""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()
    
    def init_database(self):
        """初始化数据库表"""
        # 确保数据库目录存在
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.info(f"创建数据库目录: {db_dir}")
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS weather_warnings (
                    id TEXT PRIMARY KEY,
                    sender TEXT,
                    pub_time TEXT,
                    title TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    status TEXT,
                    level TEXT,
                    severity TEXT,
                    severity_color TEXT,
                    type TEXT,
                    type_name TEXT,
                    urgency TEXT,
                    certainty TEXT,
                    text TEXT,
                    related TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
    
    def get_warning(self, warning_id: str) -> Optional[WeatherWarning]:
        """获取已存在的预警信息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM weather_warnings WHERE id = ?', (warning_id,))
            row = cursor.fetchone()
            
            if row:
                return WeatherWarning(
                    id=row[0], sender=row[1], pub_time=row[2], title=row[3],
                    start_time=row[4], end_time=row[5], status=row[6], level=row[7],
                    severity=row[8], severity_color=row[9], type=row[10], 
                    type_name=row[11], urgency=row[12], certainty=row[13],
                    text=row[14], related=row[15] or ""
                )
        return None
    
    def save_warning(self, warning: WeatherWarning):
        """保存或更新预警信息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO weather_warnings 
                (id, sender, pub_time, title, start_time, end_time, status, level, 
                severity, severity_color, type, type_name, urgency, certainty, text, related, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                warning.id, warning.sender, warning.pub_time, warning.title,
                warning.start_time, warning.end_time, warning.status, warning.level,
                warning.severity, warning.severity_color, warning.type, warning.type_name,
                warning.urgency, warning.certainty, warning.text, warning.related
            ))
            conn.commit()
    
    def get_cancelled_warnings(self, current_warning_ids: set) -> List[WeatherWarning]:
        """获取已取消的预警（在数据库中但不在当前预警列表中）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if current_warning_ids:
                # 构建占位符字符串
                placeholders = ','.join(['?' for _ in current_warning_ids])
                cursor.execute(
                    f'SELECT * FROM weather_warnings WHERE id NOT IN ({placeholders})',
                    list(current_warning_ids)
                )
            else:
                # 如果当前没有预警，返回所有数据库中的预警
                cursor.execute('SELECT * FROM weather_warnings')
            
            rows = cursor.fetchall()
            cancelled_warnings = []
            
            for row in rows:
                cancelled_warnings.append(WeatherWarning(
                    id=row[0], sender=row[1], pub_time=row[2], title=row[3],
                    start_time=row[4], end_time=row[5], status=row[6], level=row[7],
                    severity=row[8], severity_color=row[9], type=row[10], 
                    type_name=row[11], urgency=row[12], certainty=row[13],
                    text=row[14], related=row[15] or ""
                ))
            
            return cancelled_warnings

    def delete_warning(self, warning_id: str):
        """删除指定ID的预警记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM weather_warnings WHERE id = ?', (warning_id,))
            conn.commit()
            logger.info(f"已删除预警记录: {warning_id}")

class WeatherAlertFormatter:
    """预警消息格式化器"""
    
    COLOR_MAP = {
        'White': '白色',
        'Gray': '白色',
        'Blue': '蓝色', 
        'Yellow': '黄色',
        'Red': '红色',
        'Black': '黑色'
    }
    
    STATUS_MAP = {
        'Active': '激活',
        'Update': '更新', 
        'Cancel': '取消'
    }
    
    @classmethod
    def format_message(cls, warning: WeatherWarning) -> str:
        """格式化预警消息"""
        color_cn = cls.COLOR_MAP.get(warning.severity_color, warning.severity_color)
        emoji = cls._get_emoji(warning.status, color_cn)
        formatted_time = cls._format_time(warning.pub_time)
        current_time = datetime.now().strftime('%Y年%m月%d日 %H:%M')
        
        if warning.status == 'Cancel':
            message = f"""
{emoji} {warning.type_name}{color_cn}预警 [已取消]
取消时间: {current_time}
"""
            message_html = f"""
<blockquote>{emoji} {warning.type_name}{color_cn}预警 [已取消]</blockquote>
<blockquote>取消时间: {current_time}</blockquote>
"""
        else:
            message = f"""
{emoji} {warning.type_name}{color_cn}预警
发布时间: {formatted_time}
{warning.text}
"""
            message_html = f"""
<blockquote>{emoji} {warning.type_name}{color_cn}预警</blockquote>
<blockquote>发布时间: {formatted_time}</blockquote>
{warning.text}
"""
        return {
            "text": message.strip(),
            "html": message_html.strip()
        }
    
    @staticmethod
    def _get_emoji(status: str, color: str) -> str:
        """根据状态和颜色获取emoji"""
        if status == 'Cancel':
            return '🟢'
        
        emoji_map = {
            '黑色': '⚫️',
            '红色': '🔴',
            '黄色': '🟡',
            '蓝色': '🔵',
            '白色': '⚪️'
        }
        return emoji_map.get(color, '⚠️')
    
    @staticmethod
    def _format_time(pub_time: str) -> str:
        """格式化时间"""
        if not pub_time:
            return ''
        
        try:
            dt = datetime.fromisoformat(pub_time.replace('+08:00', ''))
            return dt.strftime('%Y年%m月%d日 %H:%M')
        except:
            return pub_time

class WeatherAlertMonitor:
    """天气预警监控器"""
    
    def __init__(self, location_id: str = config.LOCATION_ID, db_path: str = None):
        self.location_id = location_id
        
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "database", 
                "weather.db"
            )
        
        self.db = WeatherWarningDB(db_path)
        self.formatter = WeatherAlertFormatter()
    
    def _should_notify(self, warning: WeatherWarning, existing: Optional[WeatherWarning]) -> bool:
        """判断是否需要通知"""
        if not existing:
            return True
        
        if warning.status != existing.status:
            return True
        
        if warning.status == 'Update':
            key_fields = ['title', 'text', 'severity_color', 'level']
            for field in key_fields:
                if getattr(warning, field) != getattr(existing, field):
                    return True
        
        return False
    
    def _parse_warning_data(self, warning_data: Dict) -> WeatherWarning:
        """解析API返回的预警数据"""
        return WeatherWarning(
            id=warning_data.get('id', ''),
            sender=warning_data.get('sender', ''),
            pub_time=warning_data.get('pubTime', ''),
            title=warning_data.get('title', ''),
            start_time=warning_data.get('startTime', ''),
            end_time=warning_data.get('endTime', ''),
            status=warning_data.get('status', ''),
            level=warning_data.get('level', ''),
            severity=warning_data.get('severity', ''),
            severity_color=warning_data.get('severityColor', ''),
            type=warning_data.get('type', ''),
            type_name=warning_data.get('typeName', ''),
            urgency=warning_data.get('urgency', ''),
            certainty=warning_data.get('certainty', ''),
            text=warning_data.get('text', ''),
            related=warning_data.get('related', '')
        )
    
    async def check_alerts(self) -> List[str]:
        """检查预警并返回需要通知的消息列表"""
        try:
            warning_data = await get_weather_warning(self.location_id)
            warnings = warning_data.get('warning', [])
            
            notification_messages = []
            
            # 获取当前API返回的所有预警ID
            current_warning_ids = set()
            
            if warnings:
                logger.debug(f"获取到 {len(warnings)} 条预警信息")
                
                for warning_dict in warnings:
                    warning = self._parse_warning_data(warning_dict)
                    
                    if not warning.id:
                        continue
                    
                    current_warning_ids.add(warning.id)
                    existing_warning = self.db.get_warning(warning.id)
                    
                    if self._should_notify(warning, existing_warning):
                        message = self.formatter.format_message(warning)
                        notification_messages.append(message)
                        
                        if existing_warning:
                            logger.info(f"预警更新: {warning.title}")
                        else:
                            logger.info(f"新预警: {warning.title}")
                    
                    self.db.save_warning(warning)
            
            else:
                logger.debug("当前没有预警信息")
            
            # 检查数据库中已取消的预警（在数据库中但不在当前API返回中）
            cancelled_warnings = self.db.get_cancelled_warnings(current_warning_ids)
            
            for cancelled_warning in cancelled_warnings:
                # 生成取消消息
                cancelled_warning.status = 'Cancel'  # 修改状态为取消
                cancel_message = self.formatter.format_message(cancelled_warning)
                notification_messages.append(cancel_message)
                
                logger.info(f"预警已取消: {cancelled_warning.title}")
                
                # 从数据库中删除已取消的预警
                self.db.delete_warning(cancelled_warning.id)
            
            return notification_messages
            
        except QWeatherError as e:
            logger.error(f"和风天气API错误: {e}")
            return []
        except Exception as e:
            logger.error(f"其他错误: {e}")
            traceback.print_exc()
            return []

# ==================== 外部调用函数 ====================
async def get_and_send_alert(location: str = config.LOCATION_ID):
    """获取并发送预警信息"""
    monitor = WeatherAlertMonitor(location)
    messages = await monitor.check_alerts()
    
    if messages:
        for message in messages:
            await telegram_sender.send_text(get_user_id(), message["html"])
            payload = {
                "At": "",
                "Content": message["text"],
                "ToWxid": config.PUSH_WXID,
                "Type": 1,
                "Wxid": config.MY_WXID
            }
            await wechat_api("SEND_TEXT", payload)

async def get_and_send_rain(location: str = config.LOCATION_ID):
    """获取并发送分钟级降水信息"""
    try:
        # 获取分钟级降水数据
        result = await get_minutely_rain(location)
        logger.warning(result)
        # 解析降水数据
        minutely_data = result.get('minutely', [])
        summary = result.get('summary', '')
        
        if not minutely_data:
            no_data_msg = "📊 暂无分钟级降水预报数据"
            logger.info(no_data_msg)
            return
        
        # 格式化消息
        message = _format_rain_message(minutely_data, summary)
        
        # 发送到Telegram
        await telegram_sender.send_text(get_user_id(), message["html"])
        
        # 发送到微信
        payload = {
            "At": "",
            "Content": message["text"],
            "ToWxid": config.PUSH_WXID,
            "Type": 1,
            "Wxid": config.MY_WXID
        }
        await wechat_api("SEND_TEXT", payload)
        
    except QWeatherError as e:
        error_msg = f"❌ 和风天气API错误: {e}"
        logger.error(error_msg)
        await telegram_sender.send_text(get_user_id(), error_msg)
    except Exception as e:
        error_msg = f"❌ 获取降水信息失败: {str(e)}"
        logger.error(error_msg)
        traceback.print_exc()
        await telegram_sender.send_text(get_user_id(), error_msg)

def _format_rain_message(minutely_data: List[Dict], summary: str) -> Dict[str, str]:
    """格式化分钟级降水消息"""
    current_time = datetime.now().strftime('%Y年%m月%d日 %H:%M')
    
    # 解析降水强度
    rain_levels = []
    significant_changes = []
    
    for i, data in enumerate(minutely_data):
        time_str = data.get('fxTime', '')
        precip = float(data.get('precip', 0))
        type_desc = data.get('type', '')
        
        # 格式化时间（只显示时分）
        try:
            dt = datetime.fromisoformat(time_str.replace('+08:00', ''))
            time_display = dt.strftime('%H:%M')
        except:
            time_display = time_str
        
        # 降水强度等级
        if precip == 0:
            level = "无降水"
            emoji = "☀️"
        elif precip <= 0.25:
            level = "微量降水"
            emoji = "🌦️"
        elif precip <= 2.5:
            level = "小雨"
            emoji = "🌦️"
        elif precip <= 10:
            level = "中雨"
            emoji = "🌧️"
        elif precip <= 25:
            level = "大雨"
            emoji = "🌧️🌧️"
        else:
            level = "暴雨"
            emoji = "🌧️🌧️🌧️"
        
        rain_levels.append({
            'time': time_display,
            'precip': precip,
            'level': level,
            'emoji': emoji,
            'type': type_desc
        })
        
        # 检测显著变化（降水开始、结束或强度显著变化）
        if i == 0:
            if precip > 0:
                significant_changes.append(f"{time_display} {emoji} {level}开始 ({precip}mm/H)")
        else:
            prev_precip = float(minutely_data[i-1].get('precip', 0))
            
            # 降水开始
            if prev_precip == 0 and precip > 0:
                significant_changes.append(f"{time_display} {emoji} {level}开始 ({precip}mm/H)")
            # 降水结束
            elif prev_precip > 0 and precip == 0:
                significant_changes.append(f"{time_display} ☀️ 降水结束")
            # 强度显著变化（变化超过2.5mm/H）
            elif abs(precip - prev_precip) >= 2.5:
                if precip > prev_precip:
                    significant_changes.append(f"{time_display} {emoji} 降水增强至{level} ({precip}mm/H)")
                else:
                    significant_changes.append(f"{time_display} {emoji} 降水减弱至{level} ({precip}mm/H)")
    
    # 构建消息
    text_message = f"""🌧️ 降水预报"""
    html_message = f"""<blockquote>🌧️ 降水预报</blockquote>"""
    
    # 添加概况
    if summary and not summary.endswith("无降水"):
        text_message += f"概况: {summary}\n\n"
        html_message += f"<b>概况:</b> {summary}\n\n"
    
    # 添加显著变化
    if significant_changes:
        text_message += "⚡ 降水变化:\n"
        html_message += "<b>⚡ 降水变化:</b>\n"
        
        for change in significant_changes[:5]:  # 最多显示5个重要变化
            text_message += f"• {change}\n"
            html_message += f"• {change}\n"
        
        text_message += "\n"
        html_message += "\n"
    
    '''
    # 添加详细预报（每10分钟显示一次）
    text_message += "📊 详细预报:\n"
    html_message += "<b>📊 详细预报:</b>\n"
    
    for i, rain_info in enumerate(rain_levels):
        if i % 2 == 0:  # 每10分钟显示一次（假设数据是5分钟间隔）
            precip_str = f"{rain_info['precip']}mm/H" if rain_info['precip'] > 0 else ""
            text_message += f"{rain_info['time']} {rain_info['emoji']} {rain_info['level']} {precip_str}\n"
            html_message += f"{rain_info['time']} {rain_info['emoji']} {rain_info['level']} {precip_str}\n"
    
    # 添加统计信息
    total_precip = sum(float(data.get('precip', 0)) for data in minutely_data)
    max_precip = max(float(data.get('precip', 0)) for data in minutely_data)
    
    if total_precip > 0:
        text_message += f"\n📈 统计信息:\n"
        text_message += f"• 最大降水强度: {max_precip}mm/H\n"
        text_message += f"• 预计总降水量: {total_precip/12:.1f}mm (未来1小时)"  # 5分钟数据，12个点约1小时
        
        html_message += f"\n<b>📈 统计信息:</b>\n"
        html_message += f"• 最大降水强度: {max_precip}mm/H\n"
        html_message += f"• 预计总降水量: {total_precip/12:.1f}mm (未来1小时)"
    '''
    
    return {
        "text": text_message.strip(),
        "html": html_message.strip()
    }

