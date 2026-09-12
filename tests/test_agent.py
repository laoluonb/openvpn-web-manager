from __future__ import annotations

import pathlib
import tempfile
import unittest

from backend.agent import (
    AgentError,
    client_artifact_entries,
    filter_active_clients,
    filter_client_log_lines,
    normalize_client_network,
    normalize_server_settings,
    parse_index,
    parse_status,
    render_ccd_configs,
    render_profile,
    render_server_config,
    validate_client_name,
)


class AgentParsingTests(unittest.TestCase):
    def test_client_name_validation(self) -> None:
        self.assertEqual(validate_client_name("ethan-phone_2"), "ethan-phone_2")
        for value in ("../root", "white space", "server", "", "x" * 33):
            with self.subTest(value=value), self.assertRaises(AgentError):
                validate_client_name(value)

    def test_status_version_three(self) -> None:
        text = "\n".join(
            [
                "TITLE,OpenVPN 2.6",
                "HEADER\tCLIENT_LIST\tCommon Name\tReal Address\tVirtual Address\tVirtual IPv6 Address\tBytes Received\tBytes Sent\tConnected Since\tConnected Since (time_t)\tUsername\tClient ID\tPeer ID\tData Channel Cipher",
                "CLIENT_LIST\tethan-laptop\t203.0.113.8:55000\t10.8.0.2\t\t2048\t4096\t2026-09-11 10:00:00\t1789120800\tUNDEF\t0\t0\tAES-256-GCM",
                "END",
            ]
        )
        parsed = parse_status(text)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "ethan-laptop")
        self.assertEqual(parsed[0]["virtual_address"], "10.8.0.2")
        self.assertEqual(parsed[0]["bytes_sent"], 4096)
        self.assertEqual(parsed[0]["cipher"], "AES-256-GCM")

    def test_status_parser_keeps_legacy_comma_format(self) -> None:
        text = "\n".join(
            [
                "HEADER,CLIENT_LIST,Common Name,Real Address,Virtual Address,Virtual IPv6 Address,Bytes Received,Bytes Sent,Connected Since,Client ID,Peer ID,Data Channel Cipher",
                "CLIENT_LIST,legacy-client,198.51.100.8:1194,10.8.0.9,,12,34,2026-09-12 01:00:00,1,2,AES-256-GCM",
            ]
        )
        parsed = parse_status(text)
        self.assertEqual(parsed[0]["name"], "legacy-client")
        self.assertEqual(parsed[0]["virtual_address"], "10.8.0.9")
        self.assertEqual(parsed[0]["bytes_received"], 12)

    def test_easy_rsa_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = pathlib.Path(directory)
            (profiles / "alice.ovpn").write_text("client\n", encoding="utf-8")
            text = "\n".join(
                [
                    "V\t290911120000Z\t\t01\tunknown\t/CN=server",
                    "V\t290911120000Z\t\t02\tunknown\t/CN=alice",
                    "R\t290911120000Z\t260911120000Z\t03\tunknown\t/CN=old-phone",
                ]
            )
            online = {"alice": {"virtual_address": "10.8.0.2"}}
            parsed = parse_index(text, profiles, online)
            self.assertEqual([item["name"] for item in parsed], ["alice", "old-phone"])
            self.assertTrue(parsed[0]["online"])
            self.assertTrue(parsed[0]["has_profile"])
            self.assertEqual(parsed[1]["status"], "revoked")

    def test_management_views_hide_revoked_and_expired_clients(self) -> None:
        clients = [
            {"name": "active", "status": "active"},
            {"name": "revoked", "status": "revoked"},
            {"name": "expired", "status": "expired"},
        ]
        self.assertEqual([item["name"] for item in filter_active_clients(clients)], ["active"])

    def test_client_log_filter_matches_exact_common_name(self) -> None:
        lines = [
            "client-a/203.0.113.1:1000 Peer Connection Initiated",
            "client-ab/203.0.113.2:1001 Peer Connection Initiated",
            "CN=client-a authentication succeeded",
        ]
        self.assertEqual(filter_client_log_lines(lines, "client-a"), [lines[0], lines[2]])

    def test_revoked_artifacts_do_not_include_pki_index_or_crl(self) -> None:
        entries = client_artifact_entries(
            pathlib.Path("/etc/openvpn/server/easy-rsa"),
            pathlib.Path("/var/lib/openvpn-manager/clients"),
            "alice",
            "0A",
        )
        paths = {path.as_posix() for _label, path in entries}
        self.assertIn("/var/lib/openvpn-manager/clients/alice.ovpn", paths)
        self.assertIn("/etc/openvpn/server/easy-rsa/pki/issued/alice.crt", paths)
        self.assertIn("/etc/openvpn/server/easy-rsa/pki/private/alice.key", paths)
        self.assertIn("/etc/openvpn/server/easy-rsa/pki/certs_by_serial/0A.pem", paths)
        self.assertIn("/etc/openvpn/server/easy-rsa/pki/revoked/certs_by_serial/0A.crt", paths)
        self.assertIn("/etc/openvpn/server/easy-rsa/pki/revoked/private_by_serial/0A.key", paths)
        self.assertIn("/etc/openvpn/server/easy-rsa/pki/revoked/reqs_by_serial/0A.req", paths)
        self.assertNotIn("/etc/openvpn/server/easy-rsa/pki/index.txt", paths)
        self.assertNotIn("/etc/openvpn/server/easy-rsa/pki/crl.pem", paths)

    def test_remove_client_artifacts_deletes_exact_files(self) -> None:
        from backend.agent import remove_client_artifacts

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            easy = root / "easy-rsa"
            pki = easy / "pki"
            profiles = root / "clients"
            for path in (
                profiles / "alice.ovpn",
                pki / "issued" / "alice.crt",
                pki / "private" / "alice.key",
                pki / "reqs" / "alice.req",
                pki / "certs_by_serial" / "0A.pem",
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("secret", encoding="utf-8")
            # Similar names must survive an exact-name cleanup.
            (profiles / "alice-old.ovpn").write_text("keep", encoding="utf-8")
            (pki / "issued" / "alice-old.crt").write_text("keep", encoding="utf-8")
            removed = remove_client_artifacts(easy, profiles, "alice", "0A")
            self.assertGreaterEqual(len(removed), 5)
            self.assertFalse((profiles / "alice.ovpn").exists())
            self.assertFalse((pki / "issued" / "alice.crt").exists())
            self.assertFalse((pki / "private" / "alice.key").exists())
            self.assertFalse((pki / "reqs" / "alice.req").exists())
            self.assertFalse((pki / "certs_by_serial" / "0A.pem").exists())
            self.assertTrue((profiles / "alice-old.ovpn").exists())
            self.assertTrue((pki / "issued" / "alice-old.crt").exists())


class ProfileRenderingTests(unittest.TestCase):
    def test_profile_contains_required_embedded_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            easy = root / "easy-rsa"
            openvpn = root / "openvpn"
            (easy / "pki" / "issued").mkdir(parents=True)
            (easy / "pki" / "private").mkdir(parents=True)
            openvpn.mkdir()
            cert = "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n"
            key = "-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----\n"
            tls = "-----BEGIN OpenVPN Static key V1-----\nTEST\n-----END OpenVPN Static key V1-----\n"
            (easy / "pki" / "ca.crt").write_text(cert, encoding="utf-8")
            (easy / "pki" / "issued" / "alice.crt").write_text("metadata\n" + cert, encoding="utf-8")
            (easy / "pki" / "private" / "alice.key").write_text(key, encoding="utf-8")
            (openvpn / "tls-crypt.key").write_text(tls, encoding="utf-8")
            profile = render_profile(
                {
                    "easy_rsa_dir": str(easy),
                    "openvpn_dir": str(openvpn),
                    "endpoint": "vpn.example.com",
                    "vpn_port": 1194,
                    "vpn_protocol": "udp",
                    "server_name": "server",
                },
                "alice",
            )
            self.assertIn("remote vpn.example.com 1194", profile)
            self.assertIn("verify-x509-name server name", profile)
            self.assertIn("<tls-crypt>", profile)
            self.assertEqual(profile.count("-----BEGIN CERTIFICATE-----"), 2)

    def test_tcp_profile_uses_tcp_client_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            easy = root / "easy-rsa"
            openvpn = root / "openvpn"
            (easy / "pki" / "issued").mkdir(parents=True)
            (easy / "pki" / "private").mkdir(parents=True)
            openvpn.mkdir()
            cert = "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n"
            key = "-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----\n"
            tls = "-----BEGIN OpenVPN Static key V1-----\nTEST\n-----END OpenVPN Static key V1-----\n"
            (easy / "pki" / "ca.crt").write_text(cert, encoding="utf-8")
            (easy / "pki" / "issued" / "router.crt").write_text(cert, encoding="utf-8")
            (easy / "pki" / "private" / "router.key").write_text(key, encoding="utf-8")
            (openvpn / "tls-crypt.key").write_text(tls, encoding="utf-8")
            profile = render_profile(
                {
                    "easy_rsa_dir": str(easy),
                    "openvpn_dir": str(openvpn),
                    "endpoint": "vpn.example.com",
                    "vpn_port": 443,
                    "vpn_protocol": "tcp",
                },
                "router",
            )
            self.assertIn("proto tcp-client", profile)


class ServerConfigurationTests(unittest.TestCase):
    def test_server_settings_are_normalized_and_restricted(self) -> None:
        current = {
            "endpoint": "vpn.example.com",
            "vpn_port": 1194,
            "vpn_protocol": "udp",
            "vpn_subnet": "10.8.0.0/24",
        }
        settings = normalize_server_settings(
            current,
            {
                "endpoint": "edge.example.com",
                "vpn_port": 443,
                "vpn_protocol": "tcp",
                "vpn_subnet": "10.20.0.7/24",
                "dns_servers": ["1.1.1.1", "1.1.1.1", "9.9.9.9"],
                "redirect_gateway": False,
                "max_clients": 250,
            },
        )
        self.assertEqual(settings["vpn_subnet"], "10.20.0.0/24")
        self.assertEqual(settings["dns_servers"], ["1.1.1.1", "9.9.9.9"])
        self.assertEqual(settings["vpn_protocol"], "tcp")
        self.assertFalse(settings["redirect_gateway"])
        with self.assertRaises(AgentError):
            normalize_server_settings(current, {"vpn_subnet": "8.8.8.0/24"})
        with self.assertRaises(AgentError):
            normalize_server_settings({**current, "web_port": 8443}, {"vpn_port": 8443})

    def test_client_network_rejects_overlap(self) -> None:
        existing = {"branch-a": {"lan_subnet": "192.168.10.0/24", "share_lan": True}}
        with self.assertRaises(AgentError):
            normalize_client_network("10.8.0.0/24", True, "10.8.0.0/24", existing, "branch-b")
        with self.assertRaises(AgentError):
            normalize_client_network("192.168.10.128/25", False, "10.8.0.0/24", existing, "branch-b")
        route = normalize_client_network("192.168.20.9/24", True, "10.8.0.0/24", existing, "branch-b")
        self.assertEqual(route, {"lan_subnet": "192.168.20.0/24", "share_lan": True})

    def test_server_config_renders_shared_and_private_client_lans(self) -> None:
        config = {
            "endpoint": "vpn.example.com",
            "vpn_port": 1194,
            "vpn_protocol": "udp",
            "vpn_subnet": "10.8.0.0/24",
            "dns_servers": ["1.1.1.1"],
            "redirect_gateway": True,
            "max_clients": 100,
            "openvpn_dir": "/etc/openvpn/server",
            "status_file": "/var/log/openvpn/status.log",
            "management_socket": "/run/openvpn-manager/openvpn.sock",
        }
        rendered = render_server_config(
            config,
            {
                "branch-a": {"lan_subnet": "192.168.10.0/24", "share_lan": True},
                "branch-b": {"lan_subnet": "192.168.20.0/24", "share_lan": False},
            },
        )
        self.assertIn("route 192.168.10.0 255.255.255.0", rendered)
        self.assertNotIn('push "route 192.168.10.0 255.255.255.0"', rendered)
        self.assertIn("route 192.168.20.0 255.255.255.0", rendered)
        self.assertNotIn('push "route 192.168.20.0 255.255.255.0"', rendered)
        self.assertIn("management /run/openvpn-manager/openvpn.sock unix", rendered)
        self.assertIn("management-client-user root", rendered)
        self.assertNotIn("client-to-client", rendered)

    def test_ccd_pushes_shared_lan_only_to_other_clients(self) -> None:
        rendered = render_ccd_configs(
            {
                "branch-a": {"lan_subnet": "192.168.10.0/24", "share_lan": True},
                "branch-b": {"lan_subnet": "192.168.20.0/24", "share_lan": False},
            },
            ["branch-a", "branch-b", "phone"],
        )
        self.assertIn("iroute 192.168.10.0 255.255.255.0", rendered["branch-a"])
        self.assertNotIn('push "route 192.168.10.0 255.255.255.0"', rendered["branch-a"])
        self.assertIn('push "route 192.168.10.0 255.255.255.0"', rendered["branch-b"])
        self.assertIn('push "route 192.168.10.0 255.255.255.0"', rendered["phone"])
        self.assertIn("iroute 192.168.20.0 255.255.255.0", rendered["branch-b"])
        self.assertNotIn('push "route 192.168.20.0 255.255.255.0"', rendered["phone"])


if __name__ == "__main__":
    unittest.main()
