# 第三方组件与许可

> 本文件由 [`scripts/build_third_party_doc.py`](scripts/build_third_party_doc.py) 依据
> [`mangaproof/third_party.py`](mangaproof/third_party.py) **自动生成，请勿手工编辑**——
> 需要改动请修改源文件后重新生成。
> 程序内 **关于 → 第三方许可** 展示的是同一份数据。

MangaProof 使用了下列第三方组件。每个条目给出组件名、版本、许可证标识（SPDX）、
版权声明、主页与许可证全文（过长的 GPL / LGPL 给要点摘要与官方全文链接）。

## 范围说明

- **随应用分发**（Python 运行时、psd-tools、NumPy、PySide6 / Qt、shiboken6、reportlab、
  Pillow、attrs、typing-extensions、charset-normalizer、MiSans 与 Noto 字体等）：
  这些组件的许可声明会随安装包一并提供给用户；
- **仅构建 / 打包期使用**（名称里标了「仅 … 打包用」或「… 依赖」的条目前缀，如
  PyInstaller、python-for-android、buildozer、Cython、Nuitka、Android SDK / NDK）：
  它们不随应用分发，列出是为了让构建链路的许可同样透明；
- **未列入**：只在开发 / 测试环境使用、不进入任何产物的工具（uv、pytest、性能分析工具等）；
- 部分组件自身还会引入第三方原生库或字体（例如 Qt 内的 libpng / harfbuzz / ICU、
  Pillow 内的 libjpeg-turbo / zlib、p4a 引入的 OpenSSL / libffi），其许可随该组件一并分发，
  详见各组件官方许可页；
- 版本号在本机装有该包时取自包元数据，否则回退到仓库记录的版本。

## 组件总览

