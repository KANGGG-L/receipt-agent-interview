# -*- coding: utf-8 -*-
"""
Query SQL Generator Tool v1.1.0 (Production Active)
租户隔离只读安全 SQL 构建与**占位符强制**注入工具。

v1_0_0 实测缺陷（当前无生产消费者，属潜伏缺陷；审计如实记录）：
  1. 租户隔离失效：仅当 SQL 里出现**大写** WHERE 时才注入租户条件
     （`clean_sql.replace("WHERE", ...)`），小写/混合大小写 where 完全不注入
     -> 输出跨租户 SQL。实测 `select * from receipts where id > 0` 原样返回。
  2. 黑名单漏项：未覆盖 CREATE/ATTACH/PRAGMA/VACUUM/REINDEX/REPLACE/COPY/LOAD。
  3. 注释绕过：`-- tenant_id` 让「含 tenant_id」检查误判通过，跳过租户注入。
  4. 多语句未拦截。

v1_1_0 修法：
  1. 租户隔离改为**占位符强制**：SQL 模板必须显式含 `{tenant_id}` 占位符，缺失直接
     PermissionError（放弃不可靠的字符串改写）；替换值按 SQL 字面量转义（单引号加倍）。
  2. 检测前先**剥离注释**（`--` 行注释、`/* */` 块注释），再做黑名单与租户判断（防注释绕过）。
  3. 拒绝**多语句**（剥离末尾分号后若仍含 `;` 即拒；分号在字符串字面量内不算）。
  4. 黑名单补齐写操作与 DDL/危险语句。

设计说明（避免误伤正常只读查询）：
  - 注释剥离与黑名单/多语句判定均对**字符串字面量**做保护：字符串里的 `;`/关键词/`--`
    不会被误判，字符串内容掩蔽后只保留引号骨架。
  - `{tenant_id}` 占位符必须**位于注释之外**（脚本/注释里写 `-- tenant_id` 无效）；
    支持裸占位符 `tenant_id = {tenant_id}` 与自带引号 `tenant_id = '{tenant_id}'` 两种写法。
"""

import re
from typing import Optional


class QuerySqlGenTool:
    # 写操作 + DDL + SQLite/PG 危险语句（v1_0_0 仅覆盖前半段写操作，v1_1_0 补齐 DDL/危险项）
    FORBIDDEN = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE"
        r"|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|REPLACE|COPY|LOAD)\b",
        re.IGNORECASE,
    )
    TENANT_PLACEHOLDER = "{tenant_id}"

    # ------------------------------------------------------------------
    # 词法预处理：剥离注释 / 掩蔽字符串字面量
    # ------------------------------------------------------------------
    @staticmethod
    def _preprocess(sql: str, mask_strings: bool) -> str:
        """扫描 SQL，剥离 `--` 行注释与 `/* */` 块注释。

        mask_strings=True 时，进一步把字符串字面量的内容掩蔽为空（保留两侧引号），
        使黑名单/多语句判定不受字符串内容干扰；False 时原样保留字符串内容。
        单引号内的转义（`''`）与注释符号均被正确跳过。
        """
        out = []
        i, n = 0, len(sql)
        while i < n:
            ch = sql[i]
            if ch == "'":
                if mask_strings:
                    out.append("''")
                else:
                    out.append(ch)
                i += 1
                while i < n:
                    if sql[i] == "'":
                        if i + 1 < n and sql[i + 1] == "'":  # 字符串内转义单引号
                            if not mask_strings:
                                out.append("''")
                            i += 2
                            continue
                        if not mask_strings:
                            out.append("'")  # 闭合引号（掩蔽模式已输出 '')
                        i += 1
                        break
                    if not mask_strings:
                        out.append(sql[i])
                    i += 1
                continue
            if ch == "-" and i + 1 < n and sql[i + 1] == "-":  # 行注释到行尾
                i += 2
                while i < n and sql[i] not in "\r\n":
                    i += 1
                continue
            if ch == "/" and i + 1 < n and sql[i + 1] == "*":  # 块注释
                i += 2
                while i < n and not (sql[i] == "*" and i + 1 < n and sql[i + 1] == "/"):
                    i += 1
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _strip_trailing_semicolons(text: str) -> str:
        """去掉结尾空白与一个或多个分号（单条语句的合法结尾）。"""
        t = text.strip()
        while t.endswith(";"):
            t = t[:-1].rstrip()
        return t

    def execute(self, raw_sql: str, tenant_id: str) -> Optional[str]:
        raw = str(raw_sql or "")

        # 1. 剥离注释（后续租户判定一律基于去注释文本，防注释绕过）
        body = self._strip_trailing_semicolons(self._preprocess(raw, mask_strings=False))
        if not body:
            raise PermissionError(
                "SQL 不能为空，且必须显式包含租户占位符 {tenant_id}！")

        # 2. 黑名单 / 多语句判定基于「去注释 + 掩蔽字符串」文本，避免字符串内容误判
        masked_body = self._strip_trailing_semicolons(
            self._preprocess(raw, mask_strings=True))

        # 3. 拦截任何写操作 / DDL / 危险语句
        if self.FORBIDDEN.search(masked_body):
            raise PermissionError(
                "禁止生成或执行任何具有写操作/DDL/危险语句的 SQL！")

        # 4. 拒绝多语句（剥离末尾分号后仍含分号）
        if ";" in masked_body:
            raise PermissionError("禁止多语句 SQL，一次仅允许一条只读查询！")

        # 5. 租户隔离：模板必须显式含占位符，缺失即拒（放弃字符串改写）
        if self.TENANT_PLACEHOLDER not in body:
            raise PermissionError(
                "SQL 模板必须显式包含租户占位符 {tenant_id}，禁止省略租户隔离条件！")

        if not str(tenant_id or "").strip():
            raise PermissionError("tenant_id 不能为空，已拒绝输出！")

        # 6. 占位符替换为转义后的租户字面量（单引号加倍，兼容已带引号的写法）
        literal = "'" + str(tenant_id).replace("'", "''") + "'"
        result = body.replace("'" + self.TENANT_PLACEHOLDER + "'", literal)
        result = result.replace(self.TENANT_PLACEHOLDER, literal)

        # 7. 正向保证：输出必须真含租户隔离条件
        if literal not in result:
            raise PermissionError("生成结果缺少租户隔离条件，已拒绝输出！")
        return result


Tool = QuerySqlGenTool
