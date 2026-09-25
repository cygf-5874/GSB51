"""把笔记整份挪进归档文件。"""


def up(ctx):
    text = ctx.read("data/notes.txt", "")
    ctx.write("data/archive.txt", text)
    ctx.write("data/notes.txt", "")
    ctx.write("data/meta.txt", "schema=4\n")


def down(ctx):
    text = ctx.read("data/archive.txt", "")
    ctx.write("data/notes.txt", text)
    ctx.remove("data/archive.txt")
    ctx.write("data/meta.txt", "schema=3\n")
