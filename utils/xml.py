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