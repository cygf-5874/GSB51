"""状态文件：把「已经应用过哪些迁移步骤」记在工作目录的 state.json 里。"""

import hashlib
import json
import os

STATE_NAME = "state.json"


def sha256_file(path):
    """算一个文件的 sha256（64 位小写 hex）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class State:
    """state.json 的读写。

    结构::

        {"version": 2,
         "applied": [{"version": 1, "file": "001_init.py", "sha256": "..."}]}
    """

    def __init__(self, workdir):
        self.workdir = os.path.abspath(workdir)
        self.path = os.path.join(self.workdir, STATE_NAME)
        self.version = 0
        self.applied = []

    def load(self):
        self.version = 0
        self.applied = []
        if not os.path.exists(self.path):
            return self
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.version = int(data.get("version", 0))
        self.applied = list(data.get("applied", []))
        return self

    def save(self):
        data = {"version": self.version, "applied": self.applied}
        os.makedirs(self.workdir, exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return self

    def remove(self):
        if os.path.exists(self.path):
            os.remove(self.path)
        return self
