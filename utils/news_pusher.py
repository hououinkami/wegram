import logging

import requests

from utils.message_formatter import escape_html_chars

logger = logging.getLogger(__name__)

def get_60s(format_type="text"):
    """获取API内容并格式化为指定格式
    
    Args:
        url (str): API地址
        format_type (str): 输出格式类型
            - "text": 普通文本格式（默认）
            - "html": HTML blockquote格式
            - "both": 返回两种格式的字典
    
    Returns:
        str or dict: 根据format_type返回相应格式的内容
    """
    url="https://60s-api.viki.moe/v2/60s"

    try:       
        # 发送GET请求
        response = requests.get(url, timeout=10)
        
        # 检查响应状态码
        if response.status_code == 200:
            # 获取JSON数据
            data = response.json()
            
            if 'data' in data:
                news_data = data['data']
                date = news_data.get('date', 'N/A')
                news_list = news_data.get('news', [])
                
                # 构建普通文本格式
                text_format = "📰 每天60秒读懂世界\n"
                text_format += f"日期：{date}\n"
                
                # 构建HTML格式
                html_format = "<blockquote>📰 每天60秒读懂世界</blockquote>\n"
                html_format += f"<blockquote>日期：{date}</blockquote>\n"
                
                # 圈数字符号列表
                circle_numbers = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩', 
                                '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳']
                
                # 添加编号的新闻条目
                for i, news in enumerate(news_list):
                    if i < len(circle_numbers):  # 确保不超出圈数字符号范围
                        # 普通文本格式
                        text_format += f"{circle_numbers[i]}{news}\n"
                        # HTML格式
                        html_format += f"<blockquote>{circle_numbers[i]}{escape_html_chars(news)}</blockquote>\n"
                    else:
                        # 如果超出20条，使用普通数字
                        text_format += f"{i+1}. {news}\n"
                        html_format += f"<blockquote>{i+1}. {escape_html_chars(news)}</blockquote>\n"
                
                # 根据format_type返回相应格式
                if format_type == "text":
                    return text_format.strip()  # 去掉最后的换行符
                elif format_type == "html":
                    return html_format.strip()  # 去掉最后的换行符
                elif format_type == "both":
                    return {
                        "text": text_format.strip(),
                        "html": html_format.strip()
                    }
                else:
                    logger.warning(f"未知的格式类型: {format_type}，使用默认文本格式")
                    return text_format.strip()
                    
            else:
                logger.error("❌ API响应中没有找到data字段")
                return None
                
        else:
            logger.error(f"❌ 请求失败，状态码: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"❌ 错误: {e}")
        return None