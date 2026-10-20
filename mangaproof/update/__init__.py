# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""MangaProof 自更新系统（需求 §5）。

子模块分工（对应需求 §79 的推荐结构）：

- :mod:`version`   —— 版本号解析与比较（§3/§4）
- :mod:`detector`  —— OS/CPU 识别与 7 组 MirrorChyan 映射（§9）
- :mod:`redact`    —— CDK 等敏感信息脱敏（§16）
- :mod:`checksum`  —— 流式 SHA-256 校验（§53）
- :mod:`mirrorchyan` / :mod:`downloader` / :mod:`manager` / :mod:`installer`
  / :mod:`privilege` / :mod:`platform` —— 见各自模块文档

设计约束（全局，改代码前先读）：

1. **检查阶段零凭据（指 CDK）**：MirrorChyan 检查一律使用**不带 CDK** 的请求，
   检查函数不得有 cdk 形参（§17）。2026-10-20 补充：GitHub Token 是另一回事
   —— 它只用于 ``api.github.com`` 的资产元数据请求（检查阶段显示文件/大小、
   下载阶段解析资产与 digest，同一个函数），不发往 MirrorChyan、R2 或字节下载，
   也不进入 :func:`mangaproof.update.mirrorchyan.check_update` 的签名；
2. **CDK 只在"立即更新"阶段使用**（§18/§20），且绝不进日志/traceback/URL 日志（§16）；
3. **平台专有逻辑运行时导入**，不让 Windows 加载 Linux 依赖、反之亦然（§6）；
4. **所有耗时操作不得阻塞 GUI**：一律 QThread + Signal（§5）。
"""

from __future__ import annotations

__all__ = ["version", "detector", "redact", "checksum"]
