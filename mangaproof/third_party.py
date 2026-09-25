# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""第三方组件与许可证信息（独立的「第三方许可」页面数据）。

格式遵循业界惯例（Chromium chrome://credits、Flutter LicenseRegistry、
VS Code Third Party Notices）：组件名 + 版本 + SPDX 许可证标识 +
版权声明 + 主页 + 许可证全文（过长的 GPL/LGPL 提供摘要与官方链接）。

版本号优先从已安装包元数据（importlib.metadata）解析，缺失时回退
到随代码记录的版本。

同一份数据会导出为仓库根目录的 `THIRD_PARTY_LICENSES.md`
（`scripts/build_third_party_doc.py`），供不运行程序的人直接查阅；
两者由 tests/test_third_party_doc.py 守卫一致性。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("mangaproof.third_party")

MIT_LICENSE = """MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE."""

BSD3_LICENSE = """BSD 3-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE."""

HPND_LICENSE = """Historical Permission Notice and Disclaimer (MIT-CMU)

The Python Imaging Library (PIL) is

    Copyright © 1995-2011 by Secret Labs AB
    Copyright © 1995-2011 by Fredrik Lundh

Pillow is the friendly PIL fork. It is

    Copyright © 2010-2025 by Jeffrey A. Clark (Alex) and contributors.

By obtaining, using, and/or copying this software and/or its associated
documentation, you agree that you have read, understood, and will comply
with the following terms and conditions:

Permission to use, copy, modify and distribute this software and its
documentation for any purpose and without fee is hereby granted, provided
that the above copyright notice appears in all copies, and that both that
copyright notice and this permission notice appear in supporting
documentation, and that the name of Secret Labs AB or the author not be used
in advertising or publicity pertaining to distribution of the software
without specific, written prior permission.

SECRET LABS AB AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS.
IN NO EVENT SHALL SECRET LABS AB OR THE AUTHOR BE LIABLE FOR ANY SPECIAL,
INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE."""

PSF_LICENSE = """PSF LICENSE AGREEMENT FOR PYTHON 3.x

1. This LICENSE AGREEMENT is between the Python Software Foundation ("PSF"),
and the Individual or Organization ("Licensee") accessing and otherwise using
Python software in source or binary form and its associated documentation.

2. Subject to the terms and conditions of this License Agreement, PSF hereby
grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
analyze, test, perform and/or display publicly, prepare derivative works,
distribute, and otherwise use Python alone or in any derivative version,
provided, however, that PSF's License Agreement and PSF's notice of copyright,
i.e., "Copyright © 2001-2026 Python Software Foundation; All Rights Reserved"
are retained in Python alone or in any derivative version prepared by Licensee.

3. In the event Licensee prepares a derivative work that is based on or
incorporates Python or any part thereof, and wants to make the derivative work
available to others as provided herein, then Licensee hereby agrees to include
in any such work a brief summary of the changes made to Python.

4. PSF is making Python available to Licensee on an "AS IS" basis. PSF MAKES
NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR IMPLIED. BY WAY OF EXAMPLE, BUT
NOT LIMITATION, PSF MAKES NO AND DISCLAIMS ANY REPRESENTATION OR WARRANTY OF
MERCHANTABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF
PYTHON WILL NOT INFRINGE ANY THIRD PARTY RIGHTS.

5. PSF SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF PYTHON FOR ANY
INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS AS A RESULT OF MODIFYING,
DISTRIBUTING, OR OTHERWISE USING PYTHON, OR ANY DERIVATIVE THEREOF, EVEN IF
ADVISED OF THE POSSIBILITY THEREOF.

6. This License Agreement will automatically terminate upon a material breach
of its terms and conditions.

7. This License Agreement shall be governed by the federal law of the United
States of America.

完整文本：https://docs.python.org/3/license.html"""

