from __future__ import annotations

import time
import unittest

from backend.server import LoginLimiter, SessionManager, hash_password, verify_password


class PasswordTests(unittest.TestCase):
    def test_scrypt_round_trip(self) -> None:
        encoded = hash_password("correct horse battery staple", salt=b"0123456789abcdef")
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong password", encoded))
        self.assertFalse(verify_password("correct horse battery staple", "not-a-hash"))


class SessionTests(unittest.TestCase):
    def test_signed_session_round_trip_and_tamper_rejection(self) -> None:
        manager = SessionManager("a" * 64, hours=1)
        token, issued = manager.issue("admin")
        parsed = manager.parse(token)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["u"], "admin")
        self.assertEqual(parsed["csrf"], issued["csrf"])
        self.assertIsNone(manager.parse(token[:-1] + ("A" if token[-1] != "A" else "B")))

    def test_expired_session_is_rejected(self) -> None:
        manager = SessionManager("b" * 64, hours=1)
        token, _ = manager.issue("admin")
        original = time.time
        try:
            time.time = lambda: original() + 7200
            self.assertIsNone(manager.parse(token))
        finally:
            time.time = original


class LoginLimiterTests(unittest.TestCase):
    def test_limiter_and_success_reset(self) -> None:
        limiter = LoginLimiter(attempts=2, window_seconds=600)
        self.assertFalse(limiter.limited("127.0.0.1"))
        limiter.failure("127.0.0.1")
        limiter.failure("127.0.0.1")
        self.assertTrue(limiter.limited("127.0.0.1"))
        limiter.success("127.0.0.1")
        self.assertFalse(limiter.limited("127.0.0.1"))


if __name__ == "__main__":
    unittest.main()
