"""工作目录的字节级快照：先把内容整份读进内存，需要时再放回去。"""

import os


class Snapshot:
    """对 ``workdir`` 做一次字节级快照。

    ``exclude`` 里的目录/文件子树不参与快照（比如迁移脚本目录）。
    """

    def __init__(self, workdir, exclude=()):
        self.workdir = os.path.abspath(workdir)
        self.exclude = [os.path.abspath(item) for item in exclude]
        self.files = {}
        self.dirs = []

    # -- 内部 ---------------------------------------------------------------

    def _skipped(self, path):
        path = os.path.abspath(path)
        for item in self.exclude:
            if path == item or path.startswith(item + os.sep):
                return True
        return False

    def _walk(self):
        """自顶向下遍历，跳过 exclude 子树，产出 (相对路径, "file"|"dir")。"""
        for root, dirnames, filenames in os.walk(self.workdir, topdown=True):
            if self._skipped(root):
                dirnames[:] = []
                continue
            dirnames[:] = [
                name
                for name in dirnames
                if not self._skipped(os.path.join(root, name))
            ]
            for name in dirnames:
                yield os.path.relpath(os.path.join(root, name), self.workdir), "dir"
            for name in filenames:
                yield os.path.relpath(os.path.join(root, name), self.workdir), "file"

    # -- 对外 ---------------------------------------------------------------

    def capture(self):
        """把当前内容读进内存。"""
        self.files = {}
        self.dirs = []
        for rel, kind in self._walk():
            if kind == "dir":
                self.dirs.append(rel)
                continue
            with open(os.path.join(self.workdir, rel), "rb") as handle:
                self.files[rel] = handle.read()
        return self

    def restore(self):
        """把工作目录还原成 capture() 那一刻的样子。"""
        keep = set(self.files)
        for rel, kind in self._walk():
            if kind == "file" and rel not in keep:
                os.remove(os.path.join(self.workdir, rel))
        for rel, kind in sorted(self._walk(), key=lambda item: item[0].count(os.sep), reverse=True):
            if kind != "dir":
                continue
            try:
                os.rmdir(os.path.join(self.workdir, rel))
            except OSError:
                pass
        for rel in sorted(self.files):
            full = os.path.join(self.workdir, rel)
            parent = os.path.dirname(full)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(full, "wb") as handle:
                handle.write(self.files[rel])
        return self

    def discard(self):
        """丢掉快照，不再还原。"""
        self.files = {}
        self.dirs = []
        return self
