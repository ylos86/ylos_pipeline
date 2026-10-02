"""Launcher tests: launch_ui.command (the engine) and the Ylos.app shell around it.

The engine is bash, so it is exercised for real: a real ylos_ui.py on a free port, $HOME pointed
at a tmp dir, `open` / `osascript` replaced by recording stubs, `python3` pinned to the
interpreter running the tests (so the CI matrix 3.9 / 3.11 / 3.13 runs the server too). That is the
only way to know that --detach / --status / --stop and the dialogs' branching do what the docs say.

NOT covered here (no macOS GUI in CI): the native dialogs themselves, Gatekeeper / TCC prompts
and how the icon renders. The functional tests need bash, curl, lsof, ps and find and skip
cleanly when one is missing; run on a Mac they also check the scripts under the real bash 3.2.
"""
from __future__ import annotations

import http.server
import os
import plistlib
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "launch_ui.command"
APP = REPO / "Ylos.app"
WRAPPER = APP / "Contents" / "MacOS" / "Ylos"
PLIST = APP / "Contents" / "Info.plist"
ICNS = APP / "Contents" / "Resources" / "AppIcon.icns"

_MISSING = [t for t in ("bash", "curl", "lsof", "ps", "find") if shutil.which(t) is None]
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 never via a proxy

