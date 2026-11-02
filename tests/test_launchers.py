"""Launchers work on their platform: LF + executable start.sh, CRLF start.bat."""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402


class LauncherTest(unittest.TestCase):
    def test_start_sh_uses_lf_and_a_shebang(self):
        data = (ROOT / "start.sh").read_bytes()
        self.assertTrue(data.startswith(b"#!/usr/bin/env sh\n"))
        self.assertNotIn(b"\r", data)
        self.assertIn(b'exec "$PY" app.py "$@"', data)

    def test_start_sh_is_executable_in_git(self):
        if not shutil.which("git") or not (ROOT / ".git").exists():
            self.skipTest("not a git checkout")
        out = subprocess.run(["git", "ls-files", "-s", "start.sh"], cwd=ROOT, capture_output=True, text=True).stdout
        if not out:
            self.skipTest("start.sh is not committed yet")
        self.assertTrue(out.startswith("100755"), out)

    def test_start_bat_uses_crlf(self):
        data = (ROOT / "start.bat").read_bytes()
        lines = data.split(b"\n")[:-1]
        self.assertTrue(lines and all(line.endswith(b"\r") for line in lines))
        self.assertIn(b'cd /d "%~dp0"', data)

    def test_default_storage_is_inside_the_project_and_ignored(self):
        args = app.parse_args([])
        self.assertEqual(Path(args.data_dir), ROOT / "storage")
        self.assertEqual(Path(app.parse_args(["--data-dir", "x"]).data_dir), Path("x"))
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("storage/", ignored)
        self.assertIn("*.sqlite3", ignored)


if __name__ == "__main__":
    unittest.main()
