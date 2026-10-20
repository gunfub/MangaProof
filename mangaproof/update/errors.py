# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""更新流程的错误类型与用户可见文案。

分层原则：

- :class:`UpdateError` 是所有"能直接展示给用户"的错误的基类；
- 子类各自带上下文（错误码、URL、平台），但**不得携带 CDK**（需求 §16）——
  构造消息时一律用 :mod:`mangaproof.update.redact` 过滤。
"""

from __future__ import annotations

from mangaproof.update.redact import redact_text


class UpdateError(Exception):
    """更新流程的用户可见错误。"""


class UnsupportedPlatformError(UpdateError):
    """当前系统/架构没有对应发行包（需求 §9）。

    文案直接采用需求原文："明确提示当前系统架构暂不支持更新"。
    """

    def __init__(self, detail: str = ""):
        msg = "当前系统架构暂不支持更新"
        if detail:
            msg = f"{msg}（{detail}）"
        super().__init__(msg)


class NetworkError(UpdateError):
    """网络层失败（连不上、超时、TLS、被代理拦）。

    ``detail`` 单独留着（已脱敏），供调用方在"回落重试也失败"之类的场景里
    拼出更完整的说明，而不必再去解析 ``str(exc)`` 的前缀。
    """

    def __init__(self, action: str, detail: str):
        self.action = action
        self.detail = redact_text(detail)
        super().__init__(f"{action}失败：{self.detail}")


class ProxyTestError(NetworkError):
    """代理测试失败（需求 §29：必须明确显示成功/失败及错误原因）。"""


class MirrorChyanError(UpdateError):
    """MirrorChyan 业务错误（HTTP 200 + body 里的 ``code``）。

    错误码语义来自官方 ``MirrorChyan/docs/ErrorCode.md``（见调研报告 §3.1）。
    """

    #: 官方错误码 → 用户可读文案
    MESSAGES: dict[int, str] = {
        1001: "请求参数不正确（请把此问题反馈给开发者）",
        7001: "CDK 已过期，请到 MirrorChyan 续期后再试",
        7002: "CDK 无效，请检查后重新输入",
        7003: "CDK 今日下载次数已达上限，可改用 Cloudflare R2 渠道",
        7004: "该 CDK 与 MangaProof 不匹配，请确认购买的是本软件的 CDK",
        7005: "CDK 已被封禁，请联系 MirrorChyan 支持",
        8001: "MirrorChyan 上没有当前系统/架构的发行包",
        8002: "系统参数不被支持（请把此问题反馈给开发者）",
        8003: "架构参数不被支持（请把此问题反馈给开发者）",
        8004: "更新分支参数不被支持（请把此问题反馈给开发者）",
    }

    def __init__(self, code: int, msg: str = ""):
        self.code = int(code)
        self.server_msg = redact_text(str(msg or ""))
        text = self.MESSAGES.get(self.code)
        if not text:
            if self.code < 0:
                text = f"MirrorChyan 服务端严重错误（{self.server_msg or self.code}）"
            else:
                text = self.server_msg or f"MirrorChyan 返回未知错误（code={self.code}）"
        super().__init__(text)


class GitHubTokenError(UpdateError):
    """GitHub Token 被服务端拒绝（401 无效 / 403 无权或配额用尽）。

    这个异常**不会**直接展示给用户：:func:`mangaproof.update.sources.github_plan`
    会捕获它、记一条提示（进 ``DownloadPlan.notes``），然后自动回落匿名请求
    （2026-10-20 的需求：无效 token 不能把更新流程卡死）。

    只携带 HTTP 状态与一句可读原因，**绝不携带 token 本身**（需求 §16）。
    """

    def __init__(self, status: int, detail: str = ""):
        self.status = int(status)
        self.server_detail = redact_text(str(detail or ""))
        super().__init__(self.reason())

    def reason(self) -> str:
        """给用户看的一句话（不含 token、不含服务端原始 body）。"""
        if self.status == 401:
            return "GitHub Token 无效或已过期（HTTP 401）"
        if self.status == 403:
            return "GitHub Token 被拒绝（HTTP 403：权限不足或该 Token 配额已用尽）"
        return f"GitHub Token 不可用（HTTP {self.status}）"


class VersionInfoError(UpdateError):
    """服务端返回的版本号无法解析（需求 §3/§4 无法比较）。

    实际案例：MirrorChyan 的 alpha 通道当前返回字面量 ``"alpha"``。
    这时必须**明确报错**，绝不能静默当成"已是最新版本"——
    那会让用户以为自己在最新版，而实际上只是版本信息坏了。
    """

    def __init__(self, raw_version: str, channel: str):
        self.raw_version = raw_version
        self.channel = channel
        super().__init__(
            f"更新分支 {channel} 的版本信息异常（服务端返回 {raw_version!r}），"
            "暂时无法判断是否有新版本"
        )


class DownloadError(UpdateError):
    """下载/写盘失败。"""


class InstallerError(UpdateError):
    """安装器准备或启动失败（需求 §46：启动失败时主程序保持运行并报告错误）。"""
