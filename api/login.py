import logging
import json
import config
from typing import Dict, Any
from api.base import wechat_api

logger = logging.getLogger(__name__)

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
    api_path="/Login/LoginTwiceAutoAuth"
    query={"wxid": wxid}
    response = wechat_api(api_path=api_path, query_params=query)
    return response