LGPL3_NOTICE = """GNU LESSER GENERAL PUBLIC LICENSE, Version 3（LGPL-3.0）

PySide6 / Qt for Python 采用 LGPL-3.0-only 授权（亦可选择 GPL-2.0-only、
GPL-3.0-only 或商业授权）。

要点（非法律建议）：
- 允许以动态链接方式在本软件中使用 Qt 库，无需公开本软件源代码；
- 若对 Qt 库本身做出修改，修改部分需以 LGPLv3 提供；
- 分发本软件时需保留 Qt 的版权与许可证声明。

完整许可证文本（官方链接）：
https://www.gnu.org/licenses/lgpl-3.0.html
https://doc.qt.io/qt-6/lgpl.html"""

GPL2_EXCEPTION_NOTICE = """GNU General Public License v2 或更高版本（含 bootloader 例外）

PyInstaller 以 GPL-2.0-or-later 授权，并附带特殊例外条款：

"We hereby grant you an exclusive permission that allows you to use the
PyInstaller bootloader, in executable form, to build and distribute
non-free programs (including commercial ones)."

即：允许使用 PyInstaller 打包并分发非自由（含商业）软件，本软件的
分发不受 GPL 传染。

完整许可证文本（官方链接）：
https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt"""

GPL2_NOTICE = """GNU General Public License v2 或更高版本（GPL-2.0-or-later）

- PyInstaller 社区 hooks 集（pyinstaller-hooks-contrib）以 GPL-2.0-or-later
  授权；其中**随可执行文件分发**的运行期 hook 部分单独采用 Apache-2.0；
- 本项目仅在打包期使用它生成可执行文件，不修改、也不再分发其本体。

完整许可证文本（官方链接）：
https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/develop/LICENSE"""

# ---------------------------------------------------------------------------
# Android 打包工具链（PySide6 官方 pyside6-android-deploy 及其依赖）
# ---------------------------------------------------------------------------
# 这些组件**只在构建 APK 时使用，不随应用分发**；但按"开源组件与许可透明"的原则，
# 在「关于 → 第三方许可」里对所有平台一致展示（不区分平台）。

APACHE20_NOTICE = """Apache License 2.0（Apache-2.0）

- 允许商业使用、修改与再分发；需保留版权与许可声明，随附 NOTICE 文件（若有）；
- 分发修改版时需说明改动；
- 含专利授权；若对贡献者发起专利诉讼，该授权终止；
- 软件按"现状"提供，不附带任何明示或默示担保。

完整许可证文本（官方链接）：
https://www.apache.org/licenses/LICENSE-2.0"""

MPL2_NOTICE = """Mozilla Public License 2.0（MPL-2.0）

- 文件级 copyleft：被修改过的 MPL 源文件需继续以 MPL 提供，可与其它许可的
  代码组合、链接；
- 需保留版权与许可声明；
- 软件按"现状"提供，不附带任何担保。

完整许可证文本（官方链接）：
https://www.mozilla.org/MPL/2.0/"""

AGPL3_NOTICE = """GNU Affero General Public License v3（AGPL-3.0-or-later）

- 本项目**仅把 Nuitka 当作构建期工具**：把 Python 源码编译成 C 再交叉编译成
  Android 原生库；不修改、不再分发 Nuitka 本体，最终产物中也不包含 Nuitka；
- 若你打算再分发 Nuitka 本体或其修改版，需自行遵守 AGPL 的源码提供义务。

完整许可证文本（官方链接）：
https://www.gnu.org/licenses/agpl-3.0.txt"""

ANDROID_SDK_NOTICE = """Android SDK（cmdline-tools / build-tools / platform）

- Android SDK 以 Apache License 2.0 授权（© Google LLC）；
- 本项目仅用它**构建** Android 安装包，不随应用分发。

官方链接：
https://developer.android.com/studio/terms
https://www.apache.org/licenses/LICENSE-2.0"""

ANDROID_NDK_NOTICE = """Android NDK（Native Development Kit，r27c）

- NDK 适用《Android NDK License Agreement》（© Google LLC，专有许可）；
  以其编译出的产物不受该协议约束；
- 本项目仅用它**构建** Android 安装包（交叉编译 Python 依赖闭包），
  不随应用分发。

官方链接：
https://developer.android.com/ndk/downloads
https://developer.android.com/studio/terms"""

