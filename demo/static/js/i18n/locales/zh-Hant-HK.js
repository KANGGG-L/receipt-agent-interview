/*
 * 繁體中文（香港）。人工校對的鍵值；缺少的鍵會回退到簡體原文並經文字轉換層轉換。
 */
(function (root) {
    (root.I18N_LOCALES = root.I18N_LOCALES || {})['zh-Hant-HK'] = {
        'app.title': '香港餐飲 AI 進貨收據識別與庫存管理系統',
        'app.brand': 'AI 智能庫存系統',
        'app.demoBanner': '示範版：現在不用密碼就可以進入，只供試用，不要用來記真實賬目',
        'app.home': '系統首頁',

        'lang.label': '顯示語言',
        'lang.switchTo': '切換為{name}',

        'nav.scan': '識別覆核',
        'nav.inventory': '實時庫存與價格',
        'nav.dishes': '餐品成本',
        'nav.archive': '歸檔對賬',
        'nav.report': '部門花銷',
        'nav.export': '數據中心',
        'nav.adminSection': '系統與算法工程',
        'nav.engine': '系統與引擎設定',
        'nav.analytics': '觀測中心',

        'role.label': '目前角色',
        'role.admin': '系統管理員',
        'role.owner': '餐廳老闆 / 店長',
        'role.staff': '餐廳店員',
        'role.option.admin': '● 系統管理員',
        'role.option.owner': '● 餐廳老闆 / 店長',
        'role.option.staff': '● 餐廳店員',
        'role.switched': '角色已切換為：{name}',

        'common.save': '儲存',
        'common.cancel': '取消',
        'common.confirm': '確認',
        'common.delete': '刪除',
        'common.upload': '上載',
        'common.approve': '審批',
        'common.refresh': '刷新',
        'common.close': '關閉',
        'common.back': '返回',
        'common.edit': '編輯',
        'common.search': '搜尋',
        'common.export': '匯出',
        'common.retry': '重試',
        'common.loading': '載入中…',
        'common.empty': '暫無數據',
        'common.networkError': '網絡連線失敗，請檢查網絡後重試',
        'common.serverError': '伺服器出錯，請稍後再試',
        'common.unknown': '未知',

        'status.uploaded': '已上載',
        'status.parsing': '識別中',
        'status.parsed': '待核對',
        'status.parsed_with_warnings': '待核對（有疑問）',
        'status.edited': '待老闆審批',
        'status.approved': '已入賬',
        'status.flagged': '有問題',
        'status.reversed': '已沖銷',
        'status.error': '識別失敗'
    };
})(typeof window !== 'undefined' ? window : globalThis);
