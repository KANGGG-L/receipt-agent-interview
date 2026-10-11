# B2 接口契约（前端 F1a/F1b 读取；最终报告附在本文件后部）

所有错误响应统一为 `{"status":"error","code":"XXX","msg":"人话提示","data":{...可选}}`。msg 可直接展示给店员。
权限：设置类接口 owner 及以上（admin 也可），staff 返回 403。租户由登录身份决定（演示态读 X-Tenant-Id）。

## 1. 单位与换算

### GET /api/settings/units
```
200 {"status":"success","data":{
  "units":[{"id":-1,"name":"公斤","kind":"weight","builtin":true}, {"id":7,"name":"大桶","kind":"count","builtin":false}],
  "conversions":[{"id":-4,"from_unit":"斤","to_unit":"公斤","factor":0.6048,"note":"港斤（司马斤），香港单据口径","builtin":true},
                 {"id":3,"from_unit":"箱","to_unit":"瓶","factor":12.0,"note":"","builtin":false}]}}
```
- builtin 的 id 是负数，只读：PUT/DELETE 返回 400，提示「新增同一对单位的换算来覆盖」。
- 老板覆盖种子后，列表里只显示老板的那一条（builtin=false），不再显示被覆盖的种子。
- `units` 即「SKU 基准单位下拉」的数据源（内置 + 本租户自定义）。

### POST /api/settings/units    body `{"name":"大桶","kind":"count"}`（kind 可省略，取值 weight|volume|count|other）
- 200 `{"status":"success","id":7,"msg":"单位已添加"}`
- 409 `{"code":"UNIT_DUPLICATE","msg":"「大桶」已经有了，不用重复添加"}`；400 `UNIT_INVALID`（空名、超过 20 字）

### DELETE /api/settings/units/{id}
- 200 `{"status":"success","msg":"单位已删除"}`
- 409 `UNIT_IN_USE`：msg 如「「引用桶」还在使用中，不能删除：库存商品「引用测试食材」的库存单位。请先改掉再删。」
- 400 `UNIT_DELETE_REFUSED`（内置）；404（不存在或不属于本租户）

### POST /api/settings/unit_conversions    body `{"from_unit":"箱","to_unit":"瓶","factor":12,"note":""}`
语义：1 个 from_unit = factor 个 to_unit。两个单位不存在会自动创建；同一对单位（含反向写法）已有换算则覆盖。
- 200 `{"status":"success","id":3,"msg":"换算已保存"}`
- 400 `CONVERSION_INVALID`：factor<=0/非数字/NaN、两个单位相同、单位为空、和现有换算矛盾（msg 例：「和已有换算对不上：按现有设置（经过 盒X、瓶、毫升），1 个「盒X」等于 3000 个「毫升」，而你填的是 1。请先改掉其中一条再保存。」）
- 界面示例文案建议：「1 箱 = 12 瓶」「1 斤 = 0.6048 公斤」

### PUT /api/settings/unit_conversions/{id}   body 任意子集 `{"from_unit","to_unit","factor","note"}`
- 200 `{"status":"success","id":3,"msg":"换算已保存"}`（id 不变）；校验同 POST；404 不属于本租户

### DELETE /api/settings/unit_conversions/{id}
- 200 `{"status":"success","msg":"换算已删除"}`；404；400 `CONVERSION_DELETE_REFUSED`（内置）

### GET /api/settings/unit_conversions/check?from_unit=箱&to_unit=毫升
```
200 {"status":"success","convertible":true,"factor":3960.0,"path":["箱","瓶","毫升"]}
200 {"status":"success","convertible":false,"factor":null,"path":[]}
```
BFS 多跳；种子和租户换算合并成一张图，反向自动推导。单位写法会归一（kg=公斤=千克，司马斤=港斤=catty=斤，lb=磅，ml=毫升…）。

### 入库（approve）遇到换算不了：HTTP 422
`POST /api/receipt/{id}/approve` 在写入任何库存流水之前预检；任一明细不能换算则整张单拒绝，库存不变。
```
422 {"status":"error","code":"UNIT_CONVERSION_MISSING",
     "msg":"「箱」和库存单位「斤」之间还没有换算关系，请老板到「设置」里添加换算，或把这一行的单位改成「斤」",
     "data":{"pairs":[{"from_unit":"箱","to_unit":"斤","item_name":"预检乙"}]}}
```
`pairs` 是所有需要处理的行（msg 只描述第一行；多行时末尾带「一共有 N 行需要处理」）。前端据此引导到设置页，并可用 pairs 预填「添加换算」表单（from_unit、to_unit）。

## 2. 汇率（F1b 设置页）

### GET /api/settings/fx_rates
```
200 {"status":"success","data":{"base_currency":"HKD","rates":[
  {"currency":"HKD","rate":1.0,"confirmed":true,"note":"本位币","seed":false},
  {"currency":"CNY","rate":1.08,"confirmed":false,"note":"参考值，请老板按实际汇率确认","seed":true},
  {"currency":"USD","rate":7.78,"confirmed":true,"note":"","seed":false}]}}
```
rate = 1 单位该币种折合多少港币。seed=true 表示还是系统给的参考值，界面要标明「参考值，需老板确认」。种子币种：CNY USD EUR GBP JPY SGD MOP AUD CAD TWD THB。

### PUT /api/settings/fx_rates   body `{"rates":[{"currency":"USD","rate":7.78,"confirmed":true,"note":""}]}`
- 200 `{"status":"success","msg":"汇率已保存","data":{...同 GET...}}`；400 `FX_INVALID`（币种代码不是 3 个字母、rate<=0 等）
- HKD 行会被忽略。

### 非港币单据 approve
没有汇率则 422：`{"code":"FX_RATE_MISSING","msg":"这张单是「XXX」单，设置里还没有 XXX 兑港币的汇率，请老板到「设置」里填好汇率后再入账","data":{"currency":"XXX"}}`。
有汇率则入账，并把当时的汇率存快照，之后所有报表（成本、应付、对账、库存货值）按快照折算成港币，明细里保留原币种金额。

## 3. SKU 候选（F1a 复核界面）

`GET /api/receipt/{id}`（以及 upload 返回的 data）里，**非 approved 单据**的每条明细多一个字段 `sku_candidates`（数组，默认 []）：
```
"items":[{"id":11,"name":"牛肉丸","sku_id":null,"matched":false,
  "sku_candidates":[{"sku_id":3,"name":"牛肉","unit":"斤",
     "reason":"名字相近：「牛肉丸」和「牛肉」互相包含；单位「包」和库存单位「斤」没有换算关系，可能不是同一种东西",
     "unit_compatible":false}]}]
```
- 只对没有 sku_id 的明细给候选，最多 5 个，名字相似度高的在前。这是读取时现算的只读字段，保存时不用回传（回传也会被忽略）。
- 老板在复核界面点「就是这个」：把该明细的 `sku_id` 设成候选的 `sku_id` 后走现有的 `POST /api/save_edited` 保存（后端据此把 matched 置 1，视为人工确认），approve 时才会并入该 SKU。
- 没有确认、也没有精确/别名命中的明细：approve 时按规范名**新建 SKU**，不会并入包含匹配到的 SKU。

（付款、对账、权限、导出相关契约见后文，完成后补全。）