MISANS_LICENSE = """MiSans 字体知识产权许可协议

本《MiSans 字体知识产权许可协议》（以下简称“协议”）是您与小米科技有限
责任公司（以下简称“小米”或“许可方”）之间有关安装、使用 MiSans 字体
（以下简称“MiSans”或“MiSans 字体”）的法律协议。您在使用 MiSans 的所有或
任何部分前，应接受本协议中规定的所有条款和条件。安装、使用 MiSans 的行为
表示您同意接受本协议所有条款的约束。否则，请不要安装或使用 MiSans，并应
立即销毁和删除所有 MiSans 字体包。

根据本协议的条款和条件，许可方在此授予您一份不可转让的、非独占的、免版税
的、可撤销的、全球性的版权许可，使您依照本协议约定使用 MiSans 字体，前提
是符合下列条件：

1. 您应在软件中特别注明使用了 MiSans 字体。
2. 您不得对 MiSans 字体或其任何单独组件进行改编或二次开发。
3. 您不得单独将 MiSans 字体或其组件对外租赁、再许可、给予、出借或进一步
   分发字体软件或其任何副本以及重新分发或售卖。此限制不适用于您使用 MiSans
   字体创作的任何其他作品。如您使用 MiSans 字体创作宣传素材、logo、应用 App
   等，您有权分发或出售该作品。

下载地址：https://hyperos.mi.com/font/download"""


# Noto Sans Symbols 2（符号回退字体，OFL-1.1）。
# 许可全文内嵌在下方（NOTO_SYMBOLS2_LICENSE），展示于「关于 → 第三方许可」；
# 不再单独随包放一份 .txt（软件内即可查阅，避免同一份协议两处维护）。
NOTO_SYMBOLS2_LICENSE = """Copyright 2022 The Noto Project Authors (https://github.com/notofonts/symbols)

This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
https://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE."""

@dataclass(frozen=True)
class ThirdPartyItem:
    """单个第三方组件条目。"""

    name: str          # 组件名
    version: str       # 版本
    spdx: str          # 许可证标识（SPDX）
    copyright: str     # 版权声明
    homepage: str      # 主页/源码地址
    license_text: str  # 许可证全文或摘要+官方链接


def _resolve_version(dist_name: str, fallback: str) -> str:
    """优先从已安装包元数据解析版本；失败回退到记录值。"""
    try:
        from importlib.metadata import version

        return version(dist_name)
    except Exception:
        return fallback


