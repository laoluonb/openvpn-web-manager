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


if __name__ == "__main__":
    unittest.main()
