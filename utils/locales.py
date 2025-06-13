class LocaleConfig:
    LOCALES = {
        'ja': {
            'message_types': {
                1: "テキスト",
                3: "写真",
                34: "音声",
                43: "動画",
                42: "連絡先",
                47: "ステッカー",
                48: "位置",
                5: "リンク",
                6: "ファイル",
                19: "チャット履歴",
                33: "ミニプログラム",
                57: "引用",
                51: "チャンネル",
                66: "ファイル",
                2000: "送金",
                2001: "ラッキマネー",
                "revokemsg": "撤回",
                "pat": "軽く叩く",
                "VoIPBubbleMsg": "通話",
                "unknown": "不明"
            },
            'common': {
                'online': "🟢 WeChatがオンラインしました",
                'offline': "🔴 WeChatがオフラインしました",
                'revoke': "❌ 撤回失敗",
                'receive_on': "✅ 転送オン",
                'receive_off': "❌ 転送オフ"
            }
        },
        'zh': {
            'message_types': {
                1: "文本",
                3: "图片",
                34: "语音",
                43: "视频",
                42: "联系人",
                47: "表情",
                48: "位置",
                5: "链接",
                6: "文件",
                19: "聊天记录",
                33: "小程序",
                57: "引用",
                51: "视频号",
                66: "文件",
                2000: "转账",
                2001: "红包",
                "revokemsg": "撤回",
                "pat": "拍一拍",
                "VoIPBubbleMsg": "通话",
                "unknown": "未知"
            },
            'common': {
                'online': "🟢 WeChat已上线",
                'offline': "🔴 WeChat已离线",
                'revoke': "❌ 撤回失败",
                'receive_on': "✅ 转发开启",
                'receive_off': "❌ 转发关闭"
            }
        }
    }
    
    @classmethod
    def get_message_types(cls, locale='ja'):
        return cls.LOCALES.get(locale, {}).get('message_types', {})
    
    @classmethod
    def get_common(cls, locale='ja'):
        return cls.LOCALES.get(locale, {}).get('common', {})

class Locale:
    def __init__(self, locale='ja'):
        self.locale = locale
        self.type_map = LocaleConfig.get_message_types(locale)
        self.common_map = LocaleConfig.get_common(locale)
    
    def type(self, value):
        """获取消息类型"""
        return self.type_map.get(value)
    
    def common(self, key):
        """获取通用文本"""
        return self.common_map.get(key)