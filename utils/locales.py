class LocaleConfig:
    LOCALES = {
        'ja': {
            'message_types': {
                1: 'テキスト',
                3: '写真',
                34: '音声',
                42: '連絡先',
                43: '動画',
                47: 'ステッカー',
                48: '位置',
                5: 'リンク',
                6: 'ファイル',
                19: 'チャット履歴',
                33: 'ミニプログラム',
                51: 'チャンネル',
                53: 'グループノート',
                57: '引用',
                66: 'ファイル',
                2000: '送金',
                2001: 'ラッキマネー',
                'revokemsg': '撤回',
                'pat': '軽く叩く',
                'VoIPBubbleMsg': '通話',
                'unknown': '不明'
            },
            'common': {
                'online': '🟢 WeChatがオンラインしました',
                'offline': '🔴 WeChatがオフラインしました',
                'revoke_failed': '❌ 撤回失敗',
                'receive_on': '✅ 転送オン',
                'receive_off': '❌ 転送オフ',
                'unbind': '✅ 連絡先から削除しました',
                'twice_login_success': '✅ 二次ログイン成功',
                'twice_login_fail': '❌ 二次ログイン失敗',
                'no_binding': '⚠️ まだ連絡先とバインドされません',
                'failed': '❌ 操作失敗',
                'no_reply': '⚠️ 撤回したいメッセージを引用'
            },
            'command': {
                'update': '連絡先を更新',
                'receive': 'メッセージの受信',
                'unbind': 'バインドを解除',
                'revoke': 'メッセージの撤回',
                'login': '二次ログイン'
            }
        },
        'zh': {
            'message_types': {
                1: '文本',
                3: '图片',
                34: '语音',
                43: '视频',
                42: '联系人',
                47: '表情',
                48: '位置',
                5: '链接',
                6: '文件',
                19: '聊天记录',
                33: '小程序',
                51: '视频号',
                53: '群接龙',
                57: '引用',
                66: '文件',
                2000: '转账',
                2001: '红包',
                'revokemsg': '撤回',
                'pat': '拍一拍',
                'VoIPBubbleMsg': '通话',
                'unknown': '未知'
            },
            'common': {
                'online': '🟢 WeChat已上线',
                'offline': '🔴 WeChat已离线',
                'revoke_failed': '❌ 撤回失败',
                'receive_on': '✅ 转发开启',
                'receive_off': '❌ 转发关闭',
                'unbind': '⚠️ 从联系人文件中删除成功',
                'twice_login_success': '✅ 二次登录成功',
                'twice_login_fail': '❌ 二次登录失敗',
                'no_binding': '⚠️ 尚未绑定联系人',
                'failed': '❌ 操作失败',
                'no_reply': '⚠️ 请回复要撤回的信息'
            },
            'command': {
                'update': '更新联系人',
                'receive': '信息接收开关',
                'unbind': '解除绑定',
                'revoke': '撤回消息',
                'login': '二次登录'
            }
        }
    }
    
    @classmethod
    def get_message_types(cls, locale='ja'):
        return cls.LOCALES.get(locale, {}).get('message_types', {})
    
    @classmethod
    def get_common(cls, locale='ja'):
        return cls.LOCALES.get(locale, {}).get('common', {})
    
    @classmethod
    def get_command(cls, locale='ja'):
        return cls.LOCALES.get(locale, {}).get('command', {})

class Locale:
    def __init__(self, locale='ja'):
        self.locale = locale
        self.type_map = LocaleConfig.get_message_types(locale)
        self.common_map = LocaleConfig.get_common(locale)
        self.command_map = LocaleConfig.get_command(locale)
    
    def type(self, value):
        """获取消息类型"""
        return self.type_map.get(value)
    
    def common(self, key):
        """获取通用文本"""
        return self.common_map.get(key)
    
    def command(self, key):
        """获取命令文本"""
        return self.command_map.get(key)