| # | 组件 | 版本 | 许可证（SPDX） | 主页 |
| --- | --- | --- | --- | --- |
| 1 | Python（运行时） | 3.12 | PSF-2.0 | <https://www.python.org/> |
| 2 | psd-tools（PSD 解析） | 1.18.0 | MIT | <https://github.com/psd-tools/psd-tools> |
| 3 | NumPy（图像分析） | 2.5.2 | BSD-3-Clause | <https://numpy.org/> |
| 4 | PySide6 / Qt（GUI 框架） | 6.11.2（含部署工具 pyside6-deploy / pyside6-android-deploy） | LGPL-3.0-only | <https://www.qt.io/> |
| 5 | shiboken6（PySide6 绑定运行时） | 6.11.2 | LGPL-3.0-only（亦可选 GPL-2.0 / GPL-3.0 或商业授权） | <https://pyside.org/> |
| 6 | reportlab（PDF 生成） | 5.0.1 | BSD-3-Clause | <https://www.reportlab.com/> |
| 7 | Pillow（图像处理） | 12.3.0 | HPND（MIT-CMU） | <https://python-pillow.org/> |
| 8 | attrs（psd-tools 依赖） | 26.1.0 | MIT | <https://www.attrs.org/> |
| 9 | typing-extensions（psd-tools 依赖） | 4.16.0 | PSF-2.0 | <https://github.com/python/typing_extensions> |
| 10 | charset-normalizer（reportlab 依赖） | 3.5.1 | MIT | <https://github.com/jawah/charset_normalizer> |
| 11 | httpx（自更新 HTTP 客户端） | 0.28.1 | BSD-3-Clause | <https://www.python-httpx.org/> |
| 12 | httpcore（httpx 底层传输） | 1.0.9 | BSD-3-Clause | <https://github.com/encode/httpcore> |
| 13 | h11（HTTP/1.1 协议实现） | 0.16.0 | MIT | <https://github.com/python-hyper/h11> |
| 14 | anyio（异步 I/O 抽象） | 4.15.1 | MIT | <https://github.com/agronholm/anyio> |
| 15 | sniffio（异步库探测） | 1.3.1 | MIT OR Apache-2.0 | <https://github.com/python-trio/sniffio> |
| 16 | certifi（CA 证书包） | 2026.7.22 | MPL-2.0 | <https://github.com/certifi/python-certifi> |
| 17 | idna（国际化域名） | 3.20 | BSD-3-Clause | <https://github.com/kjd/idna> |
| 18 | socksio（SOCKS 代理支持，httpx[socks]） | 1.0.0 | MIT | <https://github.com/sethmlarson/socksio> |
| 19 | keyring（系统凭据库访问） | 25.7.0 | MIT | <https://github.com/jaraco/keyring> |
| 20 | jaraco.classes（keyring 依赖） | 3.4.0 | MIT | <https://github.com/jaraco/jaraco.classes> |
| 21 | jaraco.context（keyring 依赖） | 6.1.2 | MIT | <https://github.com/jaraco/jaraco.context> |
| 22 | jaraco.functools（keyring 依赖） | 4.6.0 | MIT | <https://github.com/jaraco/jaraco.functools> |
| 23 | more-itertools（jaraco.functools 依赖） | 11.1.0 | MIT | <https://github.com/more-itertools/more-itertools> |
| 24 | SecretStorage（keyring 的 Linux 后端） | 3.5.0 | BSD-3-Clause | <https://github.com/mitya57/secretstorage> |
| 25 | jeepney（SecretStorage 的 D-Bus 实现） | 0.9.0 | MIT | <https://gitlab.com/takluyver/jeepney> |
| 26 | cryptography（SecretStorage 依赖，仅 Linux 桌面） | 50.0.1 | Apache-2.0 OR BSD-3-Clause | <https://github.com/pyca/cryptography> |
| 27 | cffi（cryptography 依赖） | 2.1.1 | MIT | <https://cffi.readthedocs.io/> |
| 28 | pycparser（cffi 依赖） | 3.0 | BSD-3-Clause | <https://github.com/eliben/pycparser> |
| 29 | PyInstaller（打包工具） | 6.22.2 | GPL-2.0-or-later（bootloader 例外） | <https://pyinstaller.org/> |
| 30 | altgraph（PyInstaller 依赖） | 0.17.5 | MIT | <https://altgraph.readthedocs.io/> |
| 31 | pyinstaller-hooks-contrib（PyInstaller hooks 集） | 2026.6 | GPL-2.0-or-later（运行期 hook 部分为 Apache-2.0） | <https://github.com/pyinstaller/pyinstaller-hooks-contrib> |
| 32 | setuptools（PyInstaller 依赖） | 84.0.0 | MIT | <https://github.com/pypa/setuptools> |
| 33 | macholib（PyInstaller 依赖，仅 macOS 打包） | 1.16.4 | MIT（Expat） | <https://github.com/ronaldoussoren/macholib> |
| 34 | pefile（PyInstaller 依赖，仅 Windows 打包） | 2024.8.26 | MIT | <https://github.com/erocarrera/pefile> |
| 35 | pywin32-ctypes（PyInstaller 依赖，仅 Windows 打包） | 0.2.3 | BSD-3-Clause | <https://github.com/enthought/pywin32-ctypes> |
| 36 | python-for-android（p4a，Android 打包工具链） | 由 pyside6-android-deploy 安装（仅 Android 打包用） | MIT | <https://github.com/kivy/python-for-android> |
| 37 | buildozer（Android 打包驱动） | 1.5.0（仅 Android 打包用） | MIT | <https://github.com/kivy/buildozer> |
| 38 | Cython（编译 p4a recipe） | 0.29.33（仅 Android 打包用） | Apache-2.0 | <https://cython.org/> |
| 39 | Nuitka（Python → C 编译，供交叉编译） | 4.1.1（仅 Android 打包用） | AGPL-3.0-or-later | <https://nuitka.net/> |
| 40 | Jinja2（打包模板渲染） | 3.1 | BSD-3-Clause | <https://palletsprojects.com/p/jinja/> |
| 41 | packaging（版本解析） | 26.3 | Apache-2.0 或 BSD-2-Clause（双许可，任选其一） | <https://github.com/pypa/packaging> |
| 42 | pkginfo（包元数据查询） | 1.13 | MIT | <https://github.com/pypa/pkginfo> |
| 43 | tqdm（进度显示） | 4.70 | MPL-2.0 AND MIT | <https://tqdm.github.io/> |
| 44 | Android SDK（cmdline-tools / build-tools / platform） | API 35 / build-tools（仅 Android 打包用） | Apache-2.0 | <https://developer.android.com/studio> |
| 45 | Android NDK（r27c） | 27.2.12479018（仅 Android 打包用） | Android NDK License Agreement（专有） | <https://developer.android.com/ndk> |
| 46 | MiSans 字体 | MiSans-Medium（随软件分发，未做任何修改） | MiSans 字体知识产权许可协议 | <https://hyperos.mi.com/font/> |
| 47 | Noto Sans Symbols 2 | NotoSansSymbols2-Regular（随软件分发，未做任何修改） | OFL-1.1 | <https://github.com/notofonts/symbols> |

