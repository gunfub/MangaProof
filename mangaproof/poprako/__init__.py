# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako 平台接入（1.2.0）。

**本包是独立平台模块**：数据结构、鉴权、请求、匹配、转换全部自成一套，
不与其它平台共享任何业务代码（见 `docs/1.2.0开发需求.md` §4）。

两条硬约束：

- 本包**不得**导入 PySide6 —— 保证它能用 ``httpx.MockTransport`` 离线单测，
  也保证任何非 UI 路径都不会被拖进 Qt 依赖；
- 本包**不得**导入其它平台模块，也不存在"通用同步层"：每个平台各写各的，
  哪怕初版看起来相同。
"""

from __future__ import annotations

#: 界面文案里使用的平台名（「同步到 Poprako…」取它拼）
PLATFORM_NAME = "Poprako"

#: 站点主页（关于/帮助文案用）
SITE_URL = "https://poprako.com"

#: 生产 API 基址（poprako-web README 明确：生产构建经它访问 poprako-server）
API_BASE_URL = "https://api.poprako.com/api/v1"

__all__ = ["PLATFORM_NAME", "SITE_URL", "API_BASE_URL"]
