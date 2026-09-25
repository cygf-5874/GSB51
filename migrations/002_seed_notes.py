"""塞两条种子笔记。"""

SEEDS = ["buy milk\n", "read book\n"]


def up(ctx):
    for line in SEEDS:
        ctx.append("data/notes.txt", line)
    ctx.write("data/meta.txt", "schema=2\n")


def down(ctx):
    lines = ctx.read("data/notes.txt", "").splitlines(True)
    for line in reversed(SEEDS):
        if line in lines:
            lines.remove(line)
    ctx.write("data/notes.txt", "".join(lines))
    ctx.write("data/meta.txt", "schema=1\n")
