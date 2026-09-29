import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_case_table_script_passes_its_node_tests() -> None:
    # Files, not the folder: newer Node reads its arguments as globs, older Node as paths.
    test_files = sorted(str(path) for path in (_ROOT / "tests" / "js").glob("*.test.mjs"))
    completed = subprocess.run(
        ["node", "--test", *test_files],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
