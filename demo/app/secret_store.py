# -*- coding: utf-8 -*-
"""识别服务 API key 的加密存储（SQLite 里只存 Fernet 密文）。

主密钥来自环境变量 APP_SECRET_KEY；未设置时在 .env 自动生成一把并告警
（丢失 .env 即无法解密已存的 key，需要在设置里重新填一次）。
响应里 key 的脱敏展示不受影响：读取时解密，输出时仍由原有的 mask 逻辑处理。
"""

import base64
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("app.secret_store")

PREFIX = "enc:v1:"
SECRET_FIELDS = (
    "openai_rec_api_key", "openai_aud_api_key", "openai_parse_api_key",
    "grey_openai_rec_api_key", "grey_openai_aud_api_key", "grey_openai_parse_api_key",
    "openai_api_key",  # 旧版单组 key，存量库里可能还有
)
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

_lock = threading.Lock()
_fernet_cache = {}


def _derive_fernet(raw: str) -> Fernet:
    raw = raw.strip()
    try:
        if len(base64.urlsafe_b64decode(raw.encode())) == 32:
            return Fernet(raw.encode())
    except Exception:  # noqa: BLE001 不是合法 Fernet 密钥就按口令派生
        pass
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def _persist_generated_key(key: str) -> None:
    try:
        existing = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
        if "APP_SECRET_KEY=" in existing:
            return
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        with open(ENV_FILE, "a", encoding="utf-8") as f:
            f.write(f"{sep}APP_SECRET_KEY={key}\n")
    except OSError as e:
        logger.warning("无法把自动生成的 APP_SECRET_KEY 写入 .env（%s），请手动设置该环境变量，"
                       "否则重启后已加密保存的识别服务密钥将无法解密", e)


def _get_fernet() -> Fernet:
    with _lock:
        raw = (os.environ.get("APP_SECRET_KEY") or "").strip()
        if not raw:
            raw = Fernet.generate_key().decode()
            os.environ["APP_SECRET_KEY"] = raw
            _persist_generated_key(raw)
            logger.warning("未设置 APP_SECRET_KEY，已自动生成并写入 .env。请备份该密钥；"
                           "生产环境请通过环境变量注入。")
        f = _fernet_cache.get(raw)
        if f is None:
            f = _fernet_cache[raw] = _derive_fernet(raw)
        return f


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plain: str) -> str:
    if not plain or is_encrypted(plain):
        return plain
    return PREFIX + _get_fernet().encrypt(plain.encode()).decode()


def decrypt(value: str) -> str:
    if not is_encrypted(value):
        return value
    try:
        return _get_fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        logger.error("识别服务密钥解密失败（APP_SECRET_KEY 与加密时不一致），按未配置处理；"
                     "请恢复原密钥或在管理台重新填写")
        return ""


def encrypt_config_dict(d: dict) -> dict:
    out = dict(d)
    for k in SECRET_FIELDS:
        if out.get(k):
            out[k] = encrypt(out[k])
    return out


def decrypt_config_dict(d: dict) -> dict:
    out = dict(d)
    for k in SECRET_FIELDS:
        if out.get(k):
            out[k] = decrypt(out[k])
    return out


def migrate_plaintext_keys() -> int:
    """把 engine_config 里仍是明文的 key 就地改成密文；返回迁移的 key 个数。"""
    from app import db
    s = db.get_session()
    try:
        row = s.get(db._AppSettingRow, "engine_config")
        if row is None or not row.value:
            return 0
        try:
            stored = json.loads(row.value)
        except ValueError:
            return 0
        n = sum(1 for k in SECRET_FIELDS if stored.get(k) and not is_encrypted(stored[k]))
        if n == 0:
            return 0
        row.value = json.dumps(encrypt_config_dict(stored), ensure_ascii=False)
        s.commit()
        logger.info("已把 %d 个明文识别服务密钥迁移为密文", n)
        return n
    finally:
        s.close()