# Recording stand-in for osascript: "choose folder" gets $OSA_FOLDER (empty = user cancelled),
# anything else is a dialog and gets $OSA_REPLY. Every call is appended to $OSA_LOG.
OSASCRIPT_STUB = """#!/bin/sh
echo "=== osascript $*" >> "$OSA_LOG"
case "$*" in
    *"choose folder"*) printf '%s\\n' "$OSA_FOLDER"; [ -n "$OSA_FOLDER" ]; exit $? ;;
esac
printf '%s\\n' "$OSA_REPLY"
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_exec(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


class BundleStaticTests(unittest.TestCase):
    """What must hold before anybody double-clicks anything."""

    def test_scripts_are_executable(self):
        for f in (ENGINE, WRAPPER):
            self.assertTrue(os.access(f, os.X_OK), f"{f} lost its exec bit (chmod +x)")

    @unittest.skipIf(shutil.which("bash") is None, "bash missing")
    def test_scripts_parse(self):
        for f in (ENGINE, WRAPPER):
            r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{f.name}: {r.stderr}")

    def test_scripts_stay_bash_3_2_clean(self):
        # /bin/bash on macOS is 3.2 (2007): nothing that needs bash >= 4.
        banned = ("declare -A", "mapfile", "readarray", "&>>", "|&", ",,}", "^^}", "coproc", "local -n")
        for f in (ENGINE, WRAPPER):
            text = f.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, text, f"{f.name}: '{token}' needs bash >= 4")

    def test_info_plist_points_at_real_files(self):
        with open(PLIST, "rb") as fh:
            info = plistlib.load(fh)
        self.assertEqual(info["CFBundlePackageType"], "APPL")
        exe = APP / "Contents" / "MacOS" / info["CFBundleExecutable"]
        self.assertTrue(exe.is_file() and os.access(exe, os.X_OK), exe)
        icon = info["CFBundleIconFile"]
        icon = icon if icon.endswith(".icns") else icon + ".icns"
        self.assertTrue((APP / "Contents" / "Resources" / icon).is_file(), icon)

    def test_dialog_script_ships_in_the_bundle(self):
        script = APP / "Contents" / "Resources" / "ask.applescript"
        self.assertIn("on run argv", script.read_text(encoding="utf-8"))
        self.assertIn("Resources/ask.applescript", WRAPPER.read_text(encoding="utf-8"))

    def test_icns_is_well_formed(self):
        data = ICNS.read_bytes()
        self.assertEqual(data[:4], b"icns")
        self.assertEqual(struct.unpack(">I", data[4:8])[0], len(data), "container length")
        pos, sizes = 8, {}
        while pos < len(data):
            kind = data[pos:pos + 4].decode("ascii")
            length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
            payload = data[pos + 8:pos + length]
            self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n", f"{kind} is not a PNG")
            sizes[kind] = struct.unpack(">II", payload[16:24])        # IHDR width, height
            pos += length
        self.assertEqual(pos, len(data), "trailing bytes / truncated chunk")
        self.assertEqual(sizes["icp4"], (16, 16))
        self.assertEqual(sizes["ic07"], (128, 128))
        self.assertEqual(sizes["ic10"], (1024, 1024))


@unittest.skipIf(_MISSING, "needs " + ", ".join(_MISSING))
class _LauncherCase(unittest.TestCase):
    """Isolated world: tmp $HOME, free port, stubbed open / osascript / python3."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ylos_launch_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home, self.bin = self.tmp / "home", self.tmp / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        self.open_log, self.osa_log = self.tmp / "open.log", self.tmp / "osascript.log"
        _write_exec(self.bin / "open", f'#!/bin/sh\necho "$@" >> "{self.open_log}"\n')
        _write_exec(self.bin / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        _write_exec(self.bin / "osascript", OSASCRIPT_STUB)
        self.env = dict(os.environ, HOME=str(self.home), YLOS_PORT=str(self.port),
                        PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                        PYTHONDONTWRITEBYTECODE="1", OSA_LOG=str(self.osa_log),
                        OSA_REPLY="", OSA_FOLDER="")
        self.addCleanup(self._stop_everything)        # LIFO: runs before the rmtree above

    def _stop_everything(self):
        self.engine("--stop")
        for pid in self.listeners():                  # belt and braces: never leave a server behind
            try:
                os.kill(int(pid), signal.SIGKILL)
            except OSError:
                pass

    # -- helpers ------------------------------------------------------------------------
    def engine(self, *args):
        return subprocess.run(["bash", str(ENGINE), *args], env=self.env,
                              capture_output=True, text=True, timeout=90)

    def wrapper(self, app=WRAPPER, reply="", folder=""):
        env = dict(self.env, OSA_REPLY=reply, OSA_FOLDER=folder)
        return subprocess.run([str(app)], env=env, capture_output=True, text=True, timeout=90)

    def status(self):
        r = self.engine("--status")
        return r.returncode, r.stdout.strip()

    def listeners(self):
        r = subprocess.run(["lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN", "-t"],
                           capture_output=True, text=True)
        return sorted(set(r.stdout.split()))

    def opens(self):
        return self.open_log.read_text().splitlines() if self.open_log.exists() else []

    def dialogs(self):
        return self.osa_log.read_text() if self.osa_log.exists() else ""

    def http_status(self, path="/api/config"):
        with _DIRECT.open(f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
            return resp.status

    def make_stale(self):
        os.utime(self.home / ".ylos" / "ui-server.started", (1, 1))   # "started long before the last edit"

    def foreign_listener(self):
        """Something that answers HTTP on the port and is NOT Ylos (another dev server)."""
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)


class EngineTests(_LauncherCase):
    """launch_ui.command --detach / --status / --stop (what Ylos.app delegates to)."""

    def test_detach_serves_then_stop_frees_the_port(self):
        self.assertEqual(self.status(), (1, "stopped"))
        r = self.engine("--detach")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.http_status(), 200)              # alive although the launcher has exited
        self.assertEqual(len(self.listeners()), 1)
        self.assertEqual(self.opens(), [self.url])
        self.assertEqual(self.status(), (0, "running"))
        log = (self.home / ".ylos" / "ui-server.log").read_text()
        self.assertIn(f"http://127.0.0.1:{self.port}", log)    # unbuffered: the banner is already there
        r = self.engine("--stop")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Server stopped.", r.stdout)             # SIGTERM was enough: not the "(forced)" kill -9 fallback
        self.assertEqual(self.listeners(), [])
        self.assertEqual(self.status(), (1, "stopped"))

    def test_detach_reuses_a_running_server(self):
        self.engine("--detach")
        first = self.listeners()
        r = self.engine("--detach")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.listeners(), first)              # no second server
        self.assertEqual(len(self.opens()), 2)                 # but the browser came back

    def test_foreground_mode_reuses_a_running_server_instead_of_blocking(self):
        self.engine("--detach")
        first = self.listeners()
        r = self.engine()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.listeners(), first)
        self.assertEqual(len(self.opens()), 2)

    def test_the_previous_log_is_kept_as_dot_one(self):
        self.engine("--detach")
        self.engine("--stop")
        self.engine("--detach")
        ylos = self.home / ".ylos"
        self.assertTrue((ylos / "ui-server.log.1").is_file())
        self.assertEqual((ylos / "ui-server.log").read_text().count("[launcher]"), 1)

    def test_a_foreign_listener_is_neither_mistaken_for_ylos_nor_killed(self):
        self.foreign_listener()
        self.assertEqual(self.status(), (1, "blocked"))
        r = self.engine("--detach")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("another application", r.stderr)
        self.assertEqual(self.opens(), [])                     # never opens somebody else's page
        r = self.engine("--stop")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.http_status("/"), 200)           # the stranger is still there

    def test_status_turns_stale_when_the_code_is_newer_than_the_server(self):
        self.engine("--detach")
        self.make_stale()
        self.assertEqual(self.status(), (0, "stale"))
        self.engine("--stop")
        self.engine("--detach")                                # a restart re-arms the marker
        self.assertEqual(self.status(), (0, "running"))

    def test_a_server_that_dies_at_startup_is_reported_with_its_log(self):
        _write_exec(self.bin / "python3", "#!/bin/sh\necho 'boom: python3 is broken' >&2\nexit 3\n")
        r = self.engine("--detach")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not start", r.stderr)
        self.assertIn("boom: python3 is broken", r.stderr)     # the reason reaches the caller
        self.assertEqual(self.opens(), [])

    def test_unknown_option_is_refused(self):
        r = self.engine("--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Usage", r.stderr)


