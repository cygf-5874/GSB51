# migratekit —— 无依赖数据迁移引擎

`migratekit` 是一个纯标准库（零第三方依赖）的**数据迁移引擎**：它扫描迁移目录里的
`NNN_name.py`，按编号升序调用每一步的 `up(ctx)` / `down(ctx)`，把「已经应用过哪些步骤」
记进工作目录里的 `state.json`。不连数据库、不访问网络，状态与迁移写出的数据都只落在工作目录内。

```bash
python3 -m migratekit up                  # 执行全部待执行步骤
python3 -m migratekit up --to 2           # 只执行到 002
python3 -m migratekit plan                # 按执行顺序列出待执行步骤（每行一个脚本文件名）
python3 -m migratekit plan --to 2         # 只看 002 以前的待执行步骤
python3 -m migratekit up --dry-run        # 只打印将要执行的步骤，不改变任何东西
python3 -m migratekit down                # 回退一步
python3 -m migratekit down --to 1         # 回退到 001
```

`--dir <工作目录>` 指定工作目录（默认 `.`），`--migrations <目录>` 指定迁移脚本目录
（默认 `<工作目录>/migrations`）。

## 目录

```
migratekit/    引擎（__init__.py / __main__.py / runner.py / state.py / snapshot.py）
migrations/    仓库自带的 4 个示例迁移（001_init.py … 004_archive.py）
tests/         既有用例（12 个）
scripts/       build.sh / test.sh / check.sh
check.py       固定验收入口（**勿改**）
```

## 对外契约（10 条）

下面每一条都是对外契约，实现与用例都要守住。**验收以 `check.py` 为准**，语义细节以本节为准。

1. **编号与顺序**：迁移脚本的文件名必须形如 `NNN_name.py`（`NNN` 是三位十进制编号，从 `001`
   起**连续**、无重复）。引擎按编号**升序**执行。编号重复、跳号或文件名不合规时，必须报错并以
   非零退出码结束，且**不执行任何步骤、不写任何文件**。
2. **幂等**：`up` 只执行「按当前状态看还没应用过」的步骤。对同一状态重复执行同一批迁移，
   不得产生任何额外副作用 —— 工作目录的内容逐字节不变。
3. **目标版本与回退**：`up [--to N]` 执行到编号 N（含 N）为止；`down` 回退一步，
   `down [--to N]` 回退到编号 N（含 N 仍保留）。当前状态已经**达到或越过**目标时是**空操作**：
   退出码 0 且不写任何文件。
4. **版本跳跃**：允许从任意版本跳到任意更高的目标版本，中间缺失的步骤按编号升序**全部**执行，
   每一步各自独立提交（见第 5 条），状态里能看到每一步各自的记录。
5. **失败原子**：任何一步失败时，该步必须**全生效或全不生效** —— 该步造成的**数据改动与状态
   改动一起**回滚到执行该步之前的快照，失败的那一步不得留在状态里。退出码非零。
6. **历史不可改**：每个已应用步骤的脚本 sha256 记在状态里。任何会继续推进或回退历史的命令，
   都必须先把已应用步骤的脚本内容与其记录的 sha256 逐一核对；对不上（或脚本已不存在）时必须
   **拒绝执行**，退出码非零，且不写任何文件。
7. **`plan` 确定性**：`plan` 按**执行顺序（编号升序）**逐行列出待执行步骤，每行恰好是一个脚本
   文件名；同一状态下，跨进程、任意 `PYTHONHASHSEED` 输出都**逐字节一致**。`up --dry-run`
   打印与 `plan` 相同的行。
8. **空库与已最新**：工作目录里没有任何待执行步骤时（没有迁移脚本，或已经是最新版本），`up`
   退出码 0 且**不写任何文件**。
9. **`--dry-run` 无副作用**：`up --dry-run` 只打印将要执行的步骤，退出码 0，不改变状态、
   不改变任何文件。
10. **无外部依赖**：不连数据库、不访问网络。状态（`state.json`）与迁移写出的数据文件都只落在
    工作目录内，工作目录之外不产生任何文件。

## 迁移脚本与 `ctx`

迁移脚本放在迁移目录里，文件名 `NNN_name.py`。每个脚本必须同时定义 `up(ctx)` 与 `down(ctx)`：

```python
def up(ctx):
    ctx.write("data/notes.txt", "")


def down(ctx):
    ctx.remove("data/notes.txt")
```

`ctx` 只允许访问工作目录：

| 方法 | 说明 |
| --- | --- |
| `ctx.path(rel)` | 工作目录内的绝对路径；`rel` 越出工作目录时抛 `MigrateError` |
| `ctx.exists(rel)` | 文件是否存在 |
| `ctx.read(rel, default="")` | 读文本（utf-8、LF）；不存在时返回 `default` |
| `ctx.write(rel, text)` | 写文本（utf-8、LF），自动建父目录 |
| `ctx.append(rel, text)` | 追加文本（utf-8、LF） |
| `ctx.remove(rel)` | 删除文件；不存在时不报错 |

## 状态文件

工作目录里的 `state.json`：

```json
{
  "version": 2,
  "applied": [
    {"version": 1, "file": "001_init.py", "sha256": "<脚本的 sha256，64 位小写 hex>"},
    {"version": 2, "file": "002_seed_notes.py", "sha256": "..."}
  ]
}
```

`version` 是当前版本（等于已应用步骤里的最大编号；没有已应用步骤时为 0），`applied` 按应用
顺序排列。没有任何已应用步骤时，工作目录里不存在 `state.json`。

## 怎么跑

```bash
bash scripts/build.sh                       # 语法 / 导入自检
bash scripts/test.sh                        # 既有用例（起点 12/12 全绿）
bash scripts/check.sh                       # 固定验收：10 个场景，全过才退 0
bash scripts/check.sh -list                 # 列出全部场景
bash scripts/check.sh --only order          # 只跑一组（order / idempotent / atomic / guard）
```

`check.py` 是**固定验收入口，不要改它**。它在 `tempfile.mkdtemp()` 造的独立沙箱里，为每个场景
单独起一个子进程跑引擎，带 30 秒看门狗；失败不早退，一次把问题全暴露出来。

## 版本前提

- Python 3.9 及以上（开发与实测用 3.13），只用标准库（`unittest`、`hashlib`、`importlib`、
  `json`、`subprocess`、`tempfile`）。没有第三方依赖，不需要联网、数据库或中间件。
- 判据是确定性的：输入写死，不依赖墙钟、随机源、机器速度或 `dict` / `set` 的迭代顺序。

## 任务原文（User Prompt）

```
满足下面三个条件才算交付：迁移能跑通、中途失败能回滚、已经跑过的历史不能被改写。
migratekit 是 Python 3 的数据迁移引擎，仅标准库（unittest 跑用例），不连数据库、不访问网络，
状态与数据快照都落在工作目录里，构建与自检走 scripts/*.sh。
README「对外契约」一节列了 10 条；tests/ 下 12 个用例当前全绿 —— 但它们只覆盖一路成功的路径。

任务：把这 10 条补齐，让固定件全过。

验收（check.py 是固定验收程序，别改）：
- bash scripts/check.sh 退出码 0，10 个场景全过（order 3 + idempotent 2 + atomic 3 + guard 2）。

约束：
1. 不改 check.py、不改 tests/ 里既有用例的断言；已公开的函数签名不变（可新增）。
2. 不引入第三方依赖，只用标准库。
3. 任何失败都不许把工作目录留在「改了一半」的状态。
4. plan 的输出必须可复现：同一状态跑两次逐字节一致。
```
