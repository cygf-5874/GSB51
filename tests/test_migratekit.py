"""migratekit 的既有用例（起点全绿）。

这些用例只覆盖「按编号一路 up 全部成功」的路径与编号校验，不依赖任何内部结构。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from migratekit import (  # noqa: E402
    MigrateError,
    Runner,
    VersionError,
    sha256_file,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_MIGRATIONS = os.path.join(ROOT, "migrations")

ALL_STEPS = [
    "001_init.py",
    "002_seed_notes.py",
    "003_tag_notes.py",
    "004_archive.py",
]


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="gsb51-test-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.work = os.path.join(self.home, "work")
        self.migs = os.path.join(self.home, "migs")
        os.makedirs(self.work)
        shutil.copytree(
            REAL_MIGRATIONS,
            self.migs,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    def runner(self):
        return Runner(self.work, self.migs)

    def add_migration(self, filename, body=None):
        if body is None:
            body = "def up(ctx):\n    pass\n\n\ndef down(ctx):\n    pass\n"
        with open(os.path.join(self.migs, filename), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)

    def remove_migration(self, filename):
        os.remove(os.path.join(self.migs, filename))

    def read(self, rel):
        with open(os.path.join(self.work, rel), "r", encoding="utf-8") as handle:
            return handle.read()

    def state_on_disk(self):
        with open(os.path.join(self.work, "state.json"), "r", encoding="utf-8") as handle:
            return json.load(handle)


class DiscoveryTest(Base):
    def test_discovery_is_ascending(self):
        runner = self.runner()
        self.assertEqual([step.version for step in runner.ordered_steps()], [1, 2, 3, 4])
        self.assertEqual([step.filename for step in runner.ordered_steps()], ALL_STEPS)

    def test_duplicate_number_rejected(self):
        self.add_migration("002_other.py")
        with self.assertRaises(VersionError):
            self.runner()

    def test_gap_rejected(self):
        self.remove_migration("002_seed_notes.py")
        with self.assertRaises(VersionError):
            self.runner()

    def test_bad_name_rejected(self):
        self.add_migration("5_short.py")
        with self.assertRaises(VersionError):
            self.runner()


class UpTest(Base):
    def test_up_applies_every_step_in_order(self):
        runner = self.runner()
        runner.up()
        self.assertEqual([entry["version"] for entry in runner.state.applied], [1, 2, 3, 4])
        self.assertEqual(self.read("data/meta.txt"), "schema=4\n")
        self.assertEqual(self.read("data/notes.txt"), "")
        self.assertEqual(self.read("data/archive.txt"), "buy milk\nread book\ntags: todo\n")

    def test_up_to_target(self):
        runner = self.runner()
        runner.up(target=2)
        self.assertEqual([entry["version"] for entry in runner.state.applied], [1, 2])
        self.assertEqual(self.read("data/meta.txt"), "schema=2\n")
        self.assertEqual(self.read("data/notes.txt"), "buy milk\nread book\n")
        self.assertFalse(os.path.exists(os.path.join(self.work, "data", "archive.txt")))

    def test_state_records_sha256(self):
        runner = self.runner()
        runner.up()
        state = self.state_on_disk()
        self.assertEqual(state["version"], 4)
        self.assertEqual([entry["file"] for entry in state["applied"]], ALL_STEPS)
        for entry in state["applied"]:
            expected = sha256_file(os.path.join(self.migs, entry["file"]))
            self.assertEqual(entry["sha256"], expected)


class PlanTest(Base):
    def test_plan_lists_all_steps(self):
        runner = self.runner()
        self.assertEqual(sorted(runner.plan()), sorted(ALL_STEPS))

    def test_plan_does_not_write(self):
        runner = self.runner()
        runner.plan()
        self.assertEqual(os.listdir(self.work), [])


class DownTest(Base):
    def test_down_reverts_one_step(self):
        runner = self.runner()
        runner.up()
        runner.down()
        self.assertEqual([entry["version"] for entry in runner.state.applied], [1, 2, 3])
        self.assertEqual(self.read("data/meta.txt"), "schema=3\n")
        self.assertEqual(self.read("data/notes.txt"), "buy milk\nread book\ntags: todo\n")
        self.assertFalse(os.path.exists(os.path.join(self.work, "data", "archive.txt")))

    def test_down_to_zero(self):
        runner = self.runner()
        runner.up()
        runner.down(target=0)
        self.assertEqual(runner.state.applied, [])
        self.assertFalse(os.path.exists(os.path.join(self.work, "data", "notes.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.work, "data", "meta.txt")))


class CtxTest(Base):
    def test_ctx_stays_inside_workdir(self):
        runner = self.runner()
        expected = os.path.join(runner.workdir, "data", "notes.txt")
        self.assertEqual(runner.ctx.path("data/notes.txt"), expected)
        with self.assertRaises(MigrateError):
            runner.ctx.path("../escape.txt")


if __name__ == "__main__":
    unittest.main()
