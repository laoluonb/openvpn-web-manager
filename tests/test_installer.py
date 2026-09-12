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

    def test_runtime_sync_management_socket_and_online_updater_are_installed(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        override = (ROOT / "config/openvpn-service-override.conf").read_text(encoding="utf-8")
        agent_service = (ROOT / "config/openvpn-manager-agent.service").read_text(encoding="utf-8")
        tmpfiles = (ROOT / "config/openvpn-manager.tmpfiles").read_text(encoding="utf-8")
        updater = (ROOT / "scripts/openvpn-manager-update").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION="1.2.11"', installer)
        self.assertNotIn('\nVERSION="1.2.11"', installer)
        self.assertIn('"$APP_VERSION" "$EXISTING_ACTION"', installer)
        self.assertIn('CLI_COMMAND="/usr/local/bin/openvpn-manager"', installer)
        self.assertIn("cleanup_legacy_cli_commands", installer)
        self.assertNotIn("install_cli_alias", installer)
        self.assertIn('agent.py" --direct sync_runtime', installer)
        self.assertIn("openvpn-manager-update.service", installer)
        self.assertIn('"update_check_path": "/var/lib/openvpn-manager/update-check.json"', installer)
        self.assertIn('config/openvpn-manager.tmpfiles" /etc/tmpfiles.d/openvpn-manager.conf', installer)
        self.assertIn("systemd-tmpfiles --create /etc/tmpfiles.d/openvpn-manager.conf", installer)
        self.assertIn("RuntimeDirectory=", override)
        self.assertNotIn("RuntimeDirectory=openvpn-manager", override)
        self.assertNotIn("RuntimeDirectory=openvpn-manager", agent_service)
        self.assertIn("d /run/openvpn-manager 0750 root openvpn-web -", tmpfiles)
        self.assertIn("api.github.com/repos/$REPOSITORY/releases/latest", updater)
        self.assertIn('export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"', updater)
        self.assertIn('/bin/bash "$SOURCE_DIR/install.sh"', updater)
        self.assertIn('INSTALL_LOG="$WORK_DIR/install.log"', updater)
        self.assertIn('2>&1 | tee "$INSTALL_LOG"', updater)
        self.assertIn('INSTALL_EXIT_CODE=${PIPESTATUS[0]}', updater)
        self.assertIn('FAILURE_DETAIL=', updater)
        self.assertIn("--existing-action preserve", updater)
        self.assertNotIn('data.get("vpn_port", 1194)', updater)
        self.assertNotIn('--vpn-port "${SETTINGS[1]}"', updater)
        self.assertNotIn("--vpn-port random", updater)
        self.assertNotIn("--vpn-protocol", updater)
        self.assertNotIn("--vpn-subnet", updater)
        self.assertIn("更新包包含不允许的特殊文件", updater)

    def test_managed_upgrade_preserves_unset_server_parameters_and_port(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("load_preserved_server_settings", installer)
        self.assertIn('VPN_PORT="random"', installer)
        self.assertIn("generate_random_vpn_port", installer)
        self.assertIn("10000 + secrets.randbelow(20000)", installer)
        self.assertIn('OLD_VPN_PORT="${values[1]}"', installer)
        self.assertIn("port == web_port or port == previous_port", installer)
        self.assertIn('[[ "$VPN_PORT_EXPLICIT" == "1" ]] || VPN_PORT="${values[1]}"', installer)
        self.assertIn('VPN_PORT_EXPLICIT="0"', installer)
        self.assertIn('-n "${INVOCATION_ID:-}"', installer)
        self.assertIn('"$STATE_DIR/update-request.json"', installer)
        self.assertIn("检测到旧版在线更新调用", installer)
        self.assertIn('[[ "$VPN_SUBNET_EXPLICIT" == "1" ]] || VPN_SUBNET=', installer)
        self.assertIn('[[ "$WEB_PORT_EXPLICIT" == "1" ]] || WEB_PORT=', installer)
        self.assertIn('OLD_VPN_SUBNET="${values[3]}"', installer)
        self.assertIn("已清理旧客户端地址池记录", installer)

    def test_online_updater_refuses_release_downgrades(self) -> None:
        updater = (ROOT / "scripts/openvpn-manager-update").read_text(encoding="utf-8")
        self.assertIn("dpkg --compare-versions", updater)
        self.assertIn("已拒绝降级", updater)
        self.assertIn("re.fullmatch", updater)
        self.assertIn('version = str(data.get("version", "")).strip().removeprefix("v")', updater)

    def test_agent_can_reset_openvpn_address_pool(self) -> None:
        service = (ROOT / "config/openvpn-manager-agent.service").read_text(encoding="utf-8")
        self.assertIn("/var/lib/openvpn/server", service)

    def test_ccd_is_readable_after_openvpn_drops_privileges(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        agent = (ROOT / "backend/agent.py").read_text(encoding="utf-8")
        self.assertIn('install -d -m 0750 -o root -g nogroup "$OPENVPN_DIR/ccd"', installer)
        self.assertIn('install -d -m 0751 "$OPENVPN_DIR"', installer)
        self.assertIn("stat.S_IXOTH", agent)
        self.assertIn('os.chown(ccd_dir, 0, group_gid("nogroup"))', agent)
        self.assertIn('mode=0o640, group_name="nogroup"', agent)

    def test_firewall_uses_dedicated_chains_for_client_lan_policy(self) -> None:
        firewall = (ROOT / "scripts/openvpn-manager-firewall").read_text(encoding="utf-8")
        self.assertIn('FORWARD_CHAIN="OVPNMGR_FORWARD"', firewall)
        self.assertIn("append_client_route_policy", firewall)
        self.assertIn('case "$mode" in', firewall)
        self.assertIn('"$TAG-client-lan-deny"', firewall)
        self.assertIn('CLIENT_ROUTES_FILE', firewall)

    def test_updater_service_is_not_stopped_as_an_openvpn_daemon(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('$1 == "openvpn.service"', installer)
        self.assertIn('/^openvpn-server@.*\\.service$/', installer)
        stop_function = installer[installer.index("stop_existing_openvpn_units()") : installer.index("backup_existing_openvpn()")]
        self.assertNotIn("openvpn-manager-update.service", stop_function)

    def test_agent_stays_running_when_openvpn_is_restarted(self) -> None:
        service = (ROOT / "config/openvpn-manager-agent.service").read_text(encoding="utf-8")
        self.assertIn("Wants=network-online.target openvpn-server@server.service", service)
        self.assertNotIn("Requires=openvpn-server@server.service", service)

    def test_agent_repairs_shared_runtime_directory_permissions(self) -> None:
        agent = (ROOT / "backend/agent.py").read_text(encoding="utf-8")
        self.assertIn("os.chown(socket_file.parent, 0, group_id)", agent)
        self.assertIn("os.chmod(socket_file.parent, 0o750)", agent)

    def test_uninstaller_removes_shared_runtime_directory_definition(self) -> None:
        uninstaller = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
        self.assertIn("/etc/tmpfiles.d/openvpn-manager.conf", uninstaller)
        self.assertIn("rm -rf /run/openvpn-manager", uninstaller)

    def test_only_openvpn_manager_is_installed_as_public_cli(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        uninstaller = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn('CLI_COMMAND="/usr/local/bin/openvpn-manager"', installer)
        self.assertIn('scripts/openvpn-manager" "$CLI_COMMAND"', installer)
        self.assertNotIn("install_cli_alias", installer)
        self.assertNotIn("sudo openvpn-managerctl", readme)
        self.assertNotIn("sudo bt", readme)
        self.assertIn('LEGACY_CLI_COMMAND="/usr/local/bin/openvpn-managerctl"', uninstaller)
        self.assertIn('LEGACY_CLI_ALIAS="/usr/local/bin/bt"', uninstaller)

    def test_cli_can_view_and_change_dashboard_credentials(self) -> None:
        cli = (ROOT / "scripts/openvpn-manager").read_text(encoding="utf-8")
        agent = (ROOT / "backend/agent.py").read_text(encoding="utf-8")
        self.assertIn('sub.add_parser("web-settings"', cli)
        self.assertIn('sub.add_parser("web-credentials"', cli)
        self.assertIn('getpass.getpass("新面板密码（留空表示不修改）：")', cli)
        self.assertIn('"action": "set_web_credentials"', cli)
        self.assertIn('if action == "web_settings"', agent)
        self.assertIn('if action == "set_web_credentials"', agent)
        self.assertIn('self.run(["systemctl", "restart", "openvpn-web-manager.service"]', agent)
        self.assertIn('web_config["session_secret"] = os.urandom(32).hex()', agent)
        self.assertIn("密码：已修改，出于安全原因未保存明文", agent)


if __name__ == "__main__":
    unittest.main()
