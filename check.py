#!/usr/bin/env python3
"""固定验收入口：migratekit 的 10 个场景。**别改这个文件。**

用法::

    python3 check.py                  # 全部场景，全过才退 0
    python3 check.py -list            # 列出全部场景
    python3 check.py --only order     # 只跑一组（order / idempotent / atomic / guard）
    python3 check.py --only order,guard

四组场景（README「对外契约」10 条）：

- ``[order]`` 3 个 —— 契约 1、4：编号升序执行、编号重复报错、编号跳号报错。
- ``[idempotent]`` 2 个 —— 契约 2、3、8：重复 up 无副作用、重复 down 无副作用（达到目标即空操作）。
- ``[atomic]`` 3 个 —— 契约 5：某一步失败后，数据回到该步之前、状态回到该步之前、改好后再跑能成功。
- ``[guard]`` 2 个 —— 契约 6、7、9：历史脚本被改写后拒绝继续执行、``plan`` 确定且 ``--dry-run`` 无副作用。

每个场景都在 ``tempfile.mkdtemp()`` 造的独立沙箱里，用一个独立子进程跑引擎，带 30 秒看门狗。
失败不早退：一次把问题全暴露出来。

只依赖 Python 3 标准库；判据与墙钟、随机源、机器速度、``dict`` / ``set`` 迭代顺序都无关。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))

WATCHDOG_SECONDS = 30.0
BYTECODE_DIR = "__pycache__"

SCENARIOS = []


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------


class Failure(AssertionError):
    def __init__(self, message, expected="-", actual="-"):
        self.message = message
        self.expected = expected
        self.actual = actual
        super().__init__(message)


def expect(cond, message, expected=True, actual=False):
    if not cond:
        raise Failure(message, expected=expected, actual=actual)


def short(value, limit=240):
    text = repr(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def expect_eq(got, want, what):
    if got == want:
        return
    if isinstance(got, list) and isinstance(want, list) and len(got) == len(want):
        for position, (left, right) in enumerate(zip(got, want)):
            if left != right:
                what = "%s（首个不同位置 #%d：%s vs %s）" % (what, position, left, right)
                break
    raise Failure(what, expected=short(want), actual=short(got))


def expect_code(code, want, what, err=""):
    if code == want:
        return
    tail = ("；stderr=%s" % short(err.strip().splitlines()[-1])) if err.strip() else ""
    raise Failure(what + tail, expected="退出码 %s" % want, actual="退出码 %s" % code)


def expect_nonzero(code, what, err=""):
    if code != 0:
        return
    raise Failure(what, expected="非零退出码", actual="退出码 0")


def scenario(group, name, why):
    def deco(fn):
        SCENARIOS.append((group, name, why, fn))
        return fn

    return deco


# ---------------------------------------------------------------------------
# 沙箱与工具
# ---------------------------------------------------------------------------


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def body(lines):
    if not lines:
        return "    pass"
    return "\n".join("    " + line for line in lines)


def manifest(root):
    """目录内容的 {相对路径: sha256}，用来做逐字节比对。"""
    result = {}
    for base, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            result[rel] = sha256_file(full)
    return result


def tree_paths(root):
    """文件相对路径集合（忽略字节码缓存）。"""
    found = set()
    for base, _dirnames, filenames in os.walk(root):
        for name in filenames:
            rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
            if BYTECODE_DIR in rel.split("/"):
                continue
            found.add(rel)
    return found


class Sandbox:
    """一个独立沙箱：``app/``（引擎包，跑子进程时的工作目录）、``migs/``、``work/``。"""

    def __init__(self):
        self.home = tempfile.mkdtemp(prefix="gsb51-check-")
        self.app = os.path.join(self.home, "app")
        self.migs = os.path.join(self.home, "migs")
        self.work = os.path.join(self.home, "work")
        for path in (self.app, self.migs, self.work):
            os.makedirs(path)
        shutil.copytree(
            os.path.join(HERE, "migratekit"),
            os.path.join(self.app, "migratekit"),
            ignore=shutil.ignore_patterns(BYTECODE_DIR, "*.pyc"),
        )
        self.names = []

    def cleanup(self):
        shutil.rmtree(self.home, ignore_errors=True)

    # -- 造迁移 -------------------------------------------------------------

    def migration(self, filename, up_lines, down_lines):
        source = '"""%s"""\n\n\ndef up(ctx):\n%s\n\n\ndef down(ctx):\n%s\n' % (
            filename,
            body(up_lines),
            body(down_lines),
        )
        with open(os.path.join(self.migs, filename), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(source)
        if filename not in self.names:
            self.names.append(filename)
        return filename

    def edit_migration(self, filename, extra_line):
        path = os.path.join(self.migs, filename)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(extra_line)
        return path

    # -- 行动 ---------------------------------------------------------------

    def cli(self, args, extra_env=None, timeout=WATCHDOG_SECONDS):
        return run_cli(args, self.app, self.work, self.migs, extra_env=extra_env, timeout=timeout)

    def read(self, rel):
        with open(os.path.join(self.work, rel), "r", encoding="utf-8") as handle:
            return handle.read()

    def exists(self, rel):
        return os.path.exists(os.path.join(self.work, rel))

    def state(self):
        path = os.path.join(self.work, "state.json")
        expect(os.path.exists(path), "状态文件应当存在", expected="state.json", actual="缺失")
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def work_manifest(self):
        return manifest(self.work)


def run_cli(args, cwd, work, migs, extra_env=None, timeout=WATCHDOG_SECONDS):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)
    command = [sys.executable, "-m", "migratekit", "--dir", work, "--migrations", migs] + list(args)
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise Failure(
            "子进程超时（看门狗 %ds）" % int(timeout),
            expected="%ds 内结束" % int(timeout),
            actual="超时被看门狗杀掉",
        )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def normal_migration(sandbox, number, slug, token):
    """一步「正常」的迁移：up 往 data/log.txt 追加一行并留下标记文件，down 反过来。"""
    sandbox.migration(
        "%03d_%s.py" % (number, slug),
        [
            'ctx.append("data/log.txt", "%s\\n")' % token,
            'ctx.write("data/step%03d.txt", "step %s\\n")' % (number, token),
        ],
        [
            'text = ctx.read("data/log.txt", "")',
            "lines = text.splitlines(True)",
            "for index in range(len(lines) - 1, -1, -1):",
            '    if lines[index] == "%s\\n":' % token,
            "        del lines[index]",
            "        break",
            'ctx.write("data/log.txt", "".join(lines))',
            'ctx.remove("data/step%03d.txt")' % number,
        ],
    )
    return sandbox.names[-1]


# ---------------------------------------------------------------------------
# [order] 契约 1、4
# ---------------------------------------------------------------------------


@scenario("order", "ascending", "按编号升序执行，每步各自提交；工作目录之外不产生文件")
def s_order_ascending():
    sandbox = Sandbox()
    try:
        for number, token in ((1, "one"), (2, "two"), (3, "three")):
            normal_migration(sandbox, number, "step_" + token, token)

        before = tree_paths(sandbox.home)
        code, _out, err = sandbox.cli(["up"])
        expect_code(code, 0, "up 全部步骤", err)

        expect_eq(sandbox.read("data/log.txt"), "one\ntwo\nthree\n", "执行顺序体现在数据上")
        state = sandbox.state()
        expect_eq(state["version"], 3, "状态里的当前版本")
        expect_eq(
            [entry["version"] for entry in state["applied"]],
            [1, 2, 3],
            "状态里每一步各自提交",
        )
        expect_eq(
            [entry["file"] for entry in state["applied"]],
            ["001_step_one.py", "002_step_two.py", "003_step_three.py"],
            "状态里记录的文件名",
        )

        appear = sorted(tree_paths(sandbox.home) - before)
        outside = [
            rel for rel in appear if not rel.startswith("work/")
        ]
        expect_eq(outside, [], "工作目录之外新增的文件（契约 10）")
    finally:
        sandbox.cleanup()


@scenario("order", "duplicate-number", "编号重复时必须报错，且不执行任何步骤、不写任何文件")
def s_order_duplicate():
    sandbox = Sandbox()
    try:
        normal_migration(sandbox, 1, "one", "one")
        sandbox.migration("002_other.py", ["pass"], ["pass"])
        normal_migration(sandbox, 2, "two", "two")
        normal_migration(sandbox, 3, "three", "three")

        code, _out, err = sandbox.cli(["up"])
        expect_nonzero(code, "编号重复时的 up", err)
        expect_eq(os.listdir(sandbox.work), [], "报错后不许写任何文件")
    finally:
        sandbox.cleanup()


@scenario("order", "gap", "编号跳号时必须报错，且不执行任何步骤、不写任何文件")
def s_order_gap():
    sandbox = Sandbox()
    try:
        normal_migration(sandbox, 1, "one", "one")
        normal_migration(sandbox, 3, "three", "three")

        code, _out, err = sandbox.cli(["up"])
        expect_nonzero(code, "编号跳号时的 up", err)
        expect_eq(os.listdir(sandbox.work), [], "报错后不许写任何文件")
    finally:
        sandbox.cleanup()


# ---------------------------------------------------------------------------
# [idempotent] 契约 2、3、8
# ---------------------------------------------------------------------------


@scenario("idempotent", "repeat-up", "重复 up 不产生任何额外副作用，退出码 0")
def s_idempotent_repeat_up():
    sandbox = Sandbox()
    try:
        for number, token in ((1, "one"), (2, "two"), (3, "three")):
            normal_migration(sandbox, number, "step_" + token, token)

        code, _out, err = sandbox.cli(["up"])
        expect_code(code, 0, "第一次 up", err)
        expected = sandbox.work_manifest()
        expect(
            "data/log.txt" in expected,
            "第一次 up 应当写出数据",
            expected="data/log.txt",
            actual=short(sorted(expected)),
        )

        for attempt in (2, 3):
            code, _out, err = sandbox.cli(["up"])
            expect_code(code, 0, "第 %d 次 up" % attempt, err)
            expect_eq(
                sandbox.work_manifest(),
                expected,
                "第 %d 次 up 之后的工作目录（契约 2 / 8）" % attempt,
            )
    finally:
        sandbox.cleanup()


@scenario("idempotent", "repeat-down", "回退一步 / 回退到 0 / 再回退一次都不产生额外副作用")
def s_idempotent_repeat_down():
    sandbox = Sandbox()
    try:
        for number, token in ((1, "one"), (2, "two"), (3, "three")):
            normal_migration(sandbox, number, "step_" + token, token)

        code, _out, err = sandbox.cli(["up"])
        expect_code(code, 0, "up", err)
        expect_eq(sandbox.read("data/log.txt"), "one\ntwo\nthree\n", "up 之后的数据")

        code, _out, err = sandbox.cli(["down"])
        expect_code(code, 0, "down（回退一步）", err)
        expect_eq(sandbox.read("data/log.txt"), "one\ntwo\n", "回退一步之后的数据")
        expect_eq(sandbox.state()["version"], 2, "回退一步之后的版本")

        code, _out, err = sandbox.cli(["down", "--to", "0"])
        expect_code(code, 0, "down --to 0", err)
        expect_eq(sandbox.read("data/log.txt"), "", "回退到 0 之后的数据")

        pristine = sandbox.work_manifest()
        code, _out, err = sandbox.cli(["down", "--to", "0"])
        expect_code(code, 0, "已经回退到 0 之后再 down（契约 3 / 8）", err)
        expect_eq(
            sandbox.work_manifest(),
            pristine,
            "空操作之后的工作目录",
        )
    finally:
        sandbox.cleanup()


# ---------------------------------------------------------------------------
# [atomic] 契约 5
# ---------------------------------------------------------------------------


def atomic_fixture(sandbox, broken):
    """001 正常；002 注入失败（或正常）；003 正常。"""
    normal_migration(sandbox, 1, "one", "one")
    if broken:
        sandbox.migration(
            "002_two.py",
            [
                'ctx.append("data/log.txt", "two-a\\n")',
                'ctx.write("data/partial.txt", "half\\n")',
                'raise RuntimeError("002 注入的失败")',
            ],
            ["pass"],
        )
    else:
        sandbox.migration(
            "002_two.py",
            [
                'ctx.append("data/log.txt", "two-a\\n")',
                'ctx.append("data/log.txt", "two-b\\n")',
                'ctx.write("data/step002.txt", "step two\\n")',
            ],
            [
                "pass",
            ],
        )
    normal_migration(sandbox, 3, "three", "three")


@scenario("atomic", "data-rollback", "第 2 步失败后，该步改动的数据必须回到执行前")
def s_atomic_data_rollback():
    sandbox = Sandbox()
    try:
        atomic_fixture(sandbox, broken=True)
        code, _out, err = sandbox.cli(["up"])
        expect_nonzero(code, "第 2 步失败时的 up", err)

        expect_eq(sandbox.read("data/log.txt"), "one\n", "失败后 data/log.txt")
        expect(
            not sandbox.exists("data/partial.txt"),
            "失败步骤新造出来的文件必须被回滚",
            expected="data/partial.txt 不存在",
            actual="data/partial.txt 还在",
        )
        expect(
            not sandbox.exists("data/step003.txt"),
            "失败之后的步骤不许被执行",
            expected="data/step003.txt 不存在",
            actual="data/step003.txt 还在",
        )
    finally:
        sandbox.cleanup()


@scenario("atomic", "state-rollback", "第 2 步失败后，状态必须回到执行前（且 sha256 一致）")
def s_atomic_state_rollback():
    sandbox = Sandbox()
    try:
        atomic_fixture(sandbox, broken=True)
        code, _out, err = sandbox.cli(["up"])
        expect_nonzero(code, "第 2 步失败时的 up", err)

        state = sandbox.state()
        expect_eq(state["version"], 1, "失败后的版本")
        expect_eq(
            [entry["version"] for entry in state["applied"]],
            [1],
            "失败后的已应用步骤",
        )
        expect_eq(state["applied"][0]["file"], "001_one.py", "失败后剩下的那个步骤")
        expect_eq(
            state["applied"][0]["sha256"],
            sha256_file(os.path.join(sandbox.migs, "001_one.py")),
            "状态里记的 sha256",
        )
    finally:
        sandbox.cleanup()


@scenario("atomic", "retry-succeeds", "失败回滚之后修好第 2 步，再跑一次必须完整成功")
def s_atomic_retry():
    sandbox = Sandbox()
    try:
        atomic_fixture(sandbox, broken=True)
        code, _out, err = sandbox.cli(["up"])
        expect_nonzero(code, "第 2 步失败时的 up", err)

        # 把 002 改成一个正常版本：它没有被记进状态，所以改写它不该触发历史守卫。
        sandbox.migration(
            "002_two.py",
            [
                'ctx.append("data/log.txt", "two-a\\n")',
                'ctx.append("data/log.txt", "two-b\\n")',
                'ctx.write("data/step002.txt", "step two\\n")',
            ],
            ["pass"],
        )

        code, _out, err = sandbox.cli(["up"])
        expect_code(code, 0, "修好之后再 up", err)
        expect_eq(
            sandbox.read("data/log.txt"),
            "one\ntwo-a\ntwo-b\nthree\n",
            "重跑之后的数据",
        )
        expect_eq(
            [entry["version"] for entry in sandbox.state()["applied"]],
            [1, 2, 3],
            "重跑之后状态里的已应用步骤",
        )
        expect(sandbox.exists("data/step003.txt"), "第 3 步应当执行到", expected=True, actual=False)
    finally:
        sandbox.cleanup()


# ---------------------------------------------------------------------------
# [guard] 契约 6、7、9
# ---------------------------------------------------------------------------


@scenario("guard", "reject-edited-history", "已应用步骤的脚本被改写后必须拒绝继续执行")
def s_guard_reject_edited():
    sandbox = Sandbox()
    try:
        for number, token in ((1, "one"), (2, "two"), (3, "three")):
            normal_migration(sandbox, number, "step_" + token, token)

        code, _out, err = sandbox.cli(["up", "--to", "2"])
        expect_code(code, 0, "up --to 2", err)
        expected = sandbox.work_manifest()

        sandbox.edit_migration("001_step_one.py", "\n# 事后被改写的一行\n")

        code, _out, err = sandbox.cli(["up"])
        expect_nonzero(code, "历史被改写后的 up", err)
        expect_eq(
            sandbox.work_manifest(),
            expected,
            "拒绝执行时必须什么都不改",
        )
    finally:
        sandbox.cleanup()


@scenario("guard", "dry-run-no-side-effect", "plan 确定且与执行顺序一致；--dry-run 不改变任何东西")
def s_guard_dry_run():
    sandbox = Sandbox()
    try:
        slugs = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")
        for index, slug in enumerate(slugs, start=1):
            normal_migration(sandbox, index, slug, slug)
        expected_names = ["%03d_%s.py" % (index, slug) for index, slug in enumerate(slugs, start=1)]

        pristine = sandbox.work_manifest()

        code, out, err = sandbox.cli(["plan"])
        expect_code(code, 0, "空库上的 plan", err)
        expect_eq(lines(out), expected_names, "plan 的输出（按执行顺序）")

        seen = set()
        for seed in ("1", "2", "3", "4", "5"):
            code, out, err = sandbox.cli(["plan"], extra_env={"PYTHONHASHSEED": seed})
            expect_code(code, 0, "PYTHONHASHSEED=%s 时的 plan" % seed, err)
            seen.add(out)
        expect(
            len(seen) == 1,
            "plan 的输出必须与 PYTHONHASHSEED 无关",
            expected="5 个种子下逐字节一致",
            actual="%d 种不同输出" % len(seen),
        )

        code, out, err = sandbox.cli(["up", "--dry-run"])
        expect_code(code, 0, "空库上的 up --dry-run", err)
        expect_eq(lines(out), expected_names, "up --dry-run 的输出")
        expect_eq(sandbox.work_manifest(), pristine, "--dry-run 之后的工作目录（契约 9）")

        code, _out, err = sandbox.cli(["up", "--to", "2"])
        expect_code(code, 0, "up --to 2", err)
        partial = sandbox.work_manifest()

        code, out, err = sandbox.cli(["plan", "--to", "4"])
        expect_code(code, 0, "已应用 2 步之后的 plan --to 4", err)
        expect_eq(lines(out), expected_names[2:4], "plan --to 4 的输出（只列待执行步骤）")

        code, out, err = sandbox.cli(["up", "--to", "4", "--dry-run"])
        expect_code(code, 0, "已应用 2 步之后的 up --to 4 --dry-run", err)
        expect_eq(lines(out), expected_names[2:4], "up --dry-run 的输出（只列待执行步骤）")
        expect_eq(sandbox.work_manifest(), partial, "--dry-run 之后的工作目录（契约 9）")
    finally:
        sandbox.cleanup()


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------


def main(argv):
    do_list = False
    only = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-list", "--list"):
            do_list = True
        elif arg in ("--only", "--group"):
            index += 1
            if index >= len(argv):
                print("--only 需要参数（如 order,guard）", file=sys.stderr)
                return 2
            only = {part.strip() for part in argv[index].split(",") if part.strip()}
        elif arg in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        else:
            print("未知参数：%s" % arg, file=sys.stderr)
            return 2
        index += 1

    if do_list:
        for group, name, why, _fn in SCENARIOS:
            print("[%-10s] %-22s %s" % (group, name, why))
        return 0

    selected = [entry for entry in SCENARIOS if only is None or entry[0] in only]
    if not selected:
        print("没有匹配的场景", file=sys.stderr)
        return 2

    failed = 0
    for group, name, _why, fn in selected:
        try:
            fn()
        except Failure as exc:
            failed += 1
            print(
                "FAIL %s/%s  期望=%s 实际=%s（%s）"
                % (group, name, exc.expected, exc.actual, exc.message)
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(
                "FAIL %s/%s  期望=正常返回 实际=%s: %s"
                % (group, name, type(exc).__name__, exc)
            )
            for line in traceback.format_exc().strip().splitlines()[-3:]:
                print("      | %s" % line)
        else:
            print("PASS %s/%s" % (group, name))

    print("结果：通过 %d/%d" % (len(selected) - failed, len(selected)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
