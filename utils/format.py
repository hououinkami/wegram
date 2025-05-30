import logging
logger = logging.getLogger(__name__)

import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

# 解析XML内容
def xml_to_json(xml_string, as_string=False):
    try:
        # 处理XML声明
        if xml_string.startswith('<?xml'):
            xml_string = xml_string.split('?>', 1)[1]
            
        # 解析 XML 字符串
        root = ET.fromstring(xml_string)
        
        # 递归函数，将 XML 元素转换为字典
        def element_to_dict(element):
            result = {}
            
            # 添加属性
            if element.attrib:
                for key, value in element.attrib.items():
                    result[key] = value
            
            # 处理子元素
            children = list(element)
            if children:
                for child in children:
                    child_name = child.tag
                    child_dict = element_to_dict(child)
                    
                    # 如果同名子元素已存在，则转为列表
                    if child_name in result:
                        if not isinstance(result[child_name], list):
                            result[child_name] = [result[child_name]]
                        result[child_name].append(child_dict)
                    else:
                        result[child_name] = child_dict
            
            # 添加文本内容（如果有且没有其他属性或子元素）
            text = element.text
            if text and text.strip():
                if not result:  # 如果没有其他属性或子元素
                    return text.strip()
                result["_text"] = text.strip()
                
            return result
        
        # 转换为字典
        json_data = {root.tag: element_to_dict(root)}
        
        # 根据参数决定返回JSON字符串还是Python字典
        if as_string:
            return json.dumps(json_data, ensure_ascii=False, indent=2)
        else:
            return json_data
        
    except Exception as e:
        print(f"解析 XML 时出错: {e}")
        return None
    
def xml_to_obj(xml_string):
    try:
        # 处理XML声明
        if xml_string.startswith('<?xml'):
            xml_string = xml_string.split('?>', 1)[1]
            
        # 解析 XML 字符串
        root = ET.fromstring(xml_string)
        
        # 递归函数，将 XML 元素转换为字典
        def element_to_dict(element):
            result = {}
            
            # 添加属性
            if element.attrib:
                for key, value in element.attrib.items():
                    result[key] = value
            
            # 处理子元素
            children = list(element)
            if children:
                for child in children:
                    child_name = child.tag
                    child_dict = element_to_dict(child)
                    
                    # 如果同名子元素已存在，则转为列表
                    if child_name in result:
                        if not isinstance(result[child_name], list):
                            result[child_name] = [result[child_name]]
                        result[child_name].append(child_dict)
                    else:
                        result[child_name] = child_dict
            
            # 添加文本内容（如果有且没有其他属性或子元素）
            text = element.text
            if text and text.strip():
                if not result:  # 如果没有其他属性或子元素
                    return text.strip()
                result["_text"] = text.strip()
                
            return result
        
        # 转换为字典
        json_data = {root.tag: element_to_dict(root)}
        
        # 将字典转换为对象，以便使用点表示法访问
        def dict_to_obj(d):
            if isinstance(d, dict):
                # 创建SimpleNamespace对象
                obj = SimpleNamespace()
                for key, value in d.items():
                    # 处理Python关键字作为属性名的情况
                    if key in ['from', 'class', 'import', 'global', 'return', 'try', 'except', 'finally', 'raise', 'def', 'if', 'else', 'elif', 'for', 'while', 'in', 'is', 'not', 'and', 'or', 'lambda', 'with', 'as', 'assert', 'break', 'continue', 'del', 'exec', 'pass', 'print', 'yield']:
                        key = key + '_'
                    # 处理包含特殊字符或以数字开头的属性名
                    if not key.isalnum() or key[0].isdigit() or '-' in key:
                        key = 'attr_' + key
                    # 递归处理嵌套的字典和列表
                    setattr(obj, key, dict_to_obj(value))
                return obj
            elif isinstance(d, list):
                # 处理列表中的每个元素
                return [dict_to_obj(item) for item in d]
            else:
                # 基本类型直接返回
                return d
        
        # 转换整个字典为对象
        obj = dict_to_obj(json_data)
        return obj
        
    except Exception as e:
        print(f"解析 XML 时出错: {e}")
        return None

# 提取公众号文章
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
                                                # 若无冒号结尾，新增冒号
                                                if key_word and not key_word.endswith((":", "：")):
                                                    key_word = key_word + ": "
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

def escape_html_chars(text):
    """
    转义文本中的HTML特殊字符，用于Telegram Bot API的HTML格式
    
    Args:
        text (str): 需要转义的文本
        
    Returns:
        str: 转义后的文本
    """
    if not isinstance(text, str):
        return str(text)
    
    # 先处理 & 符号（必须最先处理，避免重复转义）
    text = text.replace('&', '&amp;')
    
    # 处理 < 和 > 符号
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    
    return text