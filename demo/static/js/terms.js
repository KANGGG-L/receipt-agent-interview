/*
 * 术语与人话文案的唯一来源（S3-10、S2-12、S1-P2-02）。
 * 业务界面里的状态名、结算方式、按钮名、后端错误码说明都从这里取，
 * 不在各处各写一份。新增或改叫法只改本文件（和 i18n 繁体目录里同名的键）。
 */
(function (root) {
    'use strict';

    var TERMS = {
        status: {
            uploaded: { label: '已上传', title: '图片已上传，还没开始识别', filter: '已上传' },
            parsing: { label: '识别中', title: '正在识别单据', filter: '识别中' },
            parsed: { label: '待核对', title: '系统已读完，等店员对照原图核对', filter: '待核对' },
            parsed_with_warnings: { label: '待核对（有疑问）', title: '系统读完后发现数字对不上，请重点核对', filter: '待核对（有疑问）' },
            edited: { label: '待老板审批', title: '店员已核对并提交，等老板审核入账', filter: '待老板审批' },
            approved: { label: '已入账', title: '老板已审核入账，库存已增加', filter: '已入账' },
            flagged: { label: '有问题', title: '老板标记了问题，暂时不能入账', filter: '有问题' },
            error: { label: '识别失败', title: '没能自动读出这张单，可以转手工录入', filter: '识别失败' },
            reversed: { label: '已冲销', title: '这张单入账后被老板冲销了，库存和支出已调整', filter: '已冲销' },
            discarded: { label: '已放弃', title: '这张单已放弃', filter: '已放弃' }
        },
        settlement: { cash: '现结', credit: '挂账', mixed: '现结和挂账都有' },
        settlementOption: { cash: '现结（当场付钱）', credit: '挂账（月底再结）' },
        creditBill: '挂账单',
        btn: {
            save: '保存并提交审核',
            saveNext: '保存并下一张',
            approve: '审核入账',
            flag: '标记有问题',
            discard: '放弃上传',
            manual: '转手工录入',
            retry: '重试',
            reverse: '冲销'
        },
        manualBadge: '手工录入',
        noImage: '无原图（手工录入）'
    };

    function statusLabel(code) {
        var s = TERMS.status[String(code || '')];
        return s ? s.label : '处理中';
    }

    function settlementLabel(code) {
        return TERMS.settlement[String(code || '')] || '';
    }

    // ---- 金额与字段名 ----
    function money(n) {
        var v = Number(n);
        if (!isFinite(v)) return '';
        var r = Math.round(Math.abs(v) * 100) / 100;
        return String(Number(r.toFixed(2)));
    }

    var FIELD_LABELS = {
        name: '品名', raw_name: '品名', quantity: '数量', unit: '单位', unit_price: '单价',
        amount: '金额', total_amount: '整单总额', supplier_name: '供应商', date: '开单日期',
        receipt_date: '开单日期', doc_form: '单据类型', settlement_type: '结算方式',
        payment_marked: '是否已付款', currency: '币种', items: '明细'
    };

    function fieldLabel(f) {
        var k = String(f || '').replace(/^items\.\d+\./, '').replace(/^.*\./, '');
        return FIELD_LABELS[k] || '这一项';
    }

    // ---- 后端结构化门禁结果（gate_issues）渲染 ----
    // 容忍字段别名，后端字段名以接口契约为准。
    function pick(o, keys) {
        for (var i = 0; i < keys.length; i++) {
            if (o[keys[i]] !== undefined && o[keys[i]] !== null) return o[keys[i]];
        }
        return undefined;
    }

    function issueRow(issue) {
        var r = pick(issue, ['row', 'line_no', 'line', 'row_no']);
        if (r !== undefined && isFinite(Number(r))) return Number(r);
        var idx = pick(issue, ['item_index', 'index', 'row_index']);
        if (idx !== undefined && isFinite(Number(idx))) return Number(idx) + 1;
        return null;
    }

    function formatGateIssue(issue) {
        if (!issue) return '';
        if (typeof issue === 'string') return humanizeGateText(issue);
        var kind = String(pick(issue, ['kind', 'code', 'type', 'rule']) || '').toLowerCase();
        var row = issueRow(issue);
        var name = pick(issue, ['item_name', 'name']);
        var written = pick(issue, ['written', 'actual', 'ai_value', 'value', 'stated']);
        var expected = pick(issue, ['expected', 'should_be', 'computed', 'calc']);
        var diff = pick(issue, ['diff', 'delta', 'difference']);
        if (diff === undefined && written !== undefined && expected !== undefined) {
            diff = Number(written) - Number(expected);
        }
        var head = row ? ('第 ' + row + ' 行' + (name ? ' ' + name : '') + '：') : '';
        var haveNums = written !== undefined && expected !== undefined && isFinite(Number(written)) && isFinite(Number(expected));
        if (/total|sum|header/.test(kind)) {
            if (haveNums) {
                return '明细加起来是 ' + money(expected) + ' 元，但整单总额写的是 ' + money(written) + ' 元，相差 ' + money(diff) + ' 元';
            }
        } else if (/line|amount|item|row|arith/.test(kind) || haveNums) {
            if (haveNums) {
                return head + '单据写 ' + money(written) + ' 元，按数量单价应是 ' + money(expected) + ' 元，相差 ' + money(diff) + ' 元';
            }
        }
        var field = pick(issue, ['field', 'path', 'loc']);
        if (field !== undefined || /contract|field|missing|invalid|format/.test(kind)) {
            return head + '「' + fieldLabel(Array.isArray(field) ? field.join('.') : field) + '」AI 没读清，请对照原图填写';
        }
        var msg = pick(issue, ['msg', 'message']);
        return msg ? humanizeGateText(String(msg)) : '';
    }

    function formatGateIssues(list) {
        if (!Array.isArray(list)) return [];
        var out = [];
        list.forEach(function (it) {
            var t = formatGateIssue(it);
            if (t && out.indexOf(t) === -1) out.push(t);
        });
        return out;
    }

    // ---- 旧版纯文本门禁文案兜底（后端没有给 gate_issues 时）----
    var GENERIC_GATE = '这张单据有数字对不上，请对照原图核对';

    function humanizeGateText(msg) {
        var s = (msg == null) ? '' : String(msg).trim();
        if (!s) return '';
        s = s.replace(/^\s*(算术门禁|契约校验失败|契约门禁)\s*[:：]\s*/, '');

        if (/安全阻断|canary|令牌|token|api\s*base|URL\s*不能为空/i.test(s)) {
            return '自动识别暂时用不了，请直接手工录入，原图已保留';
        }

        var m = s.match(/第\s*(\d+)\s*行\s*([^:：]*)[:：]\s*数量\s*([-0-9.]+)\s*[×xX*]\s*单价\s*([-0-9.]+)\s*=\s*([-0-9.]+)\s*[，,]\s*但小计\s*=\s*([-0-9.]+)/);
        if (m) {
            var exp = Number(m[5]), got = Number(m[6]);
            return '第 ' + m[1] + ' 行' + (m[2].trim() ? ' ' + m[2].trim() : '') + '：单据写 ' + money(got) +
                ' 元，按数量单价应是 ' + money(exp) + ' 元，相差 ' + money(got - exp) + ' 元';
        }

        m = s.match(/明细合计\s*=?\s*([-0-9.]+)\s*[，,]?\s*(?:应为|预期总额\s*=?)\s*([-0-9.]+)\s*[，,]\s*但(?:总额|单据写着)\s*=?\s*([-0-9.]+)/);
        if (m) {
            var sum = Number(m[1]), tot = Number(m[3]);
            return '明细加起来是 ' + money(sum) + ' 元，但整单总额写的是 ' + money(tot) + ' 元，相差 ' + money(tot - sum) + ' 元';
        }

        m = s.match(/(?:^|[\s;；,，])(items\.(\d+)\.)?([a-z_]+)\s*[:：]/);
        if (m && /^[a-z_]+$/.test(m[3]) && /[A-Za-z]{3,}/.test(s.replace(/[a-z_.0-9]+\s*[:：]/, ''))) {
            var rowNo = m[2] !== undefined ? Number(m[2]) + 1 : null;
            return (rowNo ? '第 ' + rowNo + ' 行的' : '') + '「' + fieldLabel(m[3]) + '」AI 没读清，请对照原图填写';
        }

        s = s.replace(/payment_marked/g, '是否已付款');
        s = s.replace(/（差\s*(-?[0-9.]+)\s*）/g, function (_, d) { return '，相差 ' + money(d) + ' 元'; });
        s = s.replace(/相差\s*-?([0-9.]+)(?!\s*元)/g, function (_, d) { return '相差 ' + money(d) + ' 元'; });
        s = s.replace(/明细合计=/g, '明细合计 ').replace(/预期总额=/g, '应为 ').replace(/，但总额=/g, '，但单据写着 ');

        // 仍然带英文残句就不展示原文
        if (/[A-Za-z]{3,}/.test(s)) return GENERIC_GATE;
        return s;
    }

    // ---- 后端错误码 -> 人话（未知码一律兜底通用说法，不展示英文原文）----
    var CODE_TEXT = {
        VERSION_REQUIRED: '这张单刚被别人改过，请刷新后再保存',
        VERSION_CONFLICT: '这张单刚被别人改过，请刷新后再保存',
        INVALID_STATUS: '这张单现在的状态不能做这个操作，请刷新看看最新状态',
        ALREADY_APPROVED: '这张单已经入账了，不能再改',
        CANNOT_MODIFY_APPROVED: '这张单已经入账了，不能再改。需要更正请让老板点「冲销」',
        CANNOT_ACKNOWLEDGE_APPROVED: '这张单已经入账了，不需要再确认',
        RECEIPT_DISCARDED: '这张单已经放弃了，请重新上传',
        SETTLEMENT_REQUIRED: '请选择结算方式（现结或挂账）',
        APPROVAL_BLOCKED: '这张单还有必须改的地方，请先修改并保存后再审核入账',
        NOT_REVIEWED: '这张单还没有人核对过，请先对照原图核对并点「保存并提交审核」',
        REVIEW_REQUIRED: '这张单还没有人核对过，请先对照原图核对并点「保存并提交审核」',
        FLAGGED_LOCKED: '这张单被老板标记了问题，店员不能再改，请联系老板处理',
        RECEIPT_FLAGGED: '这张单被老板标记了有问题，店员不能再改，请联系老板处理',
        CANNOT_MODIFY_REVERSED: '这张单已经冲销作废，不能再修改，请重新录一张新单',
        CANNOT_DISCARD_APPROVED: '这张单已经入账，不能放弃。录错了请老板用「冲销」',
        EMPTY_FILE: '没有收到照片，请重新选择照片再上传',
        UNIT_CONVERSION_MISSING: '有的单位和库存单位之间还没有换算关系，请老板先到设置里添加换算',
        IMAGE_QUALITY_ERROR: '图片有点模糊或光线不足，请重新拍摄或调整后再试',
        IMAGE_PIXELS_EXCEEDED: '照片太大了，请缩小后再传',
        IMAGE_NOT_FOUND: '找不到这张原图，请重新上传',
        IMAGE_SIGNATURE_INVALID: '原图链接已过期，请刷新页面后再看',
        IMAGE_TRANSCODE_FAILED: '这张照片格式处理不了，请换一张或直接手工录入',
        BATCH_LIMIT_EXCEEDED: '一次传的照片太多了，请分批上传',
        ENGINE_NOT_CONFIGURED: '自动识别暂时没开通，请直接手工录入，原图已保留',
        ENGINE_AUTH_FAILED: '自动识别暂时用不了，请直接手工录入，原图已保留',
        ENGINE_BUSY: '自动识别现在比较忙，可以等一会再点「重试」，也可以直接手工录入',
        ENGINE_TIMEOUT: '自动识别等太久了，可以点「重试」，也可以直接手工录入',
        NOT_FOUND: '找不到这条记录，请刷新页面',
        FORBIDDEN: '你的角色不能做这个操作，请找老板处理',
        NOT_APPROVED: '只有已经入账的单据才能冲销。这张单还没入账，直接修改或放弃就行',
        REASON_REQUIRED: '请写明原因，方便以后查账',
        RECEIPT_NOT_FOUND: '找不到这张单，可能已被删除，请刷新页面后再试',
        NOT_IN_TRASH: '回收站里没有这张单，可能已经恢复或被彻底删除，请刷新页面'
    };

    var GENERIC_ERROR = '操作没有成功，请稍后再试；如果一直不行，请联系老板';

    function hasLatinWords(t) {
        return /[A-Za-z]{3,}/.test(String(t || ''));
    }

    function hasCjk(t) {
        return /[\u4e00-\u9fff]/.test(String(t || ''));
    }

    /** 识别失败的三类原因：未配置 / 密钥错 / 临时繁忙 / 其他。 */
    function classifyEngineFailure(ret) {
        var code = String((ret && ret.code) || '').toUpperCase();
        var msg = String((ret && (ret.msg || ret.error_msg)) || '');
        if (code === 'ENGINE_NOT_CONFIGURED' || /未配置|没有配置|not\s*configured|URL\s*不能为空|API\s*Base|api[_ ]?key.*(empty|missing|缺)|密钥为空|安全阻断/i.test(msg)) return 'not_configured';
        if (code === 'ENGINE_AUTH_FAILED' || /鉴权失败|密钥无效|invalid\s*api|Unauthorized|\b401\b|\b403\b|Token is invalid|incorrect api key/i.test(msg)) return 'auth';
        if (code === 'ENGINE_BUSY' || code === 'ENGINE_TIMEOUT' || /繁忙|超时|timeout|timed out|429|rate\s*limit|overloaded|\b50[234]\b/i.test(msg)) return 'busy';
        return 'other';
    }

    // 后端 msg 已经是人话（中文、没有英文词和接口路径）就直接用，因为它带着具体原因；
    // 否则按错误码查通用说法；都没有就用兜底句。
    function isHumanMsg(m) {
        return !!m && hasCjk(m) && !hasLatinWords(m) && !/status\s*=|X-Role|Bearer|GET \/|POST \/|\/api\//i.test(m);
    }

    function humanizeCode(code, rawMsg) {
        var c = String(code || '').toUpperCase();
        var m = String(rawMsg || '').trim();
        if (isHumanMsg(m)) return m;
        if (c && CODE_TEXT[c]) return CODE_TEXT[c];
        return GENERIC_ERROR;
    }

    TERMS.statusLabel = statusLabel;
    TERMS.settlementLabel = settlementLabel;
    TERMS.formatGateIssue = formatGateIssue;
    TERMS.formatGateIssues = formatGateIssues;
    TERMS.humanizeGateText = humanizeGateText;
    TERMS.humanizeCode = humanizeCode;
    TERMS.classifyEngineFailure = classifyEngineFailure;
    TERMS.codeText = CODE_TEXT;
    TERMS.genericError = GENERIC_ERROR;
    TERMS.money = money;
    TERMS.fieldLabel = fieldLabel;

    root.TERMS = TERMS;
    if (typeof module !== 'undefined' && module.exports) module.exports = TERMS;
})(typeof window !== 'undefined' ? window : globalThis);
