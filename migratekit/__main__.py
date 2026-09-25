"""``python3 -m migratekit`` 的命令行入口。"""

import sys

from migratekit.runner import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
