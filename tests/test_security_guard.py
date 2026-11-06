"""Static tests for the explicit-authorization stack guard."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GuardSourceTests(unittest.TestCase):
    def test_control_script_has_authorization_and_guard_paths(self):
        text = (ROOT / "scripts" / "lxqt-stack").read_text()
        for needle in (
            "authorize", "revoke", "guard", "start_stack", "stop_stack",
            "docker update --restart=no", "docker stop", "AUTH=",
            "unauthorized stack start detected",
        ):
            self.assertIn(needle, text)

    def test_user_unit_uses_installed_guard(self):
        text = (ROOT / "systemd" / "lxqt-stack-guard.service").read_text()
        self.assertIn("ExecStart=%h/.local/bin/lxqt-stack guard", text)
        self.assertIn("WantedBy=default.target", text)
        self.assertIn("Restart=always", text)

    def test_system_template_is_boot_level(self):
        text = (ROOT / "systemd" / "lxqt-stack-guard-system.service.in").read_text()
        self.assertIn("WantedBy=multi-user.target", text)
        self.assertIn("User=@USER@", text)
        self.assertIn("Requires=docker.service", text)

    def test_installer_enables_and_lingers(self):
        text = (ROOT / "scripts" / "install-guard.sh").read_text()
        self.assertIn("systemctl --user enable --now", text)
        self.assertIn("loginctl enable-linger", text)
        self.assertIn("--system", text)
        self.assertIn("install -m 0644", text)

    def test_deploy_and_install_all_are_wired(self):
        deploy = (ROOT / "scripts" / "deploy.sh").read_text()
        self.assertIn('lxqt-stack" authorize "deploy.sh"', deploy)
        installer = (ROOT / "scripts" / "install-all.sh").read_text()
        self.assertIn("install-guard.sh", installer)
        test_runner = (ROOT / "scripts" / "test.sh").read_text()
        self.assertIn("lxqt-stack", test_runner)
        self.assertIn("systemd", test_runner)

    def test_shell_syntax(self):
        for name in ("lxqt-stack", "install-guard.sh", "deploy.sh", "install-all.sh", "test.sh"):
            result = subprocess.run(
                ["bash", "-n", str(ROOT / "scripts" / name)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
