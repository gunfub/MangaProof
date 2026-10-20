# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""更新凭证（MirrorChyan CDK / GitHub Token）的存储与迁移。

两类凭证走**同一套**存取逻辑，界面上也保持同一套语义（改完不立即生效，
点「保存并检查更新」或「下载更新」时才落盘）：

- **CDK**：MirrorChyan 下载用（需求 §14、§15）；
- **GitHub Token**：仅 api.github.com 的资产元数据请求（2026-10-20 追加），
  用来缓解匿名「60 次/小时/IP」的限流，见 :func:`mangaproof.update.sources.github_plan`。

存储策略（两类凭证一致）：

- **桌面端**：优先系统凭据库（keyring），逻辑命名固定为
  ``service = MangaProof``、``username = mirrorchyan_cdk / github_token``；
  但**不能只判断 import 是否成功** —— 必须实测当前 backend 能否
  ``set`` / ``get`` / ``delete`` 成功（无桌面会话的 Linux、纯 SSH 环境里
  keyring 能 import 却用不了）；
- **失败回退**：写 ``settings.json`` 的 ``update.mirrorchyan_cdk`` /
  ``update.github_token`` 明文；
- **Android**：不打包 keyring（需求 §14），直接存 settings.json
  （应用私有目录，只有应用自己可读）；
- **清空**（2026-10-20 追加）：界面上把凭证清空并保存时，**两处都要删干净**
  —— keyring 条目 + settings.json 明文。只清白字不删 keyring 的话，
  :func:`load_cdk` 下次会把旧值读回来（表现为"清掉了、重开又出现"）。

迁移（需求 §15）：桌面端若发现 settings.json 里有明文 CDK 且 keyring 可用，
则 写入 keyring → 重新读取验证 → 删除明文。keyring 不可用时**保留明文**，
且**绝不因为 keyring 失败而禁用更新功能**。

安全（需求 §16）：凭证不进日志。本模块所有日志只输出"存到哪了"，
不输出值本身；异常文本一律过 :func:`mangaproof.update.redact.redact_text`。
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from mangaproof.utils.platform import is_android_strict

log = logging.getLogger("mangaproof.update.cdk_store")

#: keyring 的逻辑命名（需求 §14：逻辑命名必须固定）
KEYRING_SERVICE = "MangaProof"
KEYRING_USERNAME = "mirrorchyan_cdk"
KEYRING_USERNAME_GITHUB_TOKEN = "github_token"

STORED_IN_KEYRING = "keyring"
STORED_IN_SETTINGS = "settings.json"
#: 空值：两处都已清除，没存到任何地方
STORED_NOWHERE = ""

#: 探测 keyring 可用性时用的临时键（用完立即删除，不污染用户凭据）
_PROBE_USERNAME = "__mangaproof_probe__"


def _keyring_module() -> Any | None:
    """导入 keyring；Android 上直接返回 ``None``（需求 §14：不打包 keyring）。"""
    if is_android_strict():
        return None
    try:
        import keyring  # noqa: PLC0415 —— 桌面专有依赖，必须延迟导入

        return keyring
    except Exception:
        log.info("keyring 不可用（未安装或导入失败），CDK 将存于 settings.json")
        return None


def keyring_available() -> bool:
    """实测 keyring 是否**真的可用**（需求 §14：不能只看 import）。

    做法：用随机值做一次完整的 set → get → delete。
    任何一步失败都判为不可用；探测键用完即删，不留残留。
    """
    module = _keyring_module()
    if module is None:
        return False
    probe_value = secrets.token_hex(8)
    try:
        module.set_password(_PROBE_USERNAME, _PROBE_USERNAME, probe_value)
        if module.get_password(_PROBE_USERNAME, _PROBE_USERNAME) != probe_value:
            return False
        return True
    except Exception as exc:
        log.info("keyring 后端不可用，回退到 settings.json：%s", type(exc).__name__)
        return False
    finally:
        try:
            module.delete_password(_PROBE_USERNAME, _PROBE_USERNAME)
        except Exception:
            pass


# --- 通用存取（CDK / GitHub Token 共用） -------------------------------------

def _load_secret(update_settings: Any, *, attr: str, username: str) -> str:
    """读取一项凭证：keyring 优先，其次 settings.json 明文（需求 §14）。"""
    module = _keyring_module()
    if module is not None:
        try:
            value = module.get_password(KEYRING_SERVICE, username)
            if value:
                return str(value)
        except Exception as exc:
            log.info("从 keyring 读取 %s 失败：%s", username, type(exc).__name__)
    return str(getattr(update_settings, attr, "") or "")


