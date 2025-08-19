import asyncio
import logging
import os
import random
import sqlite3
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Any, Union

import aiohttp
import jwt

import config
from api.telegram_sender import telegram_sender
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
        # 429 (Too Many Requests) 和 500 (Server Error) 可以重试
        return status_code in [429, 500] and self.attempt < self.max_retries
    
    def get_delay(self) -> float:
        """计算下次重试的延迟时间"""
        if self.attempt == 0:
            delay = 0
        else:
            # 指数退避: base_delay * (2 ^ attempt) + 随机抖动
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
        # 404等情况可能没有JSON响应体
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
    
    # 兜底处理
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
    """
    异步请求和风天气API，支持指数退避重试
    
    Args:
        path: API路径，如 "/v7/weather/now"
        path_params: 路径参数，用于替换路径中的占位符
        query_params: 查询参数
        method: HTTP方法，默认GET
        timeout: 请求超时时间（秒）
        enable_retry: 是否启用重试机制
    
    Returns:
        API响应的JSON数据
    
    Raises:
        QWeatherError: 和风天气API错误
        asyncio.TimeoutError: 请求超时
        aiohttp.ClientError: 网络请求错误
    """
    
    # 处理路径参数
    if path_params:
        for key, value in path_params.items():
            path = path.replace(f"{{{key}}}", str(value))
    
    # 构建完整URL
    url = f"{config.QWEATHER_HOST.rstrip('/')}{path}"
    
    # 初始化退避算法
    backoff = ExponentialBackoff() if enable_retry else None
    
    while True:
        try:
            # 生成JWT token
            jwt_token = await generate_jwt()
            
            # 设置请求头
            headers = {
                'Authorization': f'Bearer {jwt_token}',
                'Content-Type': 'application/json',
                'User-Agent': 'QWeather-API-Client/1.0'
            }
            
            # 设置超时
            timeout_config = aiohttp.ClientTimeout(total=timeout)
            
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    params=query_params
                ) as response:
                    
                    # 成功响应
                    if response.status == 200:
                        if backoff:
                            backoff.reset()
                        return await response.json()
                    
                    # 解析错误响应
                    try:
                        if response.content_type == 'application/problem+json':
                            error_data = await response.json()
                        else:
                            error_data = await response.text()
                    except:
                        error_data = f"HTTP {response.status} Error"
                    
                    qweather_error = parse_qweather_error(response.status, error_data)
                    
                    # 处理不同类型的错误
                    if response.status == 400:
                        # 客户端错误，不重试
                        logger.error(f"客户端错误: {qweather_error}")
                        if qweather_error.invalid_params:
                            logger.error(f"无效参数: {qweather_error.invalid_params}")
                        raise qweather_error
                    
                    elif response.status == 401:
                        # 认证失败，不重试
                        logger.error(f"认证失败: {qweather_error}")
                        raise qweather_error
                    
                    elif response.status == 403:
                        # 权限相关错误，大部分不重试
                        logger.error(f"权限错误: {qweather_error}")
                        raise qweather_error
                    
                    elif response.status == 404:
                        # 资源不存在，不重试
                        logger.error(f"资源不存在: {qweather_error}")
                        raise qweather_error
                    
                    elif response.status == 429:
                        # 请求过多，可以重试
                        logger.warning(f"请求过多: {qweather_error}")
                        if backoff and backoff.should_retry(response.status):
                            delay = backoff.get_delay()
                            logger.info(f"将在 {delay:.2f} 秒后重试 (第 {backoff.attempt} 次)")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            raise qweather_error
                    
                    elif response.status >= 500:
                        # 服务器错误，可以重试
                        logger.warning(f"服务器错误: {qweather_error}")
                        if backoff and backoff.should_retry(response.status):
                            delay = backoff.get_delay()
                            logger.info(f"将在 {delay:.2f} 秒后重试 (第 {backoff.attempt} 次)")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            raise qweather_error
                    
                    else:
                        # 其他错误
                        logger.error(f"未知错误: {qweather_error}")
                        raise qweather_error
                        
        except asyncio.TimeoutError:
            error_msg = f"请求超时: {url}"
            logger.error(error_msg)
            if backoff and backoff.should_retry(408):  # 408 Request Timeout
                delay = backoff.get_delay()
                logger.info(f"超时重试，将在 {delay:.2f} 秒后重试 (第 {backoff.attempt} 次)")
                await asyncio.sleep(delay)
                continue
            else:
                raise asyncio.TimeoutError(error_msg)
                
        except aiohttp.ClientError as e:
            error_msg = f"网络请求错误: {str(e)}"
            logger.error(error_msg)
            if backoff and backoff.should_retry(500):  # 当作服务器错误处理
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

