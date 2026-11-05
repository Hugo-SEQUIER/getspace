"""The commands that move file parts onto the folder, and check that the two still agree.

`space.files.move` empties the copies of file bytes a v0.5.0 installation kept in
`space.file_parts.content` and writes them to the store instead, verifying each one
against its fingerprint first. `space.files.reconcile` compares the rows with the folder:
objects no row names, and rows whose object is gone.
"""


def size_text(count) -> str:
    """A byte count in words, for the lines an operator reads."""
    count = int(count)
    if count < 1024:
        return f"{count} bytes"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KiB"
    if count < 1024 * 1024 * 1024:
        return f"{count / (1024 * 1024):.1f} MiB"
    return f"{count / (1024 * 1024 * 1024):.1f} GiB"
