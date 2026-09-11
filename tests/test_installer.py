from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent


class InstallerRegressionTests(unittest.TestCase):
    def test_runtime_components_use_canonical_manager_paths(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('CONFIG_DIR="/etc/openvpn-manager"', installer)
        self.assertIn('STATE_DIR="/var/lib/openvpn-manager"', installer)

        config_consumers = (
            "config/nginx-site.conf.tpl",
            "config/openvpn-manager-agent.service",
            "config/openvpn-web-manager.service",
            "backend/agent.py",
            "backend/server.py",
            "scripts/openvpn-manager-firewall",
        )
        for relative_path in config_consumers:
            with self.subTest(path=relative_path):
                content = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("/etc/openvpn-manager", content)
                self.assertNotIn("/etc/openvpn-web-manager", content)

        state_consumers = (
            "config/openvpn-manager-agent.service",
            "config/openvpn-web-manager.service",
        )
        for relative_path in state_consumers:
            with self.subTest(path=relative_path):
                content = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("/var/lib/openvpn-manager", content)
                self.assertNotIn("/var/lib/openvpn-web-manager", content)

    def test_v111_legacy_paths_are_migrated(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('LEGACY_CONFIG_DIR="/etc/openvpn-web-manager"', installer)
        self.assertIn('LEGACY_STATE_DIR="/var/lib/openvpn-web-manager"', installer)
        self.assertIn("migrate_legacy_manager_paths", installer)
        self.assertIn('cp -a --no-clobber "$source/." "$destination/"', installer)
        self.assertIn(
            'rm -rf "$CONFIG_DIR" "$STATE_DIR" "$LEGACY_CONFIG_DIR" "$LEGACY_STATE_DIR"',
            installer,
        )
        self.assertIn('-s "$LEGACY_CONFIG_DIR/web.json"', installer)
        self.assertIn('-s "$LEGACY_CONFIG_DIR/tls/server.crt"', installer)

    def test_legacy_paths_only_appear_as_migration_constants(self) -> None:
        allowed = {
            "install.sh": {
                'LEGACY_CONFIG_DIR="/etc/openvpn-web-manager"',
                'LEGACY_STATE_DIR="/var/lib/openvpn-web-manager"',
            },
            "uninstall.sh": {
                'LEGACY_CONFIG_DIR="/etc/openvpn-web-manager"',
                'LEGACY_STATE_DIR="/var/lib/openvpn-web-manager"',
            },
        }
        for relative_path, expected_lines in allowed.items():
            content = (ROOT / relative_path).read_text(encoding="utf-8")
            legacy_lines = {
                line.strip()
                for line in content.splitlines()
                if "/etc/openvpn-web-manager" in line
                or "/var/lib/openvpn-web-manager" in line
            }
            self.assertEqual(expected_lines, legacy_lines)

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

    def test_credentials_are_saved_before_sysctl_and_services(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        credential_write = installer.index('cat >"$CREDENTIAL_FILE"')
        sysctl_apply = installer.index("sysctl -q -p /etc/sysctl.d/99-openvpn-manager.conf")
        service_install = installer.index('install -m 0644 "$SCRIPT_DIR/config/openvpn-manager-firewall.service"')
        self.assertLess(credential_write, sysctl_apply)
        self.assertLess(credential_write, service_install)


if __name__ == "__main__":
    unittest.main()
