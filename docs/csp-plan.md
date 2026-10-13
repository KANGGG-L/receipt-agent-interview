# 内容安全策略（CSP）上线计划

## 现状

服务端对所有响应加了下面几个安全头（`app/web_assets.py`）：

| 头 | 值 | 作用 |
|---|---|---|
| `X-Frame-Options` | `DENY` | 不允许被别的网站用 iframe 嵌入 |
| `X-Content-Type-Options` | `nosniff` | 浏览器不猜文件类型 |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | 跳转到别的网站时不带完整地址 |
| `Permissions-Policy` | `camera=(self), microphone=(), geolocation=(), payment=()` | 只有本站能开摄像头（拍单据要用） |
| `Content-Security-Policy-Report-Only` | 见下 | 只上报违例，不拦截 |

Report-Only 的策略：

```
default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:;
font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self';
form-action 'self'; frame-ancestors 'none'; report-uri /api/csp-report
```

浏览器把违例发到 `POST /api/csp-report`（匿名可用，体积限 8KB，只统计「指令 + 来源」的次数）。超管可以用 `GET /api/admin/csp-violations` 看到当前进程收到的违例统计。

## 当前违例清单（静态扫描，2026-10-11）

严格策略（`script-src 'self'; style-src 'self'`）下，下面这些写法会被拦：

| 文件 | 内联事件（onclick 等） | 内联 style 属性 | 其它 |
|---|---|---|---|
| `templates/index.html` | 493 | 1501 | 1 个 `<style>` 块；2 个 `javascript:` 链接 |
| `templates/evalset.html` | 7 | 7 | 1 个 `<style>` 块 |
| `static/js/main.js`（拼 HTML 字符串） | 126 | 736 | |
| `static/js/export_center.js` | 38 | 40 | |
| `static/js/evalset.js` | 0 | 5 | |
| `static/js/finance.js` | 0 | 4 | |
| `static/js/review_ux.js` | 1 | 0 | |
| 服务端注入的评测工作台脚本 | 1 个内联 `<script>`（`window.__EVAL_WORKBENCH__ = {...}`） | | 见下 |

没有发现 `eval(...)` 或 `new Function(...)`。

## 清除顺序（交给前端同事 F2）

1. 内联事件换成事件委托：模板里写 `data-action="xxx"`，在一个脚本里统一 `addEventListener`。先清 `index.html` 的 493 处和 `main.js` 的 126 处。
2. 内联 style 属性换成 class。数量最多（共 2300 多处），可以分批做；动态值（比如进度条宽度）改用 CSS 变量 + class，由脚本设置 `element.style.setProperty`（脚本设置样式不受 `style-src` 限制）。
3. `<style>` 块搬到 `static/css/`。
4. 两个 `javascript:` 链接改成按钮。
5. 评测工作台的内联 `<script>` 改成 `<meta name="eval-workbench" content="...">`，由 `eval_workbench.js` 读取；服务端 `app/main.py::evalset_workbench_page` 同步改。
6. 违例统计降到 0（观察一周的 `/api/admin/csp-violations`）后，把 `Content-Security-Policy-Report-Only` 改名成 `Content-Security-Policy`（改 `app/web_assets.py` 里的 `SECURITY_HEADERS` 一处即可）。

## 验证方法

- 打开页面，浏览器控制台会列出被（假想）拦截的资源；同时服务端会收到上报。
- 每清掉一批，重新跑上面的扫描，数字应该下降。