async def get_air_quality(location: str, lang: str = "zh") -> Dict[str, Any]:
    """获取预警"""
    return await qweather_api_request(
        path="/v7/air/now",
        query_params={"location": location, "lang": lang}
    )

async def get_hourly_forecast(location: str, hours: int = 24, lang: str = "zh") -> Dict[str, Any]:
    """
    获取逐小时天气预报
    
    Args:
        location: 地点，支持LocationID、经纬度、城市名等
        hours: 预报小时数，支持 24, 72, 168 小时
        lang: 语言，默认中文
    
    Returns:
        逐小时天气预报数据
    
    Raises:
        QWeatherError: 当hours参数不在支持范围内时
    """
    # 验证小时数参数
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

async def get_weather_warning(location: str = "101280601", lang: str = "zh") -> Dict[str, Any]:
    """
    获取天气预警
    
    Args:
        location: 地点，支持LocationID、经纬度、城市名等
        lang: 语言，默认中文
    
    Returns:
        天气预警数据
    """
    return await qweather_api_request(
        path="/v7/warning/now",
        query_params={"location": location, "lang": lang}
    )

async def get_weather_warning_list(range_type: str = "cn", lang: str = "zh") -> Dict[str, Any]:
    """
    获取天气预警列表
    
    Args:
        range_type: 查询范围
            - "cn": 中国
            - "hk": 香港
            - "mo": 澳门
            - "tw": 台湾
        lang: 语言，默认中文
    
    Returns:
        天气预警列表数据
    """
    return await qweather_api_request(
        path="/v7/warning/list",
        query_params={"range": range_type, "lang": lang}
    )

# 组合查询函数
async def get_complete_weather_info(
    location: str, 
    include_hourly: bool = True,
    include_warning: bool = True,
    forecast_days: int = 3,
    hourly_hours: int = 24,
    lang: str = "zh"
) -> Dict[str, Any]:
    """
    获取完整的天气信息（实时+预报+逐小时+预警）
    
    Args:
        location: 地点
        include_hourly: 是否包含逐小时预报
        include_warning: 是否包含天气预警
        forecast_days: 预报天数
        hourly_hours: 逐小时预报小时数
        lang: 语言
    
    Returns:
        包含所有天气信息的字典
    """
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