## 许可全文与声明

### 1. Python（运行时）

- **版本**：3.12
- **许可证（SPDX）**：PSF-2.0
- **版权**：© 2001-2026 Python Software Foundation
- **主页**：<https://www.python.org/>

```text
PSF LICENSE AGREEMENT FOR PYTHON 3.x

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

完整文本：https://docs.python.org/3/license.html
```

### 2. psd-tools（PSD 解析）

- **版本**：1.18.0
- **许可证（SPDX）**：MIT
- **版权**：© psd-tools contributors
- **主页**：<https://github.com/psd-tools/psd-tools>

```text
MIT License

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
THE SOFTWARE.
```

### 3. NumPy（图像分析）

- **版本**：2.5.2
- **许可证（SPDX）**：BSD-3-Clause
- **版权**：© 2005-2026 NumPy Developers
- **主页**：<https://numpy.org/>

```text
BSD 3-Clause License

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
POSSIBILITY OF SUCH DAMAGE.
```

### 4. PySide6 / Qt（GUI 框架）

- **版本**：6.11.2（含部署工具 pyside6-deploy / pyside6-android-deploy）
- **许可证（SPDX）**：LGPL-3.0-only
- **版权**：© The Qt Company Ltd.
- **主页**：<https://www.qt.io/>

```text
GNU LESSER GENERAL PUBLIC LICENSE, Version 3（LGPL-3.0）

PySide6 / Qt for Python 采用 LGPL-3.0-only 授权（亦可选择 GPL-2.0-only、
GPL-3.0-only 或商业授权）。

要点（非法律建议）：
- 允许以动态链接方式在本软件中使用 Qt 库，无需公开本软件源代码；
- 若对 Qt 库本身做出修改，修改部分需以 LGPLv3 提供；
- 分发本软件时需保留 Qt 的版权与许可证声明。

完整许可证文本（官方链接）：
https://www.gnu.org/licenses/lgpl-3.0.html
https://doc.qt.io/qt-6/lgpl.html
```

### 5. shiboken6（PySide6 绑定运行时）

- **版本**：6.11.2
- **许可证（SPDX）**：LGPL-3.0-only（亦可选 GPL-2.0 / GPL-3.0 或商业授权）
- **版权**：© The Qt Company Ltd.
- **主页**：<https://pyside.org/>

```text
GNU LESSER GENERAL PUBLIC LICENSE, Version 3（LGPL-3.0）

PySide6 / Qt for Python 采用 LGPL-3.0-only 授权（亦可选择 GPL-2.0-only、
GPL-3.0-only 或商业授权）。

要点（非法律建议）：
- 允许以动态链接方式在本软件中使用 Qt 库，无需公开本软件源代码；
- 若对 Qt 库本身做出修改，修改部分需以 LGPLv3 提供；
- 分发本软件时需保留 Qt 的版权与许可证声明。

完整许可证文本（官方链接）：
https://www.gnu.org/licenses/lgpl-3.0.html
https://doc.qt.io/qt-6/lgpl.html
```

### 6. reportlab（PDF 生成）

- **版本**：5.0.1
- **许可证（SPDX）**：BSD-3-Clause
- **版权**：© 2000-2026 ReportLab Inc.
- **主页**：<https://www.reportlab.com/>

```text
BSD 3-Clause License

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
POSSIBILITY OF SUCH DAMAGE.
```

### 7. Pillow（图像处理）

- **版本**：12.3.0
- **许可证（SPDX）**：HPND（MIT-CMU）
- **版权**：© 1995-2011 Secret Labs AB / Fredrik Lundh；Pillow 贡献者
- **主页**：<https://python-pillow.org/>

```text
Historical Permission Notice and Disclaimer (MIT-CMU)

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
PERFORMANCE OF THIS SOFTWARE.
```

### 8. attrs（psd-tools 依赖）

