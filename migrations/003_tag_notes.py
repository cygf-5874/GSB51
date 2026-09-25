"""给笔记加一行标签。"""

TAG = "tags: todo\n"


def up(ctx):
    ctx.append("data/notes.txt", TAG)
    ctx.write("data/meta.txt", "schema=3\n")


def down(ctx):
    text = ctx.read("data/notes.txt", "")
    if text.endswith(TAG):
        text = text[: -len(TAG)]
    ctx.write("data/notes.txt", text)
    ctx.write("data/meta.txt", "schema=2\n")