def build_third_party_items() -> list[ThirdPartyItem]:
    return [
        ThirdPartyItem(
            "Python（运行时）",
            _resolve_version("Python", "3.12"),
            "PSF-2.0",
            "© 2001-2026 Python Software Foundation",
            "https://www.python.org/",
            PSF_LICENSE,
        ),
        ThirdPartyItem(
            "psd-tools（PSD 解析）",
            _resolve_version("psd-tools", "1.18.0"),
            "MIT",
            "© psd-tools contributors",
            "https://github.com/psd-tools/psd-tools",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "NumPy（图像分析）",
            _resolve_version("numpy", "2.5.2"),
            "BSD-3-Clause",
            "© 2005-2026 NumPy Developers",
            "https://numpy.org/",
            BSD3_LICENSE,
        ),
        ThirdPartyItem(
            "PySide6 / Qt（GUI 框架）",
            f'{_resolve_version("PySide6", "6.11.2")}（含部署工具 '
            "pyside6-deploy / pyside6-android-deploy）",
            "LGPL-3.0-only",
            "© The Qt Company Ltd.",
            "https://www.qt.io/",
            LGPL3_NOTICE,
        ),
        ThirdPartyItem(
            "shiboken6（PySide6 绑定运行时）",
            _resolve_version("shiboken6", "6.11.2"),
            "LGPL-3.0-only（亦可选 GPL-2.0 / GPL-3.0 或商业授权）",
            "© The Qt Company Ltd.",
            "https://pyside.org/",
            LGPL3_NOTICE,
        ),
        ThirdPartyItem(
            "reportlab（PDF 生成）",
            _resolve_version("reportlab", "5.0.1"),
            "BSD-3-Clause",
            "© 2000-2026 ReportLab Inc.",
            "https://www.reportlab.com/",
            BSD3_LICENSE,
        ),
        ThirdPartyItem(
            "Pillow（图像处理）",
            _resolve_version("pillow", "12.3.0"),
            "HPND（MIT-CMU）",
            "© 1995-2011 Secret Labs AB / Fredrik Lundh；Pillow 贡献者",
            "https://python-pillow.org/",
            HPND_LICENSE,
        ),
        ThirdPartyItem(
            "attrs（psd-tools 依赖）",
            _resolve_version("attrs", "26.1.0"),
            "MIT",
            "© 2015-2026 Hynek Schlawack",
            "https://www.attrs.org/",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "typing-extensions（psd-tools 依赖）",
            _resolve_version("typing-extensions", "4.16.0"),
            "PSF-2.0",
            "© Python Software Foundation 及 typing_extensions 贡献者",
            "https://github.com/python/typing_extensions",
            PSF_LICENSE,
        ),
        ThirdPartyItem(
            "charset-normalizer（reportlab 依赖）",
            _resolve_version("charset-normalizer", "3.5.1"),
            "MIT",
            "© 2019-2026 Ahmed TAHRI（jawah）",
            "https://github.com/jawah/charset_normalizer",
            MIT_LICENSE,
        ),
        # -- 自更新系统（需求 §8 httpx / §14 keyring；仅桌面端，Android 不打包 keyring）--
        # 运行期依赖：全部随桌面产物分发，因此必须出现在本清单与
        # THIRD_PARTY_LICENSES.md 中（tests/test_third_party_doc.py 逐字节守卫）。
        ThirdPartyItem(
            "httpx（自更新 HTTP 客户端）",
            _resolve_version("httpx", "0.28.1"),
            "BSD-3-Clause",
            "© 2019 Encode OSS Ltd.",
            "https://www.python-httpx.org/",
            BSD3_LICENSE,
        ),
        ThirdPartyItem(
            "httpcore（httpx 底层传输）",
            _resolve_version("httpcore", "1.0.9"),
            "BSD-3-Clause",
            "© 2020 Encode OSS Ltd.",
            "https://github.com/encode/httpcore",
            BSD3_LICENSE,
        ),
        ThirdPartyItem(
            "h11（HTTP/1.1 协议实现）",
            _resolve_version("h11", "0.16.0"),
            "MIT",
            "© 2016 Nathaniel J. Smith 及贡献者",
            "https://github.com/python-hyper/h11",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "anyio（异步 I/O 抽象）",
            _resolve_version("anyio", "4.15.1"),
            "MIT",
            "© 2018 Alex Grönholm",
            "https://github.com/agronholm/anyio",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "sniffio（异步库探测）",
            _resolve_version("sniffio", "1.3.1"),
            "MIT OR Apache-2.0",
            "© 2018 Alex Grönholm 及贡献者",
            "https://github.com/python-trio/sniffio",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "certifi（CA 证书包）",
            _resolve_version("certifi", "2026.7.22"),
            "MPL-2.0",
            "© Kenneth Reitz 及 certifi 贡献者",
            "https://github.com/certifi/python-certifi",
            MPL2_NOTICE,
        ),
        ThirdPartyItem(
            "idna（国际化域名）",
            _resolve_version("idna", "3.20"),
            "BSD-3-Clause",
            "© 2013-2026 Kim Davies 及贡献者",
            "https://github.com/kjd/idna",
            BSD3_LICENSE,
        ),
        ThirdPartyItem(
            "socksio（SOCKS 代理支持，httpx[socks]）",
            _resolve_version("socksio", "1.0.0"),
            "MIT",
            "© 2020 Seth Michael Larson",
            "https://github.com/sethmlarson/socksio",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "keyring（系统凭据库访问）",
            _resolve_version("keyring", "25.7.0"),
            "MIT",
            "© 2011-2026 Jason R. Coombs 及贡献者",
            "https://github.com/jaraco/keyring",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "jaraco.classes（keyring 依赖）",
            _resolve_version("jaraco.classes", "3.4.0"),
            "MIT",
            "© Jason R. Coombs",
            "https://github.com/jaraco/jaraco.classes",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "jaraco.context（keyring 依赖）",
            _resolve_version("jaraco.context", "6.1.2"),
            "MIT",
            "© Jason R. Coombs",
            "https://github.com/jaraco/jaraco.context",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "jaraco.functools（keyring 依赖）",
            _resolve_version("jaraco.functools", "4.6.0"),
            "MIT",
            "© Jason R. Coombs",
            "https://github.com/jaraco/jaraco.functools",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "more-itertools（jaraco.functools 依赖）",
            _resolve_version("more-itertools", "11.1.0"),
            "MIT",
            "© 2012 Erik Rose",
            "https://github.com/more-itertools/more-itertools",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "SecretStorage（keyring 的 Linux 后端）",
            _resolve_version("SecretStorage", "3.5.0"),
            "BSD-3-Clause",
            "© 2012-2026 Dmitry Shachnev",
            "https://github.com/mitya57/secretstorage",
            BSD3_LICENSE,
        ),
        ThirdPartyItem(
            "jeepney（SecretStorage 的 D-Bus 实现）",
            _resolve_version("jeepney", "0.9.0"),
            "MIT",
            "© 2017-2026 Thomas Kluyver",
            "https://gitlab.com/takluyver/jeepney",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "cryptography（SecretStorage 依赖，仅 Linux 桌面）",
            _resolve_version("cryptography", "50.0.1"),
            "Apache-2.0 OR BSD-3-Clause",
            "© The Python Cryptographic Authority 及贡献者",
            "https://github.com/pyca/cryptography",
            APACHE20_NOTICE,
        ),
        ThirdPartyItem(
            "cffi（cryptography 依赖）",
            _resolve_version("cffi", "2.1.1"),
            "MIT",
            "© 2013-2026 Armin Rigo 及贡献者",
            "https://cffi.readthedocs.io/",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "pycparser（cffi 依赖）",
            _resolve_version("pycparser", "3.0"),
            "BSD-3-Clause",
            "© 2008-2026 Eli Bendersky",
            "https://github.com/eliben/pycparser",
            BSD3_LICENSE,
        ),
        ThirdPartyItem(
            "PyInstaller（打包工具）",
            _resolve_version("pyinstaller", "6.22.2"),
            "GPL-2.0-or-later（bootloader 例外）",
            "© PyInstaller Development Team",
            "https://pyinstaller.org/",
            GPL2_EXCEPTION_NOTICE,
        ),
        ThirdPartyItem(
            "altgraph（PyInstaller 依赖）",
            _resolve_version("altgraph", "0.17.5"),
            "MIT",
            "© Istvan Albert 及贡献者",
            "https://altgraph.readthedocs.io/",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "pyinstaller-hooks-contrib（PyInstaller hooks 集）",
            _resolve_version("pyinstaller-hooks-contrib", "2026.6"),
            "GPL-2.0-or-later（运行期 hook 部分为 Apache-2.0）",
            "© PyInstaller Development Team 及贡献者",
            "https://github.com/pyinstaller/pyinstaller-hooks-contrib",
            GPL2_NOTICE,
        ),
        ThirdPartyItem(
            "setuptools（PyInstaller 依赖）",
            _resolve_version("setuptools", "84.0.0"),
            "MIT",
            "© Python Packaging Authority（PyPA）",
            "https://github.com/pypa/setuptools",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "macholib（PyInstaller 依赖，仅 macOS 打包）",
            _resolve_version("macholib", "1.16.4"),
            "MIT（Expat）",
            "© 2006-2009 Bob Ippolito；© 2008-2023 Ronald Oussoren",
            "https://github.com/ronaldoussoren/macholib",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "pefile（PyInstaller 依赖，仅 Windows 打包）",
            _resolve_version("pefile", "2024.8.26"),
            "MIT",
            "© 2004-2024 Ero Carrera",
            "https://github.com/erocarrera/pefile",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "pywin32-ctypes（PyInstaller 依赖，仅 Windows 打包）",
            _resolve_version("pywin32-ctypes", "0.2.3"),
            "BSD-3-Clause",
            "© 2014 Enthought, Inc.",
            "https://github.com/enthought/pywin32-ctypes",
            BSD3_LICENSE,
        ),
        # ---- Android 打包工具链（仅构建期使用，不随应用分发；所有平台一致展示）----
        ThirdPartyItem(
            "python-for-android（p4a，Android 打包工具链）",
            "由 pyside6-android-deploy 安装（仅 Android 打包用）",
            "MIT",
            "© 2010-2025 Kivy Team and other contributors",
            "https://github.com/kivy/python-for-android",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "buildozer（Android 打包驱动）",
            "1.5.0（仅 Android 打包用）",
            "MIT",
            "© 2010-2017 Kivy Team and other contributors",
            "https://github.com/kivy/buildozer",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "Cython（编译 p4a recipe）",
            "0.29.33（仅 Android 打包用）",
            "Apache-2.0",
            "© Cython contributors",
            "https://cython.org/",
            APACHE20_NOTICE,
        ),
        ThirdPartyItem(
            "Nuitka（Python → C 编译，供交叉编译）",
            "4.1.1（仅 Android 打包用）",
            "AGPL-3.0-or-later",
            "© Kay Hayen and Nuitka contributors",
            "https://nuitka.net/",
            AGPL3_NOTICE,
        ),
        ThirdPartyItem(
            "Jinja2（打包模板渲染）",
            _resolve_version("jinja2", "3.1"),
            "BSD-3-Clause",
            "© 2007 Pallets",
            "https://palletsprojects.com/p/jinja/",
            BSD3_LICENSE,
        ),
        ThirdPartyItem(
            "packaging（版本解析）",
            _resolve_version("packaging", "24.1"),
            "Apache-2.0 或 BSD-2-Clause（双许可，任选其一）",
            "© Donald Stufft and individual contributors",
            "https://github.com/pypa/packaging",
            APACHE20_NOTICE,
        ),
        ThirdPartyItem(
            "pkginfo（包元数据查询）",
            _resolve_version("pkginfo", "1.13"),
            "MIT",
            "© 2009-2024 Agendaless Consulting and Contributors",
            "https://github.com/pypa/pkginfo",
            MIT_LICENSE,
        ),
        ThirdPartyItem(
            "tqdm（进度显示）",
            _resolve_version("tqdm", "4.70"),
            "MPL-2.0 AND MIT",
            "© 2013-2026 tqdm developers",
            "https://tqdm.github.io/",
            MPL2_NOTICE,
        ),
        ThirdPartyItem(
            "Android SDK（cmdline-tools / build-tools / platform）",
            "API 35 / build-tools（仅 Android 打包用）",
            "Apache-2.0",
            "© Google LLC",
            "https://developer.android.com/studio",
            ANDROID_SDK_NOTICE,
        ),
        ThirdPartyItem(
            "Android NDK（r27c）",
            "27.2.12479018（仅 Android 打包用）",
            "Android NDK License Agreement（专有）",
            "© Google LLC",
            "https://developer.android.com/ndk",
            ANDROID_NDK_NOTICE,
        ),
        ThirdPartyItem(
            "MiSans 字体",
            "MiSans-Medium（随软件分发，未做任何修改）",
            "MiSans 字体知识产权许可协议",
            "© 小米科技有限责任公司",
            "https://hyperos.mi.com/font/",
            MISANS_LICENSE,
        ),
        ThirdPartyItem(
            "Noto Sans Symbols 2",
            "NotoSansSymbols2-Regular（随软件分发，未做任何修改）",
            "OFL-1.1",
            "© 2022 The Noto Project Authors",
            "https://github.com/notofonts/symbols",
            NOTO_SYMBOLS2_LICENSE,
        ),
    ]
