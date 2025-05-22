import logging
import json
import config
from typing import Dict, Any
from api.base import wechat_api

def heartbeat(wxid):
    api_path="/Login/HeartBeat"
    query={"wxid": wxid}
    response = wechat_api(api_path=api_path, query_params=query)
    return response

def get_profile(wxid):
    api_path="/User/GetContractProfile"
    query={"wxid": wxid}
    response = wechat_api(api_path=api_path, query_params=query)
    return response

def twice_login(wxid):
    api_path="/Login/TwiceAutoAuth"
    body={"Wxid": wxid}
    response = wechat_api(api_path=api_path, body=body)
    return response

def newinit(wxid, max_synckey = "", current_synckey = ""):
    api_path="/Login/Newinit"
    # query={"wxid": wxid}
    body = {
        "wxid": wxid,
        "MaxSynckey": max_synckey,
        "CurrentSynckey": current_synckey
    }
    response = wechat_api(api_path=api_path, body = body)

    if response.status != 200:
        return None
    
    # 检查内容类型
    content_type = response.headers.get('Content-Type', '')
    if 'application/json' not in content_type and 'text/json' not in content_type:
        # 尝试读取文本内容以便调试
        text = response.text()
        return None
    
    # 解析JSON响应
    json_resp = response.json()

    if json_resp.get("Success"):
        return json_resp.get("Data")
    
    else:
        return None

def get_cached_info(wxid):
    api_path="/Login/GetCacheInfo"
    query={"wxid": wxid}
    response = wechat_api(api_path=api_path, query_params=query)
    return response

def awaken_login(wxid):
    api_path="/Login/Awaken"
    body={"Wxid": wxid}
    response = wechat_api(api_path=api_path, body=body)
    return response

def get_qr_code():
    api_path="/Login/GetQR"
    body={
        "DeviceID": "49c6a982f2c5abedcb8e78a55a59a8a7",
        "DeviceName": "\u30a2\u30af\u30bb\u30b9\u30dd\u30a4\u30f3\u30c8"
    }
    response = wechat_api(api_path=api_path, body=body)
    return response

def get_cached_info():
    api_path="/Login/CheckQR"
    qr_result = get_qr_code()
    query={"uuid": qr_result["uuid"]}
    response = wechat_api(api_path=api_path, query_params=query)
    return response
