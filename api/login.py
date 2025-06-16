import logging

import config
from api.base import wechat_api

logger = logging.getLogger(__name__)

async def heartbeat(wxid):
    api_path="/Login/HeartBeat"
    query={"wxid": wxid}
    response = await wechat_api(api_path=api_path, query_params=query)
    return response

async def get_profile(wxid):
    api_path="/User/GetContractProfile"
    query={"wxid": wxid}
    response = await wechat_api(api_path=api_path, query_params=query)
    return response

async def twice_login(wxid):
    api_path="/Login/LoginTwiceAutoAuth"
    query={"wxid": wxid}
    response = await wechat_api(api_path=api_path, query_params=query)
    return response
