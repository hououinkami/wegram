import logging

import config
from api.wechat_api import wechat_api

logger = logging.getLogger(__name__)

async def heartbeat(wxid):
    api_name="HEART_BEAT"
    query={"wxid": wxid}
    response = await wechat_api(api_name, query_params=query)
    return response

async def get_profile(wxid):
    api_name="get_profile"
    query={"wxid": wxid}
    response = await wechat_api(api_name, query_params=query)
    return response

async def twice_login(wxid):
    api_name="TWICE_LOGIN"
    query={"wxid": wxid}
    response = await wechat_api(api_name, query_params=query)
    return response
