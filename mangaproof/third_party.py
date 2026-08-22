"""第三方组件与许可证信息（独立的「第三方许可」页面数据）。

格式遵循业界惯例（Chromium chrome://credits、Flutter LicenseRegistry、
VS Code Third Party Notices）：组件名 + 版本 + SPDX 许可证标识 +
版权声明 + 主页 + 许可证全文（过长的 GPL/LGPL 提供摘要与官方链接）。

版本号优先从已安装包元数据（importlib.metadata）解析，缺失时回退
到随代码记录的版本。
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
            _resolve_version("PySide6", "6.11.2"),
            "LGPL-3.0-only",
            "© The Qt Company Ltd.",
            "https://www.qt.io/",
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
            "MiSans 字体",
            "MiSans-Medium（随软件分发，未做任何修改）",
            "MiSans 字体知识产权许可协议",
            "© 小米科技有限责任公司",
            "https://hyperos.mi.com/font/download",
            MISANS_LICENSE,
        ),
    ]
