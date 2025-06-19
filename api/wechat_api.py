import asyncio
import json
import logging
from typing import Any, Dict, Optional

import aiohttp
import requests

import config

logger = logging.getLogger(__name__)

# API请求函数
async def wechat_api(api_path: str, body: Optional[Dict[str, Any]] = None, query_params: Optional[Dict[str, Any]] = None):
    api_url = f"{config.BASE_URL}{api_path}"
    try:
        # 设置超时时间
        timeout = aiohttp.ClientTimeout(total=30)  # 30秒超时
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url=api_url,
                json=body,  # 请求体数据
                params=query_params  # URL 查询参数
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    response_text = await response.text()
                    logger.error(f"API调用失败，状态码: {response.status}, 响应: {response_text}")
                    return False
                    
    except asyncio.TimeoutError:
        logger.error(f"API调用超时: {api_url}")
        return False
    except aiohttp.ClientError as e:
        logger.error(f"HTTP客户端错误: {e}")
        return False
    except Exception as e:
        logger.error(f"调用微信API时出错: {e}")
        return False

def wechat_api_sync(api_path: str, body: Dict[str, Any] = None, query_params: Dict[str, Any] = None):
    api_url = f"{config.BASE_URL}{api_path}"
    try:
        response = requests.post(
            url=api_url,
            json=body,  # 请求体数据
            params=query_params  # URL 查询参数
        )
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"API调用失败，状态码: {response.status_code}, 响应: {response.text}")
            return False
    except Exception as e:
        logger.error(f"调用微信API时出错: {e}")
        return False
