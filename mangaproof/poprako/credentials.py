# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako 令牌存取（需求 §5.3；设计文档 §5.1）。

与自更新系统的 CDK 同一套策略（见 ``mangaproof/update/cdk_store.py``），
但**独立一份实现与独立的 keyring 用户名**——平台之间不共用凭据代码：

- 桌面端优先系统凭据库 ``keyring``（service ``MangaProof``，username ``poprako_token``）；
- keyring 实测不可用（或 Android 不打包 keyring）→ 回退 ``settings.json`` 明文，
  并且**必须**由界面明示（需求 §5.3、§12.5）；
- 空值 = 清除：keyring 条目与明文字段一起删，避免旧值被下次读取救回来。

``settings`` 参数是**鸭子类型**：任何拥有可读写 ``token`` 属性的对象都行
（正式实现是 ``config.settings.PoprakoSettings``，测试里用轻量桩）。
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from mangaproof.utils.platform import is_android_strict

log = logging.getLogger("mangaproof.poprako.credentials")

#: keyring 的逻辑命名（与 CDK 相同 service，用户名各自独立）
KEYRING_SERVICE = "MangaProof"
KEYRING_USERNAME = "poprako_token"

#: 保存落点（界面据此显示"令牌存哪"）
STORE_KEYRING = "keyring"
STORE_SETTINGS = "settings.json"
STORE_NOWHERE = ""

#: 探测 keyring 可用性用的临时键（用完立即删除，不污染用户凭据）
_PROBE_KEY = "__mangaproof_poprako_probe__"


def _keyring_module() -> Optional[Any]:
    """导入 keyring；Android 上直接返回 ``None``（不打包 keyring）。"""
    if is_android_strict():
        return None
    try:
        import keyring  # noqa: PLC0415 —— 桌面专有依赖，必须延迟导入

        return keyring
    except Exception:
        log.info("keyring 不可用（未安装或导入失败），Poprako 令牌将存于 settings.json")
        return None


def keyring_available() -> bool:
    """实测 keyring **真的可用**（不能只看能不能 import）。

    做法：随机值完整走一遍 set → get → delete；任何一步失败都判不可用。
    """
    module = _keyring_module()
    if module is None:
        return False
    probe_value = secrets.token_hex(8)
    try:
        module.set_password(KEYRING_SERVICE, _PROBE_KEY, probe_value)
        if module.get_password(KEYRING_SERVICE, _PROBE_KEY) != probe_value:
            return False
        return True
    except Exception as exc:
        log.info("keyring 后端不可用，回退到 settings.json：%s", type(exc).__name__)
        return False
    finally:
        try:
            module.delete_password(KEYRING_SERVICE, _PROBE_KEY)
        except Exception:
            pass


def _plaintext(settings: Any) -> str:
    value = getattr(settings, "token", "")
    return value.strip() if isinstance(value, str) else ""


def _set_plaintext(settings: Any, value: str) -> None:
    try:
        setattr(settings, "token", value)
    except Exception:  # pragma: no cover —— 只读桩对象
        log.warning("无法写入 Poprako 令牌明文字段（settings 只读）")


def load_token(settings: Any) -> str:
    """读取令牌：keyring 优先，其次 settings.json 明文。"""
    module = _keyring_module()
    if module is not None:
        try:
            value = module.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
            if value:
                return str(value).strip()
        except Exception as exc:
            log.info("从 keyring 读取 Poprako 令牌失败：%s", type(exc).__name__)
    return _plaintext(settings)


def save_token(settings: Any, token: str) -> str:
    """保存令牌，返回实际落点（:data:`STORE_KEYRING` / :data:`STORE_SETTINGS`）。

    空值等同清除（返回 :data:`STORE_NOWHERE`）。keyring 写成功后**清空明文**，
    保证同一个令牌不会同时存在于两处。
    """
    value = (token or "").strip()
    module = _keyring_module()

    if not value:
        clear_token(settings)
        return STORE_NOWHERE

    if module is not None:
        try:
            module.set_password(KEYRING_SERVICE, KEYRING_USERNAME, value)
            if module.get_password(KEYRING_SERVICE, KEYRING_USERNAME) == value:
                _set_plaintext(settings, "")
                log.info("Poprako 令牌已保存到系统凭据库（keyring）")
                return STORE_KEYRING
        except Exception as exc:
            log.info("写入 keyring 失败，回退 settings.json：%s", type(exc).__name__)

    _set_plaintext(settings, value)
    log.info("Poprako 令牌已保存到 settings.json（明文）")
    return STORE_SETTINGS


def clear_token(settings: Any) -> None:
    """清除令牌：keyring 条目与 settings.json 明文一起删。"""
    module = _keyring_module()
    if module is not None:
        try:
            module.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            pass  # 本来就没有 / 后端不支持删除
    _set_plaintext(settings, "")
    log.info("Poprako 令牌已清除")


def migrate_plaintext(settings: Any) -> bool:
    """发现明文且 keyring 可用时迁入 keyring 并删除明文。

    返回是否真的迁移了。**迁移失败绝不影响登录功能**：明文原样保留，
    下次仍能读到（与 CDK 的迁移策略一致）。
    """
    plaintext = _plaintext(settings)
    if not plaintext:
        return False
    module = _keyring_module()
    if module is None:
        return False
    try:
        module.set_password(KEYRING_SERVICE, KEYRING_USERNAME, plaintext)
        if module.get_password(KEYRING_SERVICE, KEYRING_USERNAME) != plaintext:
            return False
    except Exception:
        return False
    _set_plaintext(settings, "")
    log.info("Poprako 令牌已从 settings.json 迁移到系统凭据库")
    return True


__all__ = [
    "KEYRING_SERVICE",
    "KEYRING_USERNAME",
    "STORE_KEYRING",
    "STORE_SETTINGS",
    "STORE_NOWHERE",
    "keyring_available",
    "load_token",
    "save_token",
    "clear_token",
    "migrate_plaintext",
]
