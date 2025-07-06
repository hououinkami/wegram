class LocaleConfig:
    LOCALES = {
        'ja': {
            'message_types': {
                1: 'テキスト',
                3: '写真',
                34: '音声',
                37: '友人登録リクエスト',
                42: '連絡先カード',
                43: '動画',
                47: 'ステッカー',
                48: '位置',
                66: 'WeCom名刺',
                10000: 'システムメッセージ',
                4: 'アプリ',
                5: 'リンク',
                6: 'ファイル',
                8: 'ステッカー',
                19: 'チャット履歴',
                33: 'ミニプログラム',
                36: 'ミニプログラム',
                51: 'チャンネル',
                53: 'グループノート',
                57: '引用',
                2000: '送金',
                2001: 'ラッキマネー',
                'revokemsg': '撤回',
                'pat': '軽く叩く',
                'ilinkvoip': "通話",
                'VoIPBubbleMsg': '通話',
                'sysmsgtemplate': 'グループに参加',
                'unknown': '不明'
            },
            'common': {
                'online': '🟢 WeChatがオンラインしました',
                'offline': '🔴 WeChatがオフラインしました',
                'twice_login_success': '✅ 二次ログイン成功',
                'twice_login_fail': '❌ 二次ログイン失敗',
                'failed': '❌ 操作失敗',
                'add_contact': '連絡先に追加',
                'request_successed': '✅ 友人要請を送信しました',
                'agree_accept': '承認',
                'accept_successed': '✅ 承認しました',
                'transfer_out': 'を受領された',
                'transfer_in': 'を受信'
            },
            'command': {
                'update': '連絡先を更新',
                'receive': 'メッセージの受信',
                'receive_on': '✅ 転送オン',
                'receive_off': '❌ 転送オフ',
                'unbind': 'バインドを解除',
                'unbind_successed': '✅ 連絡先から削除しました',
                'no_binding': '⚠️ まだ連絡先とバインドされません',
                'add': '連絡先を追加',
                'no_phone': '⚠️ 検索変数が必要',
                'no_user': '⚠️ ユーザーは存在していません',
                'user_added': '✅ この友人はすでに登録しています',
                'remark': 'コメントを設定',
                'no_remark_name': '⚠️ コメントが必要',
                'quit': 'グループから退出',
                'revoke': 'メッセージの撤回',
                'revoke_failed': '❌ 撤回失敗',
                'no_reply': '⚠️ 撤回したいメッセージを引用',
                'login': '二次ログイン',
                'only_in_bot': '⚠️ このコマンドはボットのみで有効',
                'only_in_group': '⚠️ このコマンドはグループのみで有効',
                'only_in_chat': '⚠️ このコマンドはチャットのみで有効',
                'not_in_bot': '⚠️ このコマンドはボット以外で有効'
            }
        },
        'zh': {
            'message_types': {
                1: '文本',
                3: '图片',
                34: '语音',
                37: '添加好友请求',
                43: '视频',
                42: '联系人',
                47: '表情',
                48: '位置',
                66: 'WeCom名片',
                10000: '系统提示',
                4: '应用信息',
                5: '链接',
                6: '文件',
                8: '表情',
                19: '聊天记录',
                33: '小程序',
                36: '小程序',
                51: '视频号',
                53: '群接龙',
                57: '引用',
                2000: '转账',
                2001: '红包',
                'revokemsg': '撤回',
                'pat': '拍一拍',
                'ilinkvoip': "通话",
                'VoIPBubbleMsg': '通话',
                'sysmsgtemplate': '加入群聊',
                'unknown': '未知'
            },
            'common': {
                'online': '🟢 WeChat已上线',
                'offline': '🔴 WeChat已离线',
                'twice_login_success': '✅ 二次登录成功',
                'twice_login_fail': '❌ 二次登录失敗',
                'failed': '❌ 操作失败',
                'add_to_contact': '添加到联系人',
                'request_successed': '✅ 已发送好友申请',
                'agree_accept': '同意',
                'accept_successed': '✅ 已通过',
                'transfer_out': '已接受',
                'transfer_in': ''
            },
            'command': {
                'update': '更新联系人',
                'receive': '信息接收开关',
                'receive_on': '✅ 转发开启',
                'receive_off': '❌ 转发关闭',
                'unbind': '解除绑定',
                'unbind_successed': '⚠️ 从联系人文件中删除成功',
                'no_binding': '⚠️ 尚未绑定联系人',
                'add': '添加联系人',
                'no_phone': '⚠️ 请在命令后面输入搜索变量',
                'no_user': '⚠️ 用户不存在',
                'user_added': '✅ 已经添加为好友',
                'remark': '设置备注名',
                'no_remark_name': '⚠️ 请输入备注名',
                'quit': '退出群聊',
                'revoke': '撤回消息',
                'revoke_failed': '❌ 撤回失败',
                'no_reply': '⚠️ 请回复要撤回的信息',
                'login': '二次登录',
                'only_in_bot': '⚠️ 此命令仅在Bot中有效',
                'only_in_group': '⚠️ 此命令仅在群聊中有效',
                'only_in_chat': '⚠️ 此命令仅在私聊中有效',
                'not_in_bot': '⚠️ 此命令仅在Bot中无效'
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