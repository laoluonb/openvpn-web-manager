from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent


class FrontendRegressionTests(unittest.TestCase):
    def test_javascript_referenced_ids_exist_in_html(self) -> None:
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web/app.js").read_text(encoding="utf-8")
        html_ids = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', html))
        referenced_ids = set(re.findall(r'byId\("([A-Za-z0-9_-]+)"\)', javascript))
        self.assertEqual(set(), referenced_ids - html_ids)

    def test_requested_management_features_are_present(self) -> None:
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web/app.js").read_text(encoding="utf-8")
        server = (ROOT / "backend/server.py").read_text(encoding="utf-8")
        for marker in (
            'id="serverSettingsForm"',
            'id="profileDialog"',
            'id="networkDialog"',
            'id="updateButton"',
            "允许其他 VPN 客户端访问",
        ):
            self.assertIn(marker, html)
        self.assertIn("confirmDisconnect", javascript)
        self.assertIn("viewProfile", javascript)
        self.assertIn('"/api/server/settings"', server)
        self.assertIn('"/api/update"', server)
        self.assertIn('r"/api/clients/([^/]+)/disconnect"', server)
        self.assertIn('r"/api/clients/([^/]+)/network"', server)

    def test_online_update_copy_promises_to_preserve_vpn_port(self) -> None:
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.assertIn("保留现有 VPN 端口和服务端参数", html)
        self.assertIn("现有 VPN 端口和服务端参数不会改变", javascript)
        self.assertNotIn("并重新随机 VPN 端口", javascript)

    def test_offline_branch_is_marked_unreachable(self) -> None:
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.assertIn("客户端离线，内网不可达", javascript)
        self.assertIn("允许 OpenVPN 隧道转发到 LAN", html)


if __name__ == "__main__":
    unittest.main()
