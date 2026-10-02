"""tools/macos/relocate_repo.sh: moving the repo out of a folder macOS protects.

The script is bash and runs once on a real Mac, so it is exercised for real here, against a fake
$HOME laid out like that Mac: the repo under Desktop/Claude/, a dirty working tree, the Blender
add-on link, Houdini package links (one symbolic link, one copied file), ~/.ylos/repo_path and a
shell rc file. `pgrep` and the server engine are recording stubs; `python3` is pinned to the
interpreter running the tests (the CI matrix 3.9 / 3.11 / 3.13 runs the rewrites too).

NOT covered (no macOS here): `ditto` (Linux falls back to `cp -R`), the iCloud download it
triggers, and the Finder reveal at the end. Run on a Mac, the same tests use ditto and bash 3.2.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "macos" / "relocate_repo.sh"

_MISSING = [t for t in ("bash", "git") if shutil.which(t) is None]

# Stand-in for launch_ui.command: records every call, answers --status with $ENGINE_STATUS.
ENGINE_STUB = """#!/bin/sh
echo "$*" >> "$ENGINE_LOG"
case "$1" in
    --status) echo "${ENGINE_STATUS:-stopped}" ;;
esac
exit 0
"""

# Stand-in for `pgrep -i -x NAME`: NAME "runs" when listed in $RUNNING (case-insensitive).
PGREP_STUB = """#!/bin/sh
name=$(printf '%s' "$3" | tr 'A-Z' 'a-z')
for r in $RUNNING; do
    [ "$(printf '%s' "$r" | tr 'A-Z' 'a-z')" = "$name" ] && exit 0