# 预警监控
async def get_shenzhen_alert():
    """深圳天气预警获取和处理函数"""
    
    # 预警颜色等级映射
    COLOR_MAP = {
        'White': '白色',
        'Blue': '蓝色', 
        'Yellow': '黄色',
        'Red': '红色',
        'Black': '黑色'
    }
    
    # 预警状态映射
    STATUS_MAP = {
        'Active': '激活',
        'Update': '更新', 
        'Cancel': '取消'
    }
    
    # 数据库路径
    weather_db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
        "database", 
        "weather.db"
    )
    def init_database():
        """初始化数据库表"""
        conn = sqlite3.connect(weather_db_path)
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
        conn.close()
    
    def get_existing_warning(warning_id: str) -> Optional[Dict]:
        """获取已存在的预警信息"""
        conn = sqlite3.connect(weather_db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM weather_warnings WHERE id = ?', (warning_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None
    
    def save_warning(warning: Dict):
        """保存或更新预警信息到数据库"""
        conn = sqlite3.connect(weather_db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO weather_warnings 
            (id, sender, pub_time, title, start_time, end_time, status, level, 
            severity, severity_color, type, type_name, urgency, certainty, text, related, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            warning.get('id'),
            warning.get('sender'),
            warning.get('pubTime'),
            warning.get('title'),
            warning.get('startTime'),
            warning.get('endTime'),
            warning.get('status'),
            warning.get('level'),
            warning.get('severity'),
            warning.get('severityColor'),
            warning.get('type'),
            warning.get('typeName'),
            warning.get('urgency'),
            warning.get('certainty'),
            warning.get('text'),
            warning.get('related')
        ))
        
        conn.commit()
        conn.close()
    
    def format_warning_message(warning: Dict) -> str:
        """格式化预警消息"""
        # 转换颜色等级
        color_cn = COLOR_MAP.get(warning.get('severityColor', ''), warning.get('severityColor', ''))
        
        # 根据状态选择合适的emoji
        status = warning.get('status', '')
        if status == 'Cancel':
            emoji = '🟢'  # 绿色表示取消
        elif color_cn == '黑色':
            emoji = '⚫️'
        elif color_cn == '红色':
            emoji = '🔴'  # 红色表示高级别预警
        elif color_cn == '黄色':
            emoji = '🟡'  # 黄色表示中级别预警
        elif color_cn == '蓝色':
            emoji = '🔵'  # 蓝色表示低级别预警
        elif color_cn == '白色':
            emoji = '⚪️'  # 蓝色表示低级别预警
        else:
            emoji = '⚠️'   # 默认警告符号
        
        # 格式化发布时间
        pub_time = warning.get('pubTime', '')
        if pub_time:
            try:
                # 解析ISO格式时间并转换为可读格式
                dt = datetime.fromisoformat(pub_time.replace('+08:00', ''))
                formatted_time = dt.strftime('%Y年%m月%d日 %H:%M')
            except:
                formatted_time = pub_time
        else:
            formatted_time = ''
        
        # 构建消息
        type_name = warning.get('typeName', '气象')
        text = warning.get('text', '')
        
        # 如果是取消状态，添加特殊标识
        if status == 'Cancel':
            message = f"""
{emoji} {type_name}{color_cn}预警 [已取消]
发布时间: {formatted_time}
{text}
"""
        else:
            message = f"""
{emoji} {type_name}{color_cn}预警
发布时间: {formatted_time}
{text}
"""
        
        return message.strip()
    
    def should_notify(warning: Dict, existing: Optional[Dict]) -> bool:
        """判断是否需要通知"""
        # 新预警
        if not existing:
            return True
        
        # 状态有变化（更新或取消）
        current_status = warning.get('status', '')
        existing_status = existing.get('status', '')
        
        if current_status != existing_status:
            return True
        
        # 如果是Update状态，检查内容是否有变化
        if current_status == 'Update':
            # 检查关键字段是否有变化
            key_fields = ['title', 'text', 'severity_color', 'level']
            for field in key_fields:
                if warning.get(field) != existing.get(field):
                    return True
        
        return False
    
    try:
        # 初始化数据库
        init_database()
        
        # 获取预警数据
        warning_bj = await get_weather_warning("101280601")
        warnings = warning_bj.get('warning', [])
        
        notification_messages = []
        
        if warnings:
            logger.info(f"获取到 {len(warnings)} 条预警信息")
            
            for warning in warnings:
                warning_id = warning.get('id')
                if not warning_id:
                    continue
                
                # 获取已存在的预警信息
                existing_warning = get_existing_warning(warning_id)
                
                # 判断是否需要通知
                if should_notify(warning, existing_warning):
                    # 生成通知消息
                    message = format_warning_message(warning)
                    notification_messages.append(message)
                    
                    # 打印日志
                    if existing_warning:
                        logger.info(f"预警更新: {warning.get('title', 'Unknown')}")
                    else:
                        logger.info(f"新预警: {warning.get('title', 'Unknown')}")
                
                # 保存到数据库
                save_warning(warning)
        
        else:
            logger.info("当前没有预警信息")
        
        return notification_messages
        
    except QWeatherError as e:
        logger.error(f"和风天气API错误: {e}")
        if hasattr(e, 'invalid_params') and e.invalid_params:
            logger.error(f"无效参数: {e.invalid_params}")
        return []
    except Exception as e:
        logger.error(f"其他错误: {e}")
        traceback.print_exc()
        return []

async def get_and_send_alert():
    """发送预警信息"""
    messages = await get_shenzhen_alert()
    
    if messages:
        for message in messages:
            await telegram_sender.send_text(get_user_id(), message)
