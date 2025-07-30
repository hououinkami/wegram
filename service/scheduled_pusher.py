import asyncio
import logging
from datetime import datetime

import config
from api import wechat_contacts
from api.wechat_api import wechat_api
from api.telegram_sender import telegram_sender
from service.telethon_client import get_user_id
from utils.daily_scheduler import DailyRandomScheduler
from utils.tools import get_60s

logger = logging.getLogger(__name__)

# 全局变量
_scheduler_instance = None

async def main():
    """调度器服务主函数"""
    global _scheduler_instance
    
    async def get_news():
        """获取60s新闻"""
        try:
            news = get_60s("both")

            # 检查新闻日期是否为今天
            today = datetime.now().strftime('%Y-%m-%d')
            if news.get('date') != today:
                logger.info(f"📅 新闻日期 {news.get('date')} 不是今天 {today}，跳过推送")
                return False  # 返回False表示未推送

            # 发送到微信
            # payload = {
            #     "At": "",
            #     "Content": news['text'],
            #     "ToWxid": "ocean446",
            #     "Type": 1,
            #     "Wxid": config.MY_WXID
            # }
            # await wechat_api("SEND_TEXT", payload)

            time_now = datetime.now().strftime("%Y-%-m-%-d %H:%M")
            user_info = await wechat_contacts.get_user_info("ocean446")
            contact_name = user_info.name
            avatar_url = user_info.avatar_url
            xml_text = f"""<appmsg><title></title><des></des><type>19</type><url></url><appattach><cdnthumbaeskey /><aeskey /></appattach><recorditem><![CDATA[<recordinfo><info></info><datalist count="1"><dataitem datatype="1" dataid=""><srcMsgLocalid></srcMsgLocalid><sourcetime>{time_now}</sourcetime><fromnewmsgid></fromnewmsgid><srcMsgCreateTime></srcMsgCreateTime><datadesc>{news['text']}</datadesc><dataitemsource><hashusername></hashusername></dataitemsource><sourcename>{contact_name}</sourcename><sourceheadurl>{avatar_url}</sourceheadurl></dataitem></datalist><desc>{news['text']}</desc><fromscene>2</fromscene></recordinfo>]]></recorditem></appmsg>"""
            payload = {
                "ToWxid": "ocean446",
                "Type": 49,
                "Wxid": config.MY_WXID,
                "Xml": xml_text
            }
            await wechat_api("SEND_APP", payload)

            # 发送到Telegram
            tg_user_id = get_user_id()
            await telegram_sender.send_text(tg_user_id, news['html'])

            return True  # 返回True表示成功推送
            
        except Exception as e:
            logger.error(f"❌ 获取新闻失败: {e}")
            return False

    try:
        # 创建并启动调度器
        _scheduler_instance = DailyRandomScheduler("08:55", "09:05", get_news)
        await _scheduler_instance.start()
        
        logger.info("📰 调度器服务已启动，将在每天 08:55-09:05 之间随机推送新闻")
        
        # 等待调度器任务完成
        await _scheduler_instance.scheduler_task
        
    except asyncio.CancelledError:
        logger.info("📅 调度器服务被取消")
        raise
    finally:
        if _scheduler_instance:
            await _scheduler_instance.stop()

async def shutdown():
    """关闭调度器服务"""
    global _scheduler_instance
    logger.info("🔴 正在关闭调度器服务...")
    if _scheduler_instance:
        await _scheduler_instance.stop()
        _scheduler_instance = None
    logger.info("🔴 调度器服务已关闭")

if __name__ == "__main__":
    asyncio.run(main())
