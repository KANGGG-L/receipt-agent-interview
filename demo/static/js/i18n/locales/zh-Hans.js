/*
 * 简体中文（源语言）。键名按「区域.含义」分组；新增键时先加在这里，再补 zh-Hant-HK.js。
 * 文案必须与模板/脚本中的原文一致，否则简体界面会出现可见变化。
 */
(function (root) {
    (root.I18N_LOCALES = root.I18N_LOCALES || {})['zh-Hans'] = {
        'app.title': '香港餐饮 AI 进货收据识别与库存管理系统',
        'app.brand': 'AI 智能库存系统',
        'app.demoBanner': '演示版：现在不用密码就能进入，只供试用，不要用来记真实账目',
        'app.home': '系统首页',

        'lang.label': '显示语言',
        'lang.switchTo': '切换为{name}',

        'nav.scan': '识别复核',
        'nav.inventory': '实时库存与价格',
        'nav.dishes': '餐品成本',
        'nav.archive': '归档对账',
        'nav.report': '部门花销',
        'nav.export': '数据中心',
        'nav.adminSection': '系统与算法工程',
        'nav.engine': '系统与引擎配置',
        'nav.analytics': '观测中心',

        'role.label': '当前角色',
        'role.admin': '系统管理员',
        'role.owner': '餐厅老板 / 店长',
        'role.staff': '餐厅店员',
        'role.option.admin': '● 系统管理员',
        'role.option.owner': '● 餐厅老板 / 店长',
        'role.option.staff': '● 餐厅店员',
        'role.switched': '角色已切换为：{name}',

        'common.save': '保存',
        'common.cancel': '取消',
        'common.confirm': '确认',
        'common.delete': '删除',
        'common.upload': '上传',
        'common.approve': '审批',
        'common.refresh': '刷新',
        'common.close': '关闭',
        'common.back': '返回',
        'common.edit': '编辑',
        'common.search': '搜索',
        'common.export': '导出',
        'common.retry': '重试',
        'common.loading': '加载中…',
        'common.empty': '暂无数据',
        'common.networkError': '网络连接失败，请检查网络后重试',
        'common.serverError': '服务器出错，请稍后再试',
        'common.unknown': '未知',

        'status.uploaded': '已上传',
        'status.parsing': '识别中',
        'status.parsed': '待核对',
        'status.parsed_with_warnings': '待核对（有疑问）',
        'status.edited': '待老板审批',
        'status.approved': '已入账',
        'status.flagged': '有问题',
        'status.reversed': '已冲销',
        'status.error': '识别失败'
    };
})(typeof window !== 'undefined' ? window : globalThis);
