"""Exercise the updater with real disposable Git repos and a fake Docker CLI.

No SSH connection, real Docker command, or production checkout is used.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("UPDATE_TEST_BASH") or shutil.which("bash")
if os.name == "nt" and Path("D:/msys64/usr/bin/bash.exe").exists():
    BASH = "D:/msys64/usr/bin/bash.exe"
GIT = shutil.which("git")


@unittest.skipUnless(BASH and GIT, "Bash and Git required")
class UpdateServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="icewag-updater-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.origin = self.base / "origin"
        # Exercise spaces, quotes, and shell metacharacters as literal path data.
        self.server = self.base / "server's project $(not-a-command)"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.log = self.base / "docker.log"
        self.env = os.environ.copy()
        self.env.update({
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Updater test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Updater test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "UPDATE_PROJECT_DIR": self.server.as_posix(),
            "UPDATE_TEST_LOG": self.log.as_posix(),
            "PATH": str(self.bin) + os.pathsep + str(Path(BASH).parent) + os.pathsep + os.environ["PATH"],
        })
        for key in ("COMPOSE_FILE", "UPDATE_BRANCH", "UPDATE_WITH_WATCHDOG", "UPDATE_TEST_FAILURE"):
            self.env.pop(key, None)
        docker = self.bin / "docker"
        docker.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$UPDATE_TEST_LOG"
if [[ "${UPDATE_TEST_FAILURE:-}" == build && " $* " == *' build '* ]]; then exit 17; fi
if [[ " $* " == *' ps --status running --services '* ]]; then printf 'bot\\nwatchdog\\n'; fi
exit 0
""", encoding="utf-8", newline="\n")
        docker.chmod(0o755)
        self.git(self.base, "init", "-b", "main", str(self.origin))
        (self.origin / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        (self.origin / ".gitignore").write_text(".env\n", encoding="utf-8")
        (self.origin / "version.txt").write_text("old\n", encoding="utf-8")
        self.commit(self.origin, "initial")
        self.git(self.base, "clone", str(self.origin), str(self.server))
        self.server_config = "services: {bot: {container_name: icewag-bot}}\n"
        (self.server / "docker-compose.yml").write_text(self.server_config, encoding="utf-8")
        (self.server / ".env").write_text("TEST_SECRET=keep-me\n", encoding="utf-8")
        (self.origin / "version.txt").write_text("new\n", encoding="utf-8")
        self.commit(self.origin, "update")

    def git(self, cwd, *args):
        result = subprocess.run([GIT, *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, repo, message):
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", message)

    def run_update(self):
        return subprocess.run(
            [BASH, str(ROOT / "scripts/update_server.sh")],
            cwd=self.base, env=self.env, capture_output=True, text=True, timeout=30,
        )

    def commands(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def assert_preserved(self):
        self.assertEqual((self.server / "docker-compose.yml").read_text(), self.server_config)
        self.assertEqual((self.server / ".env").read_text(), "TEST_SECRET=keep-me\n")

    def test_update_preserves_configuration_and_only_replaces_bot(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git(self.server, "rev-parse", "HEAD"), self.git(self.origin, "rev-parse", "HEAD"))
        self.assert_preserved()
        commands = self.commands()
        self.assertLess(commands.index("compose build bot"), commands.index("compose up -d --no-build --no-deps bot"))
        self.assertFalse(any("napcat" in c or " down" in c or "prune" in c for c in commands))

    def test_pull_conflict_stops_before_build_and_preserves_edits(self):
        (self.origin / "docker-compose.yml").write_text("services: {bot: {image: changed}}\n")
        self.commit(self.origin, "conflicting compose update")
        before = self.git(self.server, "rev-parse", "HEAD")
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.server, "rev-parse", "HEAD"), before)
        self.assertFalse(any(" build " in c or " up " in c for c in self.commands()))
        self.assert_preserved()
        self.assertEqual(self.git(self.server, "stash", "list"), "")

    def test_diverged_branch_is_not_merged_or_rebased(self):
        (self.server / "local.txt").write_text("server work\n")
        self.git(self.server, "add", "local.txt")
        self.git(self.server, "commit", "-m", "local work")
        before = self.git(self.server, "rev-parse", "HEAD")
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.server, "rev-parse", "HEAD"), before)
        self.assertFalse(any(" build " in c or " up " in c for c in self.commands()))

    def test_build_failure_never_replaces_running_container(self):
        self.env["UPDATE_TEST_FAILURE"] = "build"
        result = self.run_update()
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertFalse(any(" up " in c for c in self.commands()))
        self.assert_preserved()

    def test_wrong_branch_stops_without_switching(self):
        self.git(self.server, "switch", "-c", "server-work")
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.server, "branch", "--show-current"), "server-work")
        self.assertEqual(self.commands(), [])

    def test_watchdog_opt_in_preserves_compose_override(self):
        self.env["UPDATE_WITH_WATCHDOG"] = "1"
        (self.origin / "docker-compose.alerts.yml").write_text("services: {watchdog: {}}\n")
        self.commit(self.origin, "watchdog")
        override = self.server / "docker-compose.override.yml"
        override.write_text("services: {bot: {container_name: custom-bot}}\n")
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        command = next(c for c in self.commands() if " up " in c)
        self.assertIn("-f docker-compose.override.yml", command)
        self.assertTrue(command.endswith("--no-deps bot watchdog"))
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
