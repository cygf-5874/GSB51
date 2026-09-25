"""迁移引擎：扫描脚本、校编号、执行 up / down、维护 state.json。"""

import importlib.util
import os
import re
import sys

from migratekit.snapshot import Snapshot
from migratekit.state import State, sha256_file

NAME_RE = re.compile(r"^(\d{3})_([A-Za-z0-9_]+)\.py$")

USAGE = """\
用法：python3 -m migratekit [--dir <工作目录>] [--migrations <目录>] <命令> [选项]

命令：
  up   [--to N] [--dry-run]   执行待执行步骤（--to 只执行到编号 N）
  down [--to N] [--dry-run]   回退（不给 --to 时只回退一步）
  plan [--to N]               按执行顺序列出待执行步骤

选项：
  --dir <工作目录>        默认当前目录
  --migrations <目录>     默认 <工作目录>/migrations
"""


class MigrateError(Exception):
    """迁移过程中的可预期错误。"""


class VersionError(MigrateError):
    """迁移脚本的编号不合法。"""


class HistoryError(MigrateError):
    """已应用步骤的历史与迁移目录里的脚本对不上。"""


class Step:
    """一个迁移脚本。"""

    def __init__(self, version, filename, path):
        self.version = version
        self.filename = filename
        self.name = filename[:-3]
        self.path = path
        self._module = None

    @property
    def sha256(self):
        return sha256_file(self.path)

    def load(self):
        """按文件路径导入脚本模块，并确认 up / down 都在。"""
        if self._module is None:
            spec = importlib.util.spec_from_file_location(
                "migratekit_script_%03d" % self.version, self.path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for hook in ("up", "down"):
                if not callable(getattr(module, hook, None)):
                    raise VersionError("%s 里没有可调用的 %s(ctx)" % (self.filename, hook))
            self._module = module
        return self._module

    def __repr__(self):
        return "<Step %03d %s>" % (self.version, self.filename)


class Ctx:
    """传给迁移脚本的上下文，只允许访问工作目录。"""

    def __init__(self, workdir):
        self.workdir = os.path.abspath(workdir)

    def path(self, rel):
        full = os.path.abspath(os.path.join(self.workdir, rel))
        if full != self.workdir and not full.startswith(self.workdir + os.sep):
            raise MigrateError("路径越出工作目录：%s" % rel)
        return full

    def exists(self, rel):
        return os.path.exists(self.path(rel))

    def read(self, rel, default=""):
        full = self.path(rel)
        if not os.path.exists(full):
            return default
        with open(full, "r", encoding="utf-8") as handle:
            return handle.read()

    def write(self, rel, text):
        full = self.path(rel)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def append(self, rel, text):
        full = self.path(rel)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def remove(self, rel):
        full = self.path(rel)
        if os.path.exists(full):
            os.remove(full)


def discover_scripts(directory):
    """扫描迁移目录，返回 ``{文件名: Step}`` 与发现顺序一致的字典。"""
    directory = os.path.abspath(directory)
    if not os.path.isdir(directory):
        return {}
    found = {}
    for name in set(os.listdir(directory)):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        match = NAME_RE.match(name)
        if not match:
            raise VersionError("迁移脚本名必须是 NNN_name.py：%s" % name)
        found[name] = Step(int(match.group(1)), name, os.path.join(directory, name))

    ordered = sorted(found.values(), key=lambda step: step.version)
    seen = {}
    for step in ordered:
        if step.version in seen:
            raise VersionError(
                "迁移编号重复：%03d 同时出现在 %s 与 %s"
                % (step.version, seen[step.version], step.filename)
            )
        seen[step.version] = step.filename
    for index, step in enumerate(ordered):
        if step.version != index + 1:
            raise VersionError(
                "迁移编号必须从 001 起连续：%s 之前缺 %03d"
                % (step.filename, index + 1)
            )
    return found


class Runner:
    """在某个工作目录上跑迁移。"""

    def __init__(self, workdir=".", migrations_dir=None):
        self.workdir = os.path.abspath(workdir)
        self.migrations_dir = os.path.abspath(
            migrations_dir or os.path.join(self.workdir, "migrations")
        )
        self.state = State(self.workdir).load()
        self.steps = discover_scripts(self.migrations_dir)
        self.ctx = Ctx(self.workdir)

    # -- 查询 ---------------------------------------------------------------

    def ordered_steps(self):
        """全部脚本，按编号升序。"""
        return sorted(self.steps.values(), key=lambda step: step.version)

    def pending_steps(self, target=None):
        """待执行步骤，按编号升序。"""
        steps = self.ordered_steps()
        if target is not None:
            steps = [step for step in steps if step.version <= target]
        return steps

    def plan(self, target=None):
        """待执行步骤的脚本文件名，按执行顺序。"""
        return [step.filename for step in self.pending_steps(target)]

    # -- 执行 ---------------------------------------------------------------

    def up(self, target=None, dry_run=False):
        """执行待执行步骤，返回真正执行了的步数。"""
        steps = self.pending_steps(target)
        if dry_run:
            for step in steps:
                print(step.filename)
            return 0
        applied = 0
        for step in steps:
            self.state.applied.append(
                {
                    "version": step.version,
                    "file": step.filename,
                    "sha256": step.sha256,
                }
            )
            self.state.version = step.version
            self.state.save()
            step.load().up(self.ctx)
            applied += 1
        self.state.save()
        return applied

    def revert_entries(self, target=None):
        """要回退的步骤，按回退顺序（最后一步在前）。

        ``target`` 为 ``None`` 时只回退一步；否则回退到编号 ``target``（含 ``target`` 仍保留）。
        """
        if target is None:
            return list(self.state.applied[-1:])
        return [
            entry for entry in reversed(self.state.applied) if entry["version"] > target
        ]

    def down(self, target=None, dry_run=False):
        """回退，返回真正回退了的步数。"""
        to_revert = self.revert_entries(target)
        if dry_run:
            for entry in to_revert:
                print(entry["file"])
            return 0
        if not to_revert:
            raise MigrateError("没有可回退的迁移")
        reverted = 0
        for entry in to_revert:
            step = self.steps.get(entry["file"])
            if step is None:
                raise HistoryError("状态里的 %s 在迁移目录里找不到" % entry["file"])
            step.load().down(self.ctx)
            self.state.applied.pop()
            self.state.version = (
                self.state.applied[-1]["version"] if self.state.applied else 0
            )
            self.state.save()
            reverted += 1
        return reverted


def main(argv):
    """命令行入口。"""
    args = list(argv)
    workdir = "."
    migrations = None
    command = None
    target = None
    dry_run = False

    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-h", "--help"):
            print(USAGE.rstrip())
            return 0
        if arg in ("--dir", "--workdir"):
            index += 1
            if index >= len(args):
                print("--dir 需要参数", file=sys.stderr)
                return 2
            workdir = args[index]
        elif arg == "--migrations":
            index += 1
            if index >= len(args):
                print("--migrations 需要参数", file=sys.stderr)
                return 2
            migrations = args[index]
        elif arg in ("up", "down", "plan") and command is None:
            command = arg
        elif arg == "--to":
            index += 1
            if index >= len(args):
                print("--to 需要参数", file=sys.stderr)
                return 2
            try:
                target = int(args[index])
            except ValueError:
                print("--to 需要整数", file=sys.stderr)
                return 2
        elif arg == "--dry-run":
            dry_run = True
        else:
            print("未知参数：%s" % arg, file=sys.stderr)
            return 2
        index += 1

    if command is None:
        print(USAGE.rstrip())
        return 0

    try:
        runner = Runner(workdir, migrations)
        if command == "plan":
            for name in runner.plan(target):
                print(name)
        elif command == "up":
            runner.up(target, dry_run)
        else:
            runner.down(target, dry_run)
    except MigrateError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print("错误：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0
