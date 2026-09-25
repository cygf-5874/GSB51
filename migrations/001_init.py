"""建库：notes.txt 与 meta.txt。"""


def up(ctx):
    ctx.write("data/notes.txt", "")
    ctx.write("data/meta.txt", "schema=1\n")


def down(ctx):
    ctx.remove("data/notes.txt")
    ctx.remove("data/meta.txt")