- **版本**：26.1.0
- **许可证（SPDX）**：MIT
- **版权**：© 2015-2026 Hynek Schlawack
- **主页**：<https://www.attrs.org/>

```text
MIT License

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
THE SOFTWARE.
```

### 9. typing-extensions（psd-tools 依赖）

- **版本**：4.16.0
- **许可证（SPDX）**：PSF-2.0
- **版权**：© Python Software Foundation 及 typing_extensions 贡献者
- **主页**：<https://github.com/python/typing_extensions>

```text
PSF LICENSE AGREEMENT FOR PYTHON 3.x

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

完整文本：https://docs.python.org/3/license.html
```

### 10. charset-normalizer（reportlab 依赖）

- **版本**：3.5.1
- **许可证（SPDX）**：MIT
- **版权**：© 2019-2026 Ahmed TAHRI（jawah）
- **主页**：<https://github.com/jawah/charset_normalizer>

```text
MIT License

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
THE SOFTWARE.
```

### 11. httpx（自更新 HTTP 客户端）

- **版本**：0.28.1
- **许可证（SPDX）**：BSD-3-Clause
- **版权**：© 2019 Encode OSS Ltd.
- **主页**：<https://www.python-httpx.org/>

```text
BSD 3-Clause License

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
POSSIBILITY OF SUCH DAMAGE.
```

### 12. httpcore（httpx 底层传输）

- **版本**：1.0.9
- **许可证（SPDX）**：BSD-3-Clause
- **版权**：© 2020 Encode OSS Ltd.
- **主页**：<https://github.com/encode/httpcore>

```text
BSD 3-Clause License

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
POSSIBILITY OF SUCH DAMAGE.
```

### 13. h11（HTTP/1.1 协议实现）

- **版本**：0.16.0
- **许可证（SPDX）**：MIT
- **版权**：© 2016 Nathaniel J. Smith 及贡献者
- **主页**：<https://github.com/python-hyper/h11>

```text
MIT License

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
THE SOFTWARE.
```

### 14. anyio（异步 I/O 抽象）

- **版本**：4.15.1
- **许可证（SPDX）**：MIT
- **版权**：© 2018 Alex Grönholm
- **主页**：<https://github.com/agronholm/anyio>

```text
MIT License

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
THE SOFTWARE.
```

### 15. sniffio（异步库探测）

- **版本**：1.3.1
- **许可证（SPDX）**：MIT OR Apache-2.0
- **版权**：© 2018 Alex Grönholm 及贡献者
- **主页**：<https://github.com/python-trio/sniffio>

```text
MIT License

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
THE SOFTWARE.
```

### 16. certifi（CA 证书包）

- **版本**：2026.7.22
- **许可证（SPDX）**：MPL-2.0
- **版权**：© Kenneth Reitz 及 certifi 贡献者
- **主页**：<https://github.com/certifi/python-certifi>

```text
Mozilla Public License 2.0（MPL-2.0）

- 文件级 copyleft：被修改过的 MPL 源文件需继续以 MPL 提供，可与其它许可的
  代码组合、链接；
- 需保留版权与许可声明；
- 软件按"现状"提供，不附带任何担保。

完整许可证文本（官方链接）：
https://www.mozilla.org/MPL/2.0/
```

### 17. idna（国际化域名）

- **版本**：3.20
- **许可证（SPDX）**：BSD-3-Clause
- **版权**：© 2013-2026 Kim Davies 及贡献者
- **主页**：<https://github.com/kjd/idna>

```text
BSD 3-Clause License

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
POSSIBILITY OF SUCH DAMAGE.
```

### 18. socksio（SOCKS 代理支持，httpx[socks]）

- **版本**：1.0.0
- **许可证（SPDX）**：MIT
- **版权**：© 2020 Seth Michael Larson
- **主页**：<https://github.com/sethmlarson/socksio>

```text
MIT License

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
THE SOFTWARE.
```

### 19. keyring（系统凭据库访问）

- **版本**：25.7.0
- **许可证（SPDX）**：MIT
- **版权**：© 2011-2026 Jason R. Coombs 及贡献者
- **主页**：<https://github.com/jaraco/keyring>

```text
MIT License

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
THE SOFTWARE.
```

### 20. jaraco.classes（keyring 依赖）