class AppTests(_LauncherCase):
    """Ylos.app's shell: what a double-click means, with osascript recorded instead of shown."""

    def relocated_app(self):
        dest = self.tmp / "Applications" / "Ylos.app"
        dest.parent.mkdir()
        shutil.copytree(APP, dest)                             # copy2 keeps the exec bit
        return dest / "Contents" / "MacOS" / "Ylos"

    def test_first_double_click_starts_the_server_without_asking_anything(self):
        r = self.wrapper()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.http_status(), 200)
        self.assertEqual(self.opens(), [self.url])
        self.assertEqual(self.dialogs(), "")
        self.assertEqual((self.home / ".ylos" / "repo_path").read_text().strip(), str(REPO))

    def test_double_click_on_a_running_server_offers_open_restart_stop(self):
        self.engine("--detach")
        r = self.wrapper(reply="Open")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = self.dialogs()
        self.assertIn("already running", d)
        self.assertIn("Stop server,Restart,Open Open", d)      # buttons, then the default button
        self.assertEqual(len(self.opens()), 2)

    def test_choice_stop(self):
        self.engine("--detach")
        self.wrapper(reply="Stop server")
        self.assertEqual(self.listeners(), [])
        self.assertIn("is stopped", self.dialogs())

    def test_choice_restart_replaces_the_process(self):
        self.engine("--detach")
        before = self.listeners()
        r = self.wrapper(reply="Restart")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = self.listeners()
        self.assertEqual(len(after), 1)
        self.assertNotEqual(before, after)
        self.assertEqual(self.http_status(), 200)

    def test_escape_changes_nothing(self):
        self.engine("--detach")
        before, opened = self.listeners(), len(self.opens())
        self.wrapper(reply="CANCEL")
        self.assertEqual(self.listeners(), before)
        self.assertEqual(len(self.opens()), opened)

    def test_a_stale_server_proposes_restart_by_default(self):
        self.engine("--detach")
        self.make_stale()
        self.wrapper(reply="CANCEL")
        d = self.dialogs()
        self.assertIn("OLD code", d)
        self.assertIn("Stop server,Restart,Open Restart", d)

    def test_without_working_dialogs_the_double_click_still_opens_the_cockpit(self):
        _write_exec(self.bin / "osascript", "#!/bin/sh\nexit 1\n")
        self.engine("--detach")
        r = self.wrapper()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.opens()), 2)

    def test_a_failed_start_is_explained_in_a_dialog_and_the_log_can_be_opened(self):
        self.foreign_listener()
        r = self.wrapper(reply="Open log")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = self.dialogs()
        self.assertIn("could not start", d)
        self.assertIn("another application", d)
        self.assertIn("Open log,OK", d)
        # the server never got to write a log of its own: the launcher's trace is what opens
        self.assertTrue(any(line.startswith("-t ") and line.endswith("launcher.log")
                            for line in self.opens()), self.opens())

    def test_open_log_prefers_the_server_log_when_the_server_wrote_one(self):
        _write_exec(self.bin / "python3", "#!/bin/sh\necho 'boom: python3 is broken' >&2\nexit 3\n")
        self.wrapper(reply="Open log")
        self.assertIn("boom: python3 is broken", self.dialogs())
        self.assertTrue(any(line.startswith("-t ") and line.endswith("ui-server.log")
                            for line in self.opens()), self.opens())

    def test_a_copied_app_finds_the_repo_it_was_launched_from_before(self):
        (self.home / ".ylos").mkdir()
        (self.home / ".ylos" / "repo_path").write_text(str(REPO) + "\n")
        r = self.wrapper(app=self.relocated_app())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.http_status(), 200)
        self.assertEqual(self.dialogs(), "")

    def test_a_copied_app_asks_for_the_folder_once(self):
        app = self.relocated_app()
        self.wrapper(app=app, folder=str(REPO) + "/")          # osascript returns a trailing slash
        self.assertIn("choose folder", self.dialogs())
        self.assertEqual(self.http_status(), 200)
        self.engine("--stop")
        self.osa_log.unlink()
        self.wrapper(app=app)                                  # remembered: no question this time
        self.assertEqual(self.dialogs(), "")
        self.assertEqual(self.http_status(), 200)

    def test_a_copied_app_that_cannot_find_the_repo_says_so(self):
        r = self.wrapper(app=self.relocated_app(), reply="OK")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot find the YlosPipeline folder", self.dialogs())
        self.assertEqual(self.listeners(), [])


    # -- what only a real macOS launch shows (the Linux runs above cannot see it) ---------------
    def test_the_server_outlives_the_process_group_of_the_app(self):
        # Double-clicked, Ylos.app is a launchd job whose main process is the wrapper script; when
        # it exits, launchd kills what is left in the job's process group. A server that stays in
        # that group dies milliseconds after the browser opens ("connection refused"): invisible on
        # Linux unless the test plays launchd's part.
        job = subprocess.Popen([str(WRAPPER)], env=self.env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
        job.communicate(timeout=90)
        try:
            os.killpg(job.pid, signal.SIGKILL)                 # the job is over: launchd cleans up
        except ProcessLookupError:
            pass                                               # nothing left in the group
        time.sleep(0.5)
        self.assertEqual(len(self.listeners()), 1, "the server died with the app's process group")
        self.assertEqual(self.http_status(), 200)

    def trace(self):
        return (self.home / ".ylos" / "launcher.log").read_text()

    def test_every_double_click_leaves_a_trace(self):
        # No terminal under the Finder: a launcher that fails silently cannot be diagnosed.
        self.wrapper()
        trace = self.trace()
        for needle in (str(WRAPPER), "PATH=", "python3:", "state=stopped", "engine --detach -> rc=0"):
            self.assertIn(needle, trace)

    def test_a_failed_start_is_in_the_trace_too(self):
        self.foreign_listener()
        self.wrapper(reply="OK")
        self.assertIn("rc=1", self.trace())
        self.assertIn("another application", self.trace())

    def test_the_shells_own_complaints_are_kept(self):
        # stderr (osascript's, bash's own syntax errors...) would vanish under the Finder.
        _write_exec(self.bin / "osascript", "#!/bin/sh\necho 'osascript: not authorized' >&2\nexit 1\n")
        self.engine("--detach")
        self.wrapper()
        self.assertIn("osascript: not authorized", self.trace())

    def test_the_trace_stays_small(self):
        ylos = self.home / ".ylos"
        ylos.mkdir()
        (ylos / "launcher.log").write_text("x" * 150_000)
        self.wrapper()
        self.assertTrue((ylos / "launcher.log.1").is_file())
        self.assertLess((ylos / "launcher.log").stat().st_size, 20_000)


    def test_a_folder_macos_protects_is_explained_instead_of_reported_as_a_crash(self):
        # What macOS does to a script-based app whose repo sits in Desktop / Documents / Downloads /
        # iCloud Drive / an external disk: no prompt, bash is simply refused the file ("Operation not
        # permitted"). The real case: a user's ~/.ylos/launcher.log on macOS 27, 2026-10-01.
        repo = self.tmp / "protected"
        repo.mkdir()
        (repo / "ylos_ui.py").write_text("")
        _write_exec(repo / "launch_ui.command",
                    '#!/bin/sh\necho "/bin/bash: $0: Operation not permitted" >&2\nexit 126\n')
        shutil.copytree(APP, repo / "Ylos.app")
        r = self.wrapper(app=repo / "Ylos.app" / "Contents" / "MacOS" / "Ylos", reply="OK")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = self.dialogs()
        self.assertIn("will not let Ylos read", d)
        self.assertIn(str(repo.resolve()), d)
        self.assertIn("~/Developer", d)                        # and what to do about it
        self.assertNotIn("could not start", d)                 # not presented as a server crash
        self.assertIn("Operation not permitted", self.trace())  # the raw error stays in the trace


if __name__ == "__main__":
    unittest.main()
