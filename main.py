"""MangaProof 启动入口（需求 §56：python main.py）。

程序目录 = 本文件所在目录（config.paths.get_app_dir() 会自动判定）。
"""

import sys

from mangaproof.main import main

if __name__ == "__main__":
    sys.exit(main())
