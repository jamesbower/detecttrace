"""Output file helpers shared by the dashboard and the results JSON writers."""

import os
import stat
import tempfile
from pathlib import Path

# Enough for either marker: the generator meta tag or the generated_by key sits near the top.
MARKER_READ_BYTES = 64 * 1024


def _read_umask() -> int:
    # The only way to read the umask is to set it; done once at import, not per write.
    umask = os.umask(0)
    os.umask(umask)
    return umask


_UMASK = _read_umask()


def write_text_atomically(text: str, path: Path) -> None:
    """Write `text` as UTF-8 to a temporary file beside `path`, then move it into place.

    A reader never sees a half-written file, and a failed write leaves `path` as it was. A
    replaced file keeps its permission bits; a new one gets the mode a plain open() would give.
    """
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
        # mkstemp creates the file owner-only, which would silently narrow who can read it.
        os.chmod(temp_name, _to_target_mode(path))
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def read_head(path: Path, size: int) -> bytes | None:
    """The first `size` bytes of `path`, or None when it isn't a regular file.

    A path that can't be read raises OSError: "can't tell" must not look like "not ours".
    """
    # stat() before open(), so a named pipe never blocks the run.
    if not stat.S_ISREG(path.stat().st_mode):
        return None
    with path.open("rb") as file:
        return file.read(size)


def _to_target_mode(path: Path) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return 0o666 & ~_UMASK
