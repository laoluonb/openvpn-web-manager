from __future__ import annotations

import pathlib
import tempfile
import unittest

from backend.agent import AgentError, parse_index, parse_status, render_profile, validate_client_name


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
                "HEADER,CLIENT_LIST,Common Name,Real Address,Virtual Address,Virtual IPv6 Address,Bytes Received,Bytes Sent,Connected Since,Connected Since (time_t),Username,Client ID,Peer ID,Data Channel Cipher",
                "CLIENT_LIST,ethan-laptop,203.0.113.8:55000,10.8.0.2,,2048,4096,2026-09-11 10:00:00,1789120800,UNDEF,0,0,AES-256-GCM",
                "END",
            ]
        )
        parsed = parse_status(text)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "ethan-laptop")
        self.assertEqual(parsed[0]["virtual_address"], "10.8.0.2")
        self.assertEqual(parsed[0]["bytes_sent"], 4096)
        self.assertEqual(parsed[0]["cipher"], "AES-256-GCM")

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


if __name__ == "__main__":
    unittest.main()