- **版本**：3.4.0
- **许可证（SPDX）**：MIT
- **版权**：© Jason R. Coombs
- **主页**：<https://github.com/jaraco/jaraco.classes>

```text
MIT License

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
THE SOFTWARE.
```

### 21. jaraco.context（keyring 依赖）

- **版本**：6.1.2
- **许可证（SPDX）**：MIT
- **版权**：© Jason R. Coombs
- **主页**：<https://github.com/jaraco/jaraco.context>

```text
MIT License

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
THE SOFTWARE.
```

### 22. jaraco.functools（keyring 依赖）

- **版本**：4.6.0
- **许可证（SPDX）**：MIT
- **版权**：© Jason R. Coombs
- **主页**：<https://github.com/jaraco/jaraco.functools>

```text
MIT License

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
THE SOFTWARE.
```

### 23. more-itertools（jaraco.functools 依赖）

- **版本**：11.1.0
- **许可证（SPDX）**：MIT
- **版权**：© 2012 Erik Rose
- **主页**：<https://github.com/more-itertools/more-itertools>

```text
MIT License

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
THE SOFTWARE.
```

### 24. SecretStorage（keyring 的 Linux 后端）

- **版本**：3.5.0
- **许可证（SPDX）**：BSD-3-Clause
- **版权**：© 2012-2026 Dmitry Shachnev
- **主页**：<https://github.com/mitya57/secretstorage>

```text
BSD 3-Clause License

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
POSSIBILITY OF SUCH DAMAGE.
```

### 25. jeepney（SecretStorage 的 D-Bus 实现）

- **版本**：0.9.0
- **许可证（SPDX）**：MIT
- **版权**：© 2017-2026 Thomas Kluyver
- **主页**：<https://gitlab.com/takluyver/jeepney>

```text
MIT License

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
THE SOFTWARE.
```

### 26. cryptography（SecretStorage 依赖，仅 Linux 桌面）

- **版本**：50.0.1
- **许可证（SPDX）**：Apache-2.0 OR BSD-3-Clause
- **版权**：© The Python Cryptographic Authority 及贡献者
- **主页**：<https://github.com/pyca/cryptography>

```text
Apache License 2.0（Apache-2.0）

- 允许商业使用、修改与再分发；需保留版权与许可声明，随附 NOTICE 文件（若有）；
- 分发修改版时需说明改动；
- 含专利授权；若对贡献者发起专利诉讼，该授权终止；
- 软件按"现状"提供，不附带任何明示或默示担保。

完整许可证文本（官方链接）：
https://www.apache.org/licenses/LICENSE-2.0
```

### 27. cffi（cryptography 依赖）

- **版本**：2.1.1
- **许可证（SPDX）**：MIT
- **版权**：© 2013-2026 Armin Rigo 及贡献者
- **主页**：<https://cffi.readthedocs.io/>

```text
MIT License

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
THE SOFTWARE.
```

### 28. pycparser（cffi 依赖）

- **版本**：3.0
- **许可证（SPDX）**：BSD-3-Clause
- **版权**：© 2008-2026 Eli Bendersky
- **主页**：<https://github.com/eliben/pycparser>

```text
BSD 3-Clause License

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
POSSIBILITY OF SUCH DAMAGE.
```

### 29. PyInstaller（打包工具）

- **版本**：6.22.2
- **许可证（SPDX）**：GPL-2.0-or-later（bootloader 例外）
- **版权**：© PyInstaller Development Team
- **主页**：<https://pyinstaller.org/>

```text
GNU General Public License v2 或更高版本（含 bootloader 例外）

PyInstaller 以 GPL-2.0-or-later 授权，并附带特殊例外条款：

"We hereby grant you an exclusive permission that allows you to use the
PyInstaller bootloader, in executable form, to build and distribute
non-free programs (including commercial ones)."

即：允许使用 PyInstaller 打包并分发非自由（含商业）软件，本软件的
分发不受 GPL 传染。

完整许可证文本（官方链接）：
https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt
```

### 30. altgraph（PyInstaller 依赖）

- **版本**：0.17.5
- **许可证（SPDX）**：MIT
- **版权**：© Istvan Albert 及贡献者
- **主页**：<https://altgraph.readthedocs.io/>

```text
MIT License

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
THE SOFTWARE.
```

