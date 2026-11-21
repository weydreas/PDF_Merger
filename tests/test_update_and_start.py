"""Update-and-start safety tests; no network or medical files are used."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class UpdateAndStartContracts(unittest.TestCase):
    def test_windows_launcher_invokes_safe_powershell_updater(self):
        cmd = (ROOT / "update-and-start.cmd").read_text(encoding="utf-8").lower()
        self.assertIn("%~dp0update-and-start.ps1", cmd)
        self.assertIn("powershell.exe -nologo -noprofile", cmd)
        self.assertIn("pause >nul", cmd)

    def test_updater_uses_repository_root_and_never_forces_updates(self):
        file = ROOT / "update-and-start.ps1"
        self.assertTrue(file.read_bytes().startswith(b"\xef\xbb\xbf"))
        text = file.read_text(encoding="utf-8-sig").lower()
        self.assertIn("$projectroot = $psscriptroot", text)
        self.assertIn("status --porcelain --untracked-files=normal", text)
        self.assertIn("pull --ff-only origin main", text)
        self.assertIn("& $startcmd", text)
        self.assertLess(text.index("pull --ff-only origin main"), text.index("& $startcmd"))
        self.assertNotIn("reset --hard", text)
        self.assertNotIn("clean -fd", text)
        self.assertNotIn("force", text)


@unittest.skipUnless(sys.platform == "win32" and shutil.which("git"),
                     "Requires Windows and Git for Windows")
class UpdateAndStartWindowsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "PDF_merger"
        self.origin = self.root / "origin.git"
        self.publisher = self.root / "publisher"
        self.repo.mkdir()
        self.git("init", "--bare", "--initial-branch=main", str(self.origin))
        self.git("init", "-b", "main", str(self.repo))
        for name in ("update-and-start.cmd", "update-and-start.ps1"):
            shutil.copy2(ROOT / name, self.repo / name)
        (self.repo / "start.cmd").write_text(
            '@echo off\r\n> "%~dp0launched.txt" echo started\r\nexit /b 0\r\n',
            encoding="ascii",
        )
        (self.repo / ".gitignore").write_text(
            ".env\n/logs/\n/input/\n/output/\n",
            encoding="ascii",
        )
        (self.repo / "readme.txt").write_text("original", encoding="ascii")
        self.git("-C", str(self.repo), "config", "user.name", "Updater Test")
        self.git("-C", str(self.repo), "config", "user.email", "test@example.invalid")
        self.git("-C", str(self.repo), "add", ".")
        self.git("-C", str(self.repo), "commit", "-m", "initial")
        self.git("-C", str(self.repo), "remote", "add", "origin", str(self.origin))
        self.git("-C", str(self.repo), "push", "-u", "origin", "main")
        self.git("clone", "--branch", "main", str(self.origin), str(self.publisher))
        self.git("-C", str(self.publisher), "config", "user.name", "Publisher Test")
        self.git("-C", str(self.publisher), "config", "user.email", "test@example.invalid")
        (self.repo / "logs").mkdir()
        (self.repo / "input").mkdir()
        (self.repo / "output").mkdir()
        (self.repo / ".env").write_text("PDF_MERGER_API_KEY=local-sentinel", encoding="ascii")
        (self.repo / "input" / "private.pdf").write_bytes(b"synthetic")
        (self.repo / "output" / "old.pdf").write_bytes(b"synthetic")

    def git(self, *args):
        result = subprocess.run(
            ["git", *args], cwd=self.root, text=True,
            capture_output=True, encoding="utf-8", errors="replace", timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        return result.stdout

    def publish(self):
        (self.publisher / "new-release.txt").write_text("new release", encoding="ascii")
        self.git("-C", str(self.publisher), "add", "new-release.txt")
        self.git("-C", str(self.publisher), "commit", "-m", "publish release")
        self.git("-C", str(self.publisher), "push", "origin", "main")

    def run_launcher(self):
        return subprocess.run(
            ["cmd.exe", "/d", "/c", str(self.repo / "update-and-start.cmd")],
            cwd=self.repo / "logs", input="\n", capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=90,
        )

    def assert_private_data_unchanged(self):
        self.assertEqual((self.repo / ".env").read_text(encoding="ascii"),
                         "PDF_MERGER_API_KEY=local-sentinel")
        self.assertEqual((self.repo / "input" / "private.pdf").read_bytes(), b"synthetic")
        self.assertEqual((self.repo / "output" / "old.pdf").read_bytes(), b"synthetic")

    def test_one_click_updates_from_logs_then_starts(self):
        self.publish()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        self.assertTrue((self.repo / "new-release.txt").exists())
        self.assertTrue((self.repo / "launched.txt").exists())
        self.assert_private_data_unchanged()

    def test_dirty_tracked_file_blocks_update_and_launch(self):
        self.publish()
        (self.repo / "readme.txt").write_text("local edit", encoding="ascii")
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        self.assertFalse((self.repo / "new-release.txt").exists())
        self.assertFalse((self.repo / "launched.txt").exists())
        self.assertEqual((self.repo / "readme.txt").read_text(encoding="ascii"), "local edit")
        self.assert_private_data_unchanged()

    def test_non_fast_forward_is_rejected_without_reset(self):
        self.publish()
        (self.repo / "readme.txt").write_text("local commit", encoding="ascii")
        self.git("-C", str(self.repo), "add", "readme.txt")
        self.git("-C", str(self.repo), "commit", "-m", "local divergence")
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        self.assertFalse((self.repo / "new-release.txt").exists())
        self.assertFalse((self.repo / "launched.txt").exists())
        self.assertEqual((self.repo / "readme.txt").read_text(encoding="ascii"), "local commit")
        self.assert_private_data_unchanged()


if __name__ == "__main__":
    unittest.main()
