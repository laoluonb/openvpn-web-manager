from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent


class InstallerRegressionTests(unittest.TestCase):
    def test_installer_loads_only_its_own_sysctl_file(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn("sysctl --system", installer)
        self.assertIn("sysctl -q -p /etc/sysctl.d/99-openvpn-manager.conf", installer)

    def test_uninstaller_does_not_reload_all_host_sysctls(self) -> None:
        uninstaller = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
        self.assertNotIn("sysctl --system", uninstaller)

    def test_existing_openvpn_actions_are_supported(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("--existing-action", installer)
        self.assertIn("preserve|remove|abort", installer)
        self.assertIn("backup_existing_openvpn", installer)
        self.assertIn("remove_existing_openvpn_state", installer)
        self.assertIn("OPENVPN_CONFIG_PRESENT", installer)


if __name__ == "__main__":
    unittest.main()