def _save_secret(
    update_settings: Any, *, attr: str, username: str, value: str, label: str
) -> str:
    """保存一项凭证，返回实际落点（``keyring`` / ``settings.json`` / 空串）。

    **空值 = 清除**（2026-10-20）：keyring 条目与 settings.json 明文一起删。
    以前空值只写白字不删 keyring，于是下次打开更新页又会把旧值读回来。
    """
    value = (value or "").strip()
    module = _keyring_module()

    if not value:
        if module is not None:
            try:
                module.delete_password(KEYRING_SERVICE, username)
            except Exception:
                pass                       # 本来就没有 / 后端不支持删除
        setattr(update_settings, attr, "")
        log.info("%s 已清空（keyring 条目与 settings.json 明文均已删除）", label)
        return STORED_NOWHERE

    if module is not None:
        try:
            module.set_password(KEYRING_SERVICE, username, value)
            if module.get_password(KEYRING_SERVICE, username) == value:
                setattr(update_settings, attr, "")
                log.info("%s 已保存到系统凭据库（keyring）", label)
                return STORED_IN_KEYRING
        except Exception as exc:
            log.info("写入 keyring 失败，回退 settings.json：%s", type(exc).__name__)
            # 写不进 keyring 时把旧条目删掉：否则 _load_secret 会继续读到
            # keyring 里的旧值，把刚写进 settings.json 的新值盖掉。
            try:
                module.delete_password(KEYRING_SERVICE, username)
            except Exception:
                pass

    setattr(update_settings, attr, value)
    log.info("%s 已保存到 settings.json%s", label,
             "（Android：应用私有目录）" if is_android_strict() else "（明文，keyring 不可用）")
    return STORED_IN_SETTINGS


# --- MirrorChyan CDK ---------------------------------------------------------

def load_cdk(update_settings: Any) -> str:
    """读取 CDK：keyring 优先，其次 settings.json 明文（需求 §14）。"""
    return _load_secret(
        update_settings, attr="cdk", username=KEYRING_USERNAME
    )


def save_cdk(update_settings: Any, value: str) -> str:
    """保存 CDK，返回实际落点（``keyring`` / ``settings.json``；清空时为空串）。

    桌面端写入 keyring 成功时，**清空** settings.json 里的明文；
    失败则写明文（需求 §14：不得因 keyring 失败而禁用更新功能）。
    """
    return _save_secret(
        update_settings,
        attr="cdk",
        username=KEYRING_USERNAME,
        value=value,
        label="CDK",
    )


def clear_cdk(update_settings: Any) -> None:
    """清除 CDK（两处都清）。"""
    save_cdk(update_settings, "")


def migrate_plaintext_cdk(update_settings: Any) -> bool:
    """需求 §15：settings.json 明文 CDK → keyring → 验证 → 删除明文。

    :returns: 是否真的完成了迁移（``False`` 表示无需迁移或 keyring 不可用）。
    """
    plaintext = str(getattr(update_settings, "cdk", "") or "").strip()
    if not plaintext or is_android_strict():
        return False
    if not keyring_available():
        log.info("keyring 不可用，保留 settings.json 中的明文 CDK")
        return False

    module = _keyring_module()
    assert module is not None  # keyring_available() 已确认
    try:
        module.set_password(KEYRING_SERVICE, KEYRING_USERNAME, plaintext)
        if module.get_password(KEYRING_SERVICE, KEYRING_USERNAME) != plaintext:
            log.warning("CDK 迁移失败（回读不一致），保留明文")
            return False
    except Exception as exc:
        log.warning("CDK 迁移失败（%s），保留明文", type(exc).__name__)
        return False

    update_settings.cdk = ""
    log.info("CDK 已从 settings.json 迁移到系统凭据库，并删除明文")
    return True


# --- GitHub Token（2026-10-20 追加） -----------------------------------------

def load_github_token(update_settings: Any) -> str:
    """读取 GitHub Token：keyring 优先，其次 settings.json 明文。"""
    return _load_secret(
        update_settings, attr="github_token", username=KEYRING_USERNAME_GITHUB_TOKEN
    )


def save_github_token(update_settings: Any, value: str) -> str:
    """保存 GitHub Token，返回实际落点；传空串即两处一起清除。"""
    return _save_secret(
        update_settings,
        attr="github_token",
        username=KEYRING_USERNAME_GITHUB_TOKEN,
        value=value,
        label="GitHub Token",
    )


def clear_github_token(update_settings: Any) -> None:
    """清除 GitHub Token（两处都清）。"""
    save_github_token(update_settings, "")


__all__ = [
    "KEYRING_SERVICE",
    "KEYRING_USERNAME",
    "KEYRING_USERNAME_GITHUB_TOKEN",
    "STORED_IN_KEYRING",
    "STORED_IN_SETTINGS",
    "STORED_NOWHERE",
    "clear_cdk",
    "clear_github_token",
    "keyring_available",
    "load_cdk",
    "load_github_token",
    "migrate_plaintext_cdk",
    "save_cdk",
    "save_github_token",
]