done
exit 1
"""

OLD_PKG = '{\n    "env": [\n        {\n            "YLOS_REPO": "$HOME/Desktop/Claude/YlosPipeline"\n        }\n    ]\n}\n'


def _write_exec(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout


@unittest.skipIf(_MISSING, "needs " + ", ".join(_MISSING))
class TestRelocateRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.src = self.home / "Desktop" / "Claude" / "YlosPipeline"
        self.dest = self.home / "Developer" / "YlosPipeline"
        self.backup = self.src.parent / "YlosPipeline (old copy - moved to Developer)"

        # --- the repo, as on the Mac ---
        (self.src / "tools" / "macos").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.src / "tools" / "macos" / "relocate_repo.sh")
        (self.src / "ylos_ui.py").write_text("# server\n")
        _write_exec(self.src / "launch_ui.command", ENGINE_STUB)
        (self.src / "plugins" / "blender").mkdir(parents=True)
        (self.src / "plugins" / "blender" / "__init__.py").write_text("# add-on\n")
        (self.src / "plugins" / "houdini").mkdir(parents=True)
        (self.src / "plugins" / "houdini" / "ylos.json").write_text(OLD_PKG)
        (self.src / ".gitignore").write_text("__pycache__/\n")
        _git(self.src, "init", "-q")
        _git(self.src, "add", "-A")
        _git(self.src, "commit", "-q", "-m", "init")
        self.head = _git(self.src, "rev-parse", "HEAD").strip()
        # uncommitted work must travel too
        (self.src / "ylos_ui.py").write_text("# server, edited\n")
        (self.src / "notes.txt").write_text("untracked\n")
        (self.src / "__pycache__").mkdir()
        (self.src / "__pycache__" / "ylos_ui.cpython-313.pyc").write_bytes(b"\0")
        self.status = _git(self.src, "status", "--porcelain")

        # --- what points at the repo from outside ---
        lib = self.home / "Library"
        addons = lib / "Application Support" / "Blender" / "5.2" / "scripts" / "addons"
        addons.mkdir(parents=True)
        self.addon_link = addons / "ylos_pipeline"
        self.addon_link.symlink_to(self.src / "plugins" / "blender")
        other_addons = lib / "Application Support" / "Blender" / "4.5" / "scripts" / "addons"
        other_addons.mkdir(parents=True)
        self.foreign_link = other_addons / "ylos_pipeline"
        self.foreign_link.symlink_to(self.tmp / "elsewhere")
        hou = lib / "Preferences" / "houdini"
        (hou / "21.0" / "packages").mkdir(parents=True)
        self.pkg_link = hou / "21.0" / "packages" / "ylos.json"
        self.pkg_link.symlink_to(self.src / "plugins" / "houdini" / "ylos.json")
        (hou / "20.5" / "packages").mkdir(parents=True)
        self.pkg_copy = hou / "20.5" / "packages" / "ylos.json"
        self.pkg_copy.write_text(OLD_PKG)
        self.pkg_other = hou / "20.5" / "packages" / "other.json"
        self.pkg_other.write_text('{"env": [{"OTHER": "$HOME/Desktop/Claude/YlosPipelineX"}]}\n')
        (self.home / ".ylos").mkdir()
        (self.home / ".ylos" / "repo_path").write_text(str(self.src) + "\n")
        (self.home / ".zshrc").write_text('alias ylos="cd $HOME/Desktop/Claude/YlosPipeline"\n')

        # --- stubs ---
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        _write_exec(self.stubs / "pgrep", PGREP_STUB)
        _write_exec(self.stubs / "python3", '#!/bin/sh\nexec "{}" "$@"\n'.format(sys.executable))
        self.engine_log = self.tmp / "engine.log"

    def run_script(self, *args: str, running: str = "", status: str = "stopped",
                   script: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ, HOME=str(self.home), RUNNING=running, ENGINE_STATUS=status,
                   ENGINE_LOG=str(self.engine_log),
                   PATH="{}{}{}".format(self.stubs, os.pathsep, os.environ.get("PATH", "")))
        target = script or (self.src / "tools" / "macos" / "relocate_repo.sh")
        return subprocess.run(["bash", str(target), *args], env=env, capture_output=True,
                              text=True, timeout=120)

    def engine_calls(self) -> str:
        return self.engine_log.read_text() if self.engine_log.exists() else ""

    def assert_untouched(self):
        self.assertTrue(self.src.is_dir(), "the original must stay where it was")
        self.assertFalse(self.backup.exists())
        self.assertEqual(os.readlink(self.addon_link), str(self.src / "plugins" / "blender"))
        self.assertEqual(self.pkg_copy.read_text(), OLD_PKG)
        self.assertEqual((self.home / ".ylos" / "repo_path").read_text().strip(), str(self.src))

    # --- the move -----------------------------------------------------------------------------
    def test_moves_the_repo_and_repoints_everything(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # the copy: same commit, same uncommitted work, history intact, no stale bytecode
        self.assertEqual(_git(self.dest, "rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(_git(self.dest, "status", "--porcelain").replace(" M plugins/houdini/ylos.json\n", ""),
                         self.status)
        self.assertEqual((self.dest / "ylos_ui.py").read_text(), "# server, edited\n")
        self.assertFalse((self.dest / "__pycache__").exists())
        subprocess.run(["git", "-C", str(self.dest), "fsck", "--no-progress"], check=True,
                       capture_output=True)

        # the original is kept, renamed, never deleted
        self.assertFalse(self.src.exists())
        self.assertTrue((self.backup / ".git").is_dir())

        # what pointed at the old folder now points at the new one
        self.assertEqual(os.readlink(self.addon_link), str(self.dest / "plugins" / "blender"))
        self.assertEqual(os.readlink(self.pkg_link), str(self.dest / "plugins" / "houdini" / "ylos.json"))
        self.assertIn('"$HOME/Developer/YlosPipeline"', self.pkg_copy.read_text())
        self.assertEqual(Path(str(self.pkg_copy) + ".bak").read_text(), OLD_PKG)
        self.assertIn('"YLOS_REPO": "$HOME/Developer/YlosPipeline"',
                      (self.dest / "plugins" / "houdini" / "ylos.json").read_text())
        self.assertEqual((self.home / ".ylos" / "repo_path").read_text().strip(), str(self.dest))

        # ... and nothing else moved
        self.assertEqual(os.readlink(self.foreign_link), str(self.tmp / "elsewhere"))
        self.assertIn("YlosPipelineX", self.pkg_other.read_text())
        self.assertFalse(Path(str(self.pkg_other) + ".bak").exists())

        self.assertIn("--stop", self.engine_calls())
        self.assertIn(".zshrc still mentions the old folder", r.stdout)
        self.assertIn("Done. The repo now lives in " + str(self.dest), r.stdout)

    def test_a_committed_new_path_is_left_alone(self):
        # once ylos.json is committed with the new place, the copy has nothing to rewrite
        (self.src / "plugins" / "houdini" / "ylos.json").write_text(
            OLD_PKG.replace("Desktop/Claude", "Developer"))
        _git(self.src, "commit", "-q", "-am", "new place")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("ylos.json: YLOS_REPO", r.stdout)
        self.assertNotIn("plugins/houdini/ylos.json", _git(self.dest, "status", "--porcelain"))

    # --- refusals: nothing is touched -----------------------------------------------------------
    def test_refuses_an_existing_destination(self):
        self.dest.mkdir(parents=True)
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already exists", r.stderr)
        self.assert_untouched()
        self.assertEqual(self.engine_calls(), "", "the server must not even be stopped")

    def test_refuses_while_blender_runs(self):
        r = self.run_script(running="Blender")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("quit first: Blender", r.stderr)
        self.assertFalse(self.dest.exists())
        self.assert_untouched()

    def test_refuses_while_houdini_runs(self):
        r = self.run_script(running="happrentice")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("happrentice", r.stderr)
        self.assert_untouched()

    def test_refuses_a_protected_destination(self):
        for dest in (self.home / "Documents" / "YlosPipeline", self.home / "Desktop" / "Ylos2",
                     Path("/Volumes/NVMe/YlosPipeline")):
            r = self.run_script(str(dest))
            self.assertNotEqual(r.returncode, 0, dest)
            self.assertIn("macOS protects", r.stderr)
        self.assert_untouched()

    def test_refuses_a_destination_inside_the_repo_or_relative(self):
        for dest in (str(self.src / "sub"), "Developer/YlosPipeline"):
            r = self.run_script(dest)
            self.assertNotEqual(r.returncode, 0, dest)
        self.assert_untouched()

    def test_stops_when_the_server_cannot_be_stopped(self):
        r = self.run_script(status="running")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("could not be stopped", r.stderr)
        self.assertFalse(self.dest.exists())
        self.assert_untouched()

    def test_refuses_outside_the_repo(self):
        stray = self.tmp / "stray" / "tools" / "macos"
        stray.mkdir(parents=True)
        shutil.copy2(SCRIPT, stray / "relocate_repo.sh")
        r = self.run_script(script=stray / "relocate_repo.sh")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not look like the YlosPipeline repo", r.stderr)
        self.assert_untouched()


class TestRelocateScriptStatic(unittest.TestCase):
    def test_executable_and_bash_32_clean(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), "relocate_repo.sh must be executable")
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/bash\n"))
        # bash 4+ only: macOS ships bash 3.2
        for construct in ("declare -A", "mapfile", "readarray", ",,}", "^^}", "|&", "&>>"):
            self.assertNotIn(construct, text, construct)
        # never delete the original: the only rm targets the fresh copy or its __pycache__
        rm_lines = [l.strip() for l in text.splitlines() if l.strip().startswith("rm ")]
        self.assertEqual(rm_lines, ['rm -rf "$DEST"'])


if __name__ == "__main__":
    unittest.main()
