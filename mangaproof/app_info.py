# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""项目的对外链接（「关于」页与测试共用的一处定义）。

为什么单独放一个模块，而不是直接用 `mangaproof/update/sources.py` 里的
`GITHUB_OWNER` / `GITHUB_REPO` 拼：那两个常量属于**更新器**（那个模块导入时
就会拉起 httpx 网络栈）。「关于」页只是给人看几行网址，没必要为了显示一行
链接把网络依赖带进 UI 路径。代价是地址写了两处，所以 `tests/test_about_page.py`
守卫"关于页的仓库地址 == 更新器用的 owner/repo"，将来改名不会漏改。
"""

from __future__ import annotations

#: GitHub 仓库：源码、Release、Issue 都在这里
SOURCE_URL = "https://github.com/gunfub/MangaProof"

#: 官网：下载入口与使用说明
WEBSITE_URL = "https://mangaproof.priloba.com/"

#: 意见与反馈：**直接开写**新 Issue（省掉"先进仓库页再找按钮"那一步）
FEEDBACK_URL = f"{SOURCE_URL}/issues/new"

#: 已有反馈列表：提交前先看有没有人提过同样的问题，避免重复
ISSUES_URL = f"{SOURCE_URL}/issues"
