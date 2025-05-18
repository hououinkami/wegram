import logging
logger = logging.getLogger(__name__)

def extract_url_items(json_dict):
    result = ""
    
    try:
        # 首先检查是否有appmsg元素
        if "msg" in json_dict and "appmsg" in json_dict["msg"]:
            appmsg = json_dict["msg"]["appmsg"]
            
            # 检查是否有mmreader和item列表
            if "mmreader" in appmsg:
                mmreader = appmsg["mmreader"]
                
                # 检查是否有category和item列表
                if "category" in mmreader:
                    category = mmreader["category"]
                    
                    if "item" in category:
                        items = category["item"]
                        # 确保items是列表
                        if not isinstance(items, list):
                            items = [items]
                            
                        # 遍历item列表提取每篇文章的标题和URL
                        for item in items:
                            if "title" in item and "url" in item:
                                title = item["title"]
                                url = item["url"]
                                summary = ""
                                
                                # 尝试获取summary
                                if "summary" in item and item["summary"]:
                                    summary = escape_markdown_chars(item["summary"])
                                # 如果没有summary，尝试从template_detail和line_content中获取
                                elif "template_detail" in mmreader and "line_content" in mmreader["template_detail"]:
                                    line_content = mmreader["template_detail"]["line_content"]
                                    if "lines" in line_content and "line" in line_content["lines"]:
                                        line_items = line_content["lines"]["line"]
                                        if not isinstance(line_items, list):
                                            line_items = [line_items]
                                        
                                        # 从每一行中提取key和value，并组合
                                        line_texts = []
                                        for line_item in line_items:
                                            if "key" in line_item and "value" in line_item:
                                                key_word = line_item["key"].get("word", "")
                                                value_word = line_item["value"].get("word", "")
                                                if isinstance(key_word, dict) and "_text" in key_word:
                                                    key_word = key_word["_text"]
                                                if isinstance(value_word, dict) and "_text" in value_word:
                                                    value_word = value_word["_text"]
                                                line_texts.append(f"{escape_markdown_chars(key_word)}{escape_markdown_chars(value_word)}")
                                        
                                        # 将所有行文本以换行符连接
                                        if line_texts:
                                            summary = "\n>".join(line_texts)
                                
                                # 转义markdown特殊字符
                                title = escape_markdown_chars(title)
                                url = escape_markdown_chars(url)
                                summary = summary
                                # 如果title或url是字典而不是字符串，尝试获取_text属性
                                if isinstance(title, dict) and "_text" in title:
                                    title = title["_text"]
                                if isinstance(url, dict) and "_text" in url:
                                    url = url["_text"]
                                result += f"[{title}]({url})\n>{summary}\n"
            
            # 如果没有mmreader或者mmreader中没有item，则使用主文章的标题和URL
            if not result and "title" in appmsg and "url" in appmsg:
                title = appmsg["title"]
                url = appmsg["url"]
                summary = appmsg.get("des", "")  # 使用get方法，如果不存在则返回空字符串
                # 转义markdown特殊字符
                title = escape_markdown_chars(title)
                url = escape_markdown_chars(url)
                summary = escape_markdown_chars(summary)
                # 如果title或url是字典而不是字符串，尝试获取_text属性
                if isinstance(title, dict) and "_text" in title:
                    title = title["_text"]
                if isinstance(url, dict) and "_text" in url:
                    url = url["_text"]
                result += f"[{title}]({url})\n>{summary}\n"
    
    except Exception as e:
        print(f"提取标题和URL时出错: {e}")
    
    return result

# 字符串添加转义符匹配TG的markdown输出
def escape_markdown_chars(text):
    """
    检测字符串中是否包含 Markdown 特殊字符，并为它们添加转义符 \
    
    参数:
        text (str): 需要处理的字符串
        
    返回:
        str: 处理后的字符串，特殊字符前添加了转义符 \
    """
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    result = ""
    
    for char in text:
        if char in special_chars:
            result += "\\" + char
        else:
            result += char
            
    return result