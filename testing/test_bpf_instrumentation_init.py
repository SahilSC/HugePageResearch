# ruff: noqa: E402
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = str(ROOT / "python" / "kernmlops")


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class BPFInstrumentationInitTest(unittest.TestCase):
    def test_hook_names_do_not_import_perf_hook(self):
        result = _run_python(
            f"""
import sys
sys.path.insert(0, {PYTHON_ROOT!r})
import data_collection.bpf_instrumentation as bpf
assert "data_collection.bpf_instrumentation.perf.perf_hook" not in sys.modules
names = bpf.hook_names()
assert names[0] == "file_data"
assert "perf" in names
assert "vaptr" == names[-1]
assert "data_collection.bpf_instrumentation.perf.perf_hook" not in sys.modules
"""
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_get_hook_for_vaptr_does_not_import_perf_hook(self):
        result = _run_python(
            f"""
import sys
sys.path.insert(0, {PYTHON_ROOT!r})
import data_collection.bpf_instrumentation as bpf
hook_type = bpf.get_hook("vaptr")
assert hook_type is not None
assert hook_type.__name__ == "VAPtrHook"
assert "data_collection.bpf_instrumentation.vaptr_hook" in sys.modules
assert "data_collection.bpf_instrumentation.perf.perf_hook" not in sys.modules
"""
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_importing_data_collection_does_not_import_perf_hook(self):
        result = _run_python(
            f"""
import sys
sys.path.insert(0, {PYTHON_ROOT!r})
import data_collection
assert "data_collection.bpf_instrumentation.perf.perf_hook" not in sys.modules
"""
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)


if __name__ == "__main__":
    unittest.main()
