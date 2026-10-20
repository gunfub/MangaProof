# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako 请求异常族（需求 §5.8、§9.5）。

设计原则（与 ``docs/1.2.0开发需求.md`` §12 一致）：

- 异常**只携带** HTTP 状态码与业务 code，**绝不**携带令牌、密码、请求体；
- 异常文本会被写进日志、弹进对话框、甚至进 traceback，所以"构造时就干净"，
  而不是指望调用方记得脱敏；
- UI 只按异常**类型**分支（:func:`user_message`），不解析字符串。
"""

from __future__ import annotations

__all__ = [
    "PoprakoError",
    "PoprakoNetworkError",
    "PoprakoAuthError",
    "PoprakoForbiddenError",
    "PoprakoValidationError",
    "PoprakoHTTPError",
    "user_message",
]


class PoprakoError(Exception):
    """Poprako 交互失败的基类。"""

    def __init__(self, message: str, *, status: int | None = None, code: int | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class PoprakoNetworkError(PoprakoError):
    """连不上 / 超时 / 传输层错误（已按策略重试过）。"""

    def __init__(self) -> None:
        super().__init__("网络请求失败")


class PoprakoAuthError(PoprakoError):
    """HTTP 401：令牌无效或过期。"""

    def __init__(self, status: int = 401, code: int | None = None):
        super().__init__(f"HTTP {status}" + (f"（code {code}）" if code is not None else ""),
                         status=status, code=code)


class PoprakoForbiddenError(PoprakoError):
    """HTTP 403：已认证但无权限（导入需要该章节的 REVIEWER 指派）。"""

    def __init__(self, status: int = 403, code: int | None = None):
        super().__init__(f"HTTP {status}" + (f"（code {code}）" if code is not None else ""),
                         status=status, code=code)


class PoprakoValidationError(PoprakoError):
    """HTTP 422：章节不存在/已发布，或提交字段不合法。"""

    def __init__(self, status: int = 422, code: int | None = None):
        super().__init__(f"HTTP {status}" + (f"（code {code}）" if code is not None else ""),
                         status=status, code=code)


class PoprakoHTTPError(PoprakoError):
    """其余状态码（含 404 / 429 / 5xx）。"""


def user_message(error: BaseException) -> str:
    """把异常翻译成一句能直接显示给用户的中文（需求 §9.5 的失败矩阵）。"""
    if isinstance(error, PoprakoNetworkError):
        return "无法连接 api.poprako.com（请检查网络或代理）；整体替换是幂等的，可安全重试"
    if isinstance(error, PoprakoAuthError):
        return "登录已失效，请重新登录"
    if isinstance(error, PoprakoForbiddenError):
        return "需要该章节的「监制」指派，管理员身份不足以同步"
    if isinstance(error, PoprakoValidationError):
        code = f" / code {error.code}" if error.code is not None else ""
        return f"提交内容被拒绝（HTTP 422{code}），请检查页面映射与预览"
    if isinstance(error, PoprakoHTTPError):
        status = error.status or 0
        if status == 404:
            return "章节或复合图页已不存在，请刷新目标"
        if status == 429:
            return "请求过于频繁，请稍后再试"
        if status >= 500:
            return f"服务器错误（HTTP {status}），稍后重试；重试是安全的"
        return f"请求失败（HTTP {status}）"
    return str(error)