### 31. pyinstaller-hooks-contrib（PyInstaller hooks 集）

- **版本**：2026.6
- **许可证（SPDX）**：GPL-2.0-or-later（运行期 hook 部分为 Apache-2.0）
- **版权**：© PyInstaller Development Team 及贡献者
- **主页**：<https://github.com/pyinstaller/pyinstaller-hooks-contrib>

```text
GNU General Public License v2 或更高版本（GPL-2.0-or-later）

- PyInstaller 社区 hooks 集（pyinstaller-hooks-contrib）以 GPL-2.0-or-later
  授权；其中**随可执行文件分发**的运行期 hook 部分单独采用 Apache-2.0；
- 本项目仅在打包期使用它生成可执行文件，不修改、也不再分发其本体。

完整许可证文本（官方链接）：
https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/develop/LICENSE
```

### 32. setuptools（PyInstaller 依赖）

- **版本**：84.0.0
- **许可证（SPDX）**：MIT
- **版权**：© Python Packaging Authority（PyPA）
- **主页**：<https://github.com/pypa/setuptools>

```text
MIT License

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
THE SOFTWARE.
```

### 33. macholib（PyInstaller 依赖，仅 macOS 打包）

- **版本**：1.16.4
- **许可证（SPDX）**：MIT（Expat）
- **版权**：© 2006-2009 Bob Ippolito；© 2008-2023 Ronald Oussoren
- **主页**：<https://github.com/ronaldoussoren/macholib>

```text
MIT License

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
THE SOFTWARE.
```

### 34. pefile（PyInstaller 依赖，仅 Windows 打包）

- **版本**：2024.8.26
- **许可证（SPDX）**：MIT
- **版权**：© 2004-2024 Ero Carrera
- **主页**：<https://github.com/erocarrera/pefile>

```text
MIT License

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
THE SOFTWARE.
```

### 35. pywin32-ctypes（PyInstaller 依赖，仅 Windows 打包）

- **版本**：0.2.3
- **许可证（SPDX）**：BSD-3-Clause
- **版权**：© 2014 Enthought, Inc.
- **主页**：<https://github.com/enthought/pywin32-ctypes>

```text
BSD 3-Clause License

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
POSSIBILITY OF SUCH DAMAGE.
```

### 36. python-for-android（p4a，Android 打包工具链）

- **版本**：由 pyside6-android-deploy 安装（仅 Android 打包用）
- **许可证（SPDX）**：MIT
- **版权**：© 2010-2025 Kivy Team and other contributors
- **主页**：<https://github.com/kivy/python-for-android>

```text
MIT License

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
THE SOFTWARE.
```

### 37. buildozer（Android 打包驱动）

- **版本**：1.5.0（仅 Android 打包用）
- **许可证（SPDX）**：MIT
- **版权**：© 2010-2017 Kivy Team and other contributors
- **主页**：<https://github.com/kivy/buildozer>

```text
MIT License

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
THE SOFTWARE.
```

### 38. Cython（编译 p4a recipe）

- **版本**：0.29.33（仅 Android 打包用）
- **许可证（SPDX）**：Apache-2.0
- **版权**：© Cython contributors
- **主页**：<https://cython.org/>

```text
Apache License 2.0（Apache-2.0）

- 允许商业使用、修改与再分发；需保留版权与许可声明，随附 NOTICE 文件（若有）；
- 分发修改版时需说明改动；
- 含专利授权；若对贡献者发起专利诉讼，该授权终止；
- 软件按"现状"提供，不附带任何明示或默示担保。

完整许可证文本（官方链接）：
https://www.apache.org/licenses/LICENSE-2.0
```

### 39. Nuitka（Python → C 编译，供交叉编译）

- **版本**：4.1.1（仅 Android 打包用）
- **许可证（SPDX）**：AGPL-3.0-or-later
- **版权**：© Kay Hayen and Nuitka contributors
- **主页**：<https://nuitka.net/>

```text
GNU Affero General Public License v3（AGPL-3.0-or-later）

- 本项目**仅把 Nuitka 当作构建期工具**：把 Python 源码编译成 C 再交叉编译成
  Android 原生库；不修改、不再分发 Nuitka 本体，最终产物中也不包含 Nuitka；
- 若你打算再分发 Nuitka 本体或其修改版，需自行遵守 AGPL 的源码提供义务。

完整许可证文本（官方链接）：
https://www.gnu.org/licenses/agpl-3.0.txt
```

### 40. Jinja2（打包模板渲染）

- **版本**：3.1
- **许可证（SPDX）**：BSD-3-Clause
- **版权**：© 2007 Pallets
- **主页**：<https://palletsprojects.com/p/jinja/>

```text
BSD 3-Clause License

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
POSSIBILITY OF SUCH DAMAGE.
```

### 41. packaging（版本解析）

- **版本**：26.3
- **许可证（SPDX）**：Apache-2.0 或 BSD-2-Clause（双许可，任选其一）
- **版权**：© Donald Stufft and individual contributors
- **主页**：<https://github.com/pypa/packaging>

```text
Apache License 2.0（Apache-2.0）

- 允许商业使用、修改与再分发；需保留版权与许可声明，随附 NOTICE 文件（若有）；
- 分发修改版时需说明改动；
- 含专利授权；若对贡献者发起专利诉讼，该授权终止；
- 软件按"现状"提供，不附带任何明示或默示担保。

完整许可证文本（官方链接）：
https://www.apache.org/licenses/LICENSE-2.0
```

### 42. pkginfo（包元数据查询）

- **版本**：1.13
- **许可证（SPDX）**：MIT
- **版权**：© 2009-2024 Agendaless Consulting and Contributors
- **主页**：<https://github.com/pypa/pkginfo>

```text
MIT License

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
THE SOFTWARE.
```

### 43. tqdm（进度显示）

- **版本**：4.70
- **许可证（SPDX）**：MPL-2.0 AND MIT
- **版权**：© 2013-2026 tqdm developers
- **主页**：<https://tqdm.github.io/>

```text
Mozilla Public License 2.0（MPL-2.0）

- 文件级 copyleft：被修改过的 MPL 源文件需继续以 MPL 提供，可与其它许可的
  代码组合、链接；
- 需保留版权与许可声明；
- 软件按"现状"提供，不附带任何担保。

完整许可证文本（官方链接）：
https://www.mozilla.org/MPL/2.0/
```

### 44. Android SDK（cmdline-tools / build-tools / platform）

- **版本**：API 35 / build-tools（仅 Android 打包用）
- **许可证（SPDX）**：Apache-2.0
- **版权**：© Google LLC
- **主页**：<https://developer.android.com/studio>

```text
Android SDK（cmdline-tools / build-tools / platform）

- Android SDK 以 Apache License 2.0 授权（© Google LLC）；
- 本项目仅用它**构建** Android 安装包，不随应用分发。

官方链接：
https://developer.android.com/studio/terms
https://www.apache.org/licenses/LICENSE-2.0
```

### 45. Android NDK（r27c）

- **版本**：27.2.12479018（仅 Android 打包用）
- **许可证（SPDX）**：Android NDK License Agreement（专有）
- **版权**：© Google LLC
- **主页**：<https://developer.android.com/ndk>

```text
Android NDK（Native Development Kit，r27c）

- NDK 适用《Android NDK License Agreement》（© Google LLC，专有许可）；
  以其编译出的产物不受该协议约束；
- 本项目仅用它**构建** Android 安装包（交叉编译 Python 依赖闭包），
  不随应用分发。

官方链接：
https://developer.android.com/ndk/downloads
https://developer.android.com/studio/terms
```

### 46. MiSans 字体

- **版本**：MiSans-Medium（随软件分发，未做任何修改）
- **许可证（SPDX）**：MiSans 字体知识产权许可协议
- **版权**：© 小米科技有限责任公司
- **主页**：<https://hyperos.mi.com/font/>

```text
MiSans 字体知识产权许可协议

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

下载地址：https://hyperos.mi.com/font/download
```

### 47. Noto Sans Symbols 2

- **版本**：NotoSansSymbols2-Regular（随软件分发，未做任何修改）
- **许可证（SPDX）**：OFL-1.1
- **版权**：© 2022 The Noto Project Authors
- **主页**：<https://github.com/notofonts/symbols>

```text
Copyright 2022 The Noto Project Authors (https://github.com/notofonts/symbols)

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
OTHER DEALINGS IN THE FONT SOFTWARE.
```
