#!/usr/bin/env python3
"""Unprivileged HTTP API and local demo server for OpenVPN Web Manager."""

from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import hashlib
import hmac
import http.cookies
import http.server
import json
import logging
import mimetypes
import os
import pathlib
import re
import secrets
import socket
import threading
import time
import urllib.parse
from typing import Any

try:
    from .agent import filter_active_clients
except ImportError:  # Allow `python3 backend/server.py --demo` from the project root.
    from agent import filter_active_clients


CONFIG_PATH = os.environ.get("OPENVPN_MANAGER_CONFIG", "/etc/openvpn-manager/server.json")
WEB_CONFIG_PATH = os.environ.get("OPENVPN_MANAGER_WEB_CONFIG", "/etc/openvpn-manager/web.json")
SOCKET_PATH = os.environ.get("OPENVPN_MANAGER_SOCKET", "/run/openvpn-manager/agent.sock")
STATIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"
CLIENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
MAX_BODY = 32_768
LOGGER = logging.getLogger("openvpn-web-manager")


class APIError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("密码不能为空")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${b64encode(salt)}${b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_value, r_value, p_value, salt_value, expected_value = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = b64decode(salt_value)
        expected = b64decode(expected_value)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_value),
            r=int(r_value),
            p=int(p_value),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def load_json(path: str | pathlib.Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


class SessionManager:
    def __init__(self, secret: str, hours: int = 8):
        self.secret = secret.encode("utf-8")
        self.lifetime = max(1, min(int(hours), 72)) * 3600

    def issue(self, username: str) -> tuple[str, dict[str, Any]]:
        payload = {
            "u": username,
            "exp": int(time.time()) + self.lifetime,
            "csrf": secrets.token_urlsafe(24),
        }
        encoded = b64encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = b64encode(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}", payload

    def parse(self, token: str) -> dict[str, Any] | None:
        try:
            encoded, supplied = token.split(".", 1)
            expected = b64encode(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                return None
            payload = json.loads(b64decode(encoded))
            if not isinstance(payload, dict) or int(payload.get("exp", 0)) < int(time.time()):
                return None
            if not isinstance(payload.get("u"), str) or not isinstance(payload.get("csrf"), str):
                return None
            return payload
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            return None


class LoginLimiter:
    def __init__(self, attempts: int = 5, window_seconds: int = 600):
        self.attempts = attempts
        self.window = window_seconds
        self.entries: dict[str, collections.deque[float]] = {}
        self.lock = threading.Lock()

    def limited(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            queue = self.entries.setdefault(key, collections.deque())
            while queue and now - queue[0] > self.window:
                queue.popleft()
            return len(queue) >= self.attempts

    def failure(self, key: str) -> None:
        with self.lock:
            self.entries.setdefault(key, collections.deque()).append(time.monotonic())

    def success(self, key: str) -> None:
        with self.lock:
            self.entries.pop(key, None)


class AgentClient:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def request(self, payload: dict[str, Any]) -> Any:
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(130)
                client.connect(self.socket_path)
                client.sendall(encoded)
                chunks: list[bytes] = []
                total = 0
                while True:
                    block = client.recv(65536)
                    if not block:
                        break
                    total += len(block)
                    if total > 512_000:
                        raise APIError("控制代理返回的数据过大", 502)
                    chunks.append(block)
                    if b"\n" in block:
                        break
        except (OSError, TimeoutError) as exc:
            raise APIError(f"控制代理当前不可用：{exc}", 503) from exc
        try:
            response = json.loads(b"".join(chunks).split(b"\n", 1)[0])
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise APIError("控制代理返回了无效响应", 502) from exc
        if not response.get("ok"):
            raise APIError(str(response.get("error", "控制操作失败")), 409)
        return response.get("data")


class DemoAgent:
    def __init__(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.lock = threading.Lock()
        self.settings = {
            "endpoint": "vpn.example.com",
            "vpn_port": 1194,
            "vpn_protocol": "udp",
            "vpn_subnet": "10.8.0.0/24",
            "dns_servers": ["1.1.1.1", "9.9.9.9"],
            "redirect_gateway": True,
            "max_clients": 100,
            "web_port": 9090,
            "web_allow": "127.0.0.1/32",
            "tun_mtu": 1500,
            "mssfix": 1450,
            "keepalive_ping": 10,
            "keepalive_timeout": 120,
            "data_cipher": "AES-256-GCM",
            "auth_digest": "SHA256",
            "log_verb": 3,
            "push_routes": [],
        }
        self.update_state = {
            "state": "idle",
            "message": "演示环境尚未执行在线更新",
            "manager_version": "1.2.7-demo",
            "openvpn_version": "OpenVPN 2.6 demo",
        }
        self.update_check = {
            "current_manager_version": "1.2.7-demo",
            "latest_manager_version": "v1.2.7-demo",
            "current_openvpn_version": "2.6 demo",
            "installed_openvpn_version": "2.6 demo",
            "candidate_openvpn_version": "2.6 demo",
            "manager_update_available": False,
            "openvpn_update_available": False,
            "update_available": False,
            "release_name": "v1.2.7：更新检查与发布信息",
            "release_url": "https://github.com/laoluonb/openvpn-web-manager/releases/tag/v1.2.7",
            "release_published_at": "2026-09-12T00:00:00Z",
            "release_notes": "新增当前版本、远程版本、OpenVPN 软件源候选版本和 GitHub 更新日志展示。\n新增更新可用提示，并继续保留 VPN 端口和服务端参数。",
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self.clients = [
            {
                "name": "ethan-laptop",
                "status": "active",
                "serial": "01",
                "expires_at": (now + dt.timedelta(days=825)).isoformat().replace("+00:00", "Z"),
                "revoked_at": None,
                "created_at": (now - dt.timedelta(days=18)).isoformat().replace("+00:00", "Z"),
                "has_profile": True,
                "online": True,
                "lan_subnet": "192.168.50.0/24",
                "share_lan": True,
                "connection": {
                    "real_address": "203.0.113.42:52881",
                    "virtual_address": "10.8.0.2",
                    "bytes_received": 2_486_200,
                    "bytes_sent": 8_932_410,
                    "connected_since": "今天 08:41",
                    "cipher": "AES-256-GCM",
                },
            },
            {
                "name": "phone",
                "status": "active",
                "serial": "02",
                "expires_at": (now + dt.timedelta(days=900)).isoformat().replace("+00:00", "Z"),
                "revoked_at": None,
                "created_at": (now - dt.timedelta(days=4)).isoformat().replace("+00:00", "Z"),
                "has_profile": True,
                "online": False,
                "lan_subnet": None,
                "share_lan": False,
                "connection": None,
            },
            {
                "name": "old-tablet",
                "status": "revoked",
                "serial": "03",
                "expires_at": (now + dt.timedelta(days=300)).isoformat().replace("+00:00", "Z"),
                "revoked_at": (now - dt.timedelta(days=2)).isoformat().replace("+00:00", "Z"),
                "created_at": None,
                "has_profile": False,
                "online": False,
                "lan_subnet": None,
                "share_lan": False,
                "connection": None,
            },
        ]

    def _status(self) -> dict[str, Any]:
        online = [item for item in self.clients if item["online"]]
        return {
            "service": "active",
            "healthy": True,
            "online_count": len(online),
            "online_clients": [item["connection"] for item in online],
            **self.settings,
            "manager_version": "1.2.7-demo",
            "version": "OpenVPN 2.6 demo",
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def request(self, payload: dict[str, Any]) -> Any:
        action = payload.get("action")
        with self.lock:
            if action == "overview":
                return {"status": self._status(), "clients": filter_active_clients(self.clients)}
            if action == "status":
                return self._status()
            if action == "list_clients":
                return filter_active_clients(self.clients)
            if action == "create_client":
                name = payload.get("name", "")
                if not CLIENT_NAME_RE.fullmatch(name) or name.lower() in {"server", "ca", "root"}:
                    raise APIError("客户端名称无效或属于保留名称")
                if any(item["name"] == name for item in self.clients):
                    raise APIError("该客户端已经存在", 409)
                item = {
                    "name": name,
                    "status": "active",
                    "serial": f"{len(self.clients) + 1:02X}",
                    "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=825)).isoformat().replace("+00:00", "Z"),
                    "revoked_at": None,
                    "created_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                    "has_profile": True,
                    "online": False,
                    "lan_subnet": str(payload.get("lan_subnet") or "").strip() or None,
                    "share_lan": bool(payload.get("lan_subnet")) and bool(payload.get("share_lan")),
                    "connection": None,
                }
                self.clients.append(item)
                return item
            if action == "set_client_network":
                name = payload.get("name")
                for item in self.clients:
                    if item["name"] == name and item["status"] == "active":
                        item["lan_subnet"] = str(payload.get("lan_subnet") or "").strip() or None
                        item["share_lan"] = bool(item["lan_subnet"]) and bool(payload.get("share_lan"))
                        return dict(item)
                raise APIError("未找到有效客户端", 404)
            if action == "disconnect_client":
                name = payload.get("name")
                for item in self.clients:
                    if item["name"] == name and item["online"]:
                        item.update(online=False, connection=None)
                        return {"name": name, "disconnected": True, "sessions": 1}
                raise APIError("客户端当前不在线", 409)
            if action == "revoke_client":
                name = payload.get("name")
                for item in self.clients:
                    if item["name"] == name and item["status"] == "active":
                        item.update(status="revoked", revoked_at=dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"), has_profile=False, online=False, lan_subnet=None, share_lan=False, connection=None)
                        return {"name": name, "status": "revoked", "deleted": True, "deleted_artifacts": ["演示客户端文件"]}
                raise APIError("未找到有效客户端", 404)
            if action == "get_profile":
                name = payload.get("name", "client")
                client_protocol = "udp" if self.settings["vpn_protocol"] == "udp" else "tcp-client"
                content = f"""client
dev tun
proto {client_protocol}
remote {self.settings['endpoint']} {self.settings['vpn_port']}
remote-cert-tls server
<ca>
-----BEGIN CERTIFICATE-----
DEMO-CA
-----END CERTIFICATE-----
</ca>
<cert>
-----BEGIN CERTIFICATE-----
DEMO-CLIENT
-----END CERTIFICATE-----
</cert>
<key>
-----BEGIN PRIVATE KEY-----
DEMO-PRIVATE-KEY
-----END PRIVATE KEY-----
</key>
<tls-crypt>
-----BEGIN OpenVPN Static key V1-----
DEMO-TLS-CRYPT
-----END OpenVPN Static key V1-----
</tls-crypt>
"""
                return {
                    "name": name,
                    "filename": f"{name}.ovpn",
                    "content": content,
                    "ikuai": {
                        "dial_name": name,
                        "server": self.settings["endpoint"],
                        "port": self.settings["vpn_port"],
                        "protocol": self.settings["vpn_protocol"].upper(),
                        "line": "自动",
                        "tunnel_type": "TUN",
                        "cipher": self.settings["data_cipher"],
                        "compression": "关闭",
                        "mtu": self.settings["tun_mtu"],
                        "additional_config": "tun-mtu 1500\nmssfix 1450\nauth-nocache\nmute-replay-warnings\nnobind",
                        "server_route_push": True,
                        "routes": "",
                        "redial": False,
                        "line_check": "使用爱快默认值",
                        "authentication": "静态密钥（tls-crypt）",
                        "ca_certificate": "-----BEGIN CERTIFICATE-----\nDEMO-CA\n-----END CERTIFICATE-----\n",
                        "client_certificate": "-----BEGIN CERTIFICATE-----\nDEMO-CLIENT\n-----END CERTIFICATE-----\n",
                        "private_key": "-----BEGIN PRIVATE KEY-----\nDEMO-PRIVATE-KEY\n-----END PRIVATE KEY-----\n",
                        "tls_crypt_key": "-----BEGIN OpenVPN Static key V1-----\nDEMO-TLS-CRYPT\n-----END OpenVPN Static key V1-----\n",
                    },
                }
            if action == "server_settings":
                return dict(self.settings)
            if action == "update_server_settings":
                requested = payload.get("settings")
                if not isinstance(requested, dict):
                    raise APIError("服务端设置无效")
                for key in (
                    "endpoint", "vpn_port", "vpn_protocol", "vpn_subnet", "dns_servers",
                    "redirect_gateway", "max_clients", "web_allow", "tun_mtu", "mssfix",
                    "keepalive_ping", "keepalive_timeout", "data_cipher", "auth_digest",
                    "log_verb", "push_routes",
                ):
                    if key in requested:
                        self.settings[key] = requested[key]
                return {"settings": dict(self.settings), "status": self._status()}
            if action == "restart":
                return self._status()
            if action == "logs":
                return {
                    "count": 6,
                    "checked_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                    "lines": [
                        "openvpn-server@server: Initialization Sequence Completed",
                        "openvpn-server@server: MULTI: multi_create_instance called",
                        "openvpn-server@server: peer info: IV_VER=3.git::58b92569",
                        "openvpn-server@server: Data Channel: cipher 'AES-256-GCM'",
                        "openvpn-server@server: ethan-laptop/203.0.113.42:52881 MULTI_sva: pool returned IPv4=10.8.0.2",
                        "openvpn-server@server: ethan-laptop/203.0.113.42:52881 PUSH: Received control message",
                    ],
                }
            if action == "client_logs":
                name = str(payload.get("name", "")).strip()
                if not any(item["name"] == name and item["status"] == "active" for item in self.clients):
                    raise APIError("只能查看有效客户端的日志", 404)
                service_lines = [
                    "openvpn-server@server: Initialization Sequence Completed",
                    "openvpn-server@server: MULTI: multi_create_instance called",
                    "openvpn-server@server: peer info: IV_VER=3.git::58b92569",
                    "openvpn-server@server: Data Channel: cipher 'AES-256-GCM'",
                    "openvpn-server@server: ethan-laptop/203.0.113.42:52881 MULTI_sva: pool returned IPv4=10.8.0.2",
                    "openvpn-server@server: ethan-laptop/203.0.113.42:52881 PUSH: Received control message",
                ]
                lines = [
                    line for line in service_lines if name in line
                ]
                return {
                    "name": name,
                    "lines": lines,
                    "count": payload.get("lines", 120),
                    "matched_count": len(lines),
                    "checked_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            if action == "set_web_password":
                return {"changed": True}
            if action == "update_status":
                return dict(self.update_state)
            if action == "check_updates":
                self.update_check["checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
                return dict(self.update_check)
            if action == "start_update":
                self.update_state = {
                    "state": "completed",
                    "message": "演示环境已模拟完成管理面板与 OpenVPN 更新",
                    "manager_version": "1.2.7-demo",
                    "openvpn_version": "OpenVPN 2.6 demo",
                    "target_version": "v1.2.7-demo",
                }
                return dict(self.update_state)
            raise APIError("不支持此演示操作")


class AppContext:
    def __init__(self, *, demo: bool = False):
        self.demo = demo
        if demo:
            self.server_config = {"state_dir": "/tmp/openvpn-manager-demo"}
            self.web_config = {
                "admin_user": "admin",
                "password_hash": hash_password("demo"),
                "session_secret": secrets.token_hex(32),
                "session_hours": 8,
            }
            self.agent: AgentClient | DemoAgent = DemoAgent()
        else:
            self.server_config = load_json(CONFIG_PATH)
            self.web_config = load_json(WEB_CONFIG_PATH)
            self.agent = AgentClient(SOCKET_PATH)
        self.sessions = SessionManager(
            self.web_config["session_secret"],
            int(self.web_config.get("session_hours", 8)),
        )
        self.login_limiter = LoginLimiter()
        self.password_lock = threading.Lock()

    def change_password(self, old_password: str, new_password: str) -> None:
        with self.password_lock:
            if not verify_password(old_password, self.web_config["password_hash"]):
                raise APIError("当前密码不正确", 403)
            if len(new_password) < 12 or len(new_password) > 128:
                raise APIError("新密码长度必须为 12 到 128 个字符")
            encoded = hash_password(new_password)
            self.agent.request({"action": "set_web_password", "password_hash": encoded})
            self.web_config["password_hash"] = encoded


class ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class RequestHandler(http.server.BaseHTTPRequestHandler):
    server_version = "OpenVPNWebManager/1.2.7"
    context: AppContext

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s %s", self.client_address[0], fmt % args)

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Real-IP", "").strip()
        return forwarded or self.client_address[0]

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )

    def _send_json(self, status: int, payload: Any, *, cookie: str | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise APIError("请求长度无效") from exc
        if length <= 0 or length > MAX_BODY:
            raise APIError("请求正文大小无效")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise APIError("请求正文必须是有效的 JSON") from exc
        if not isinstance(payload, dict):
            raise APIError("请求正文必须是 JSON 对象")
        return payload

    def _session(self) -> dict[str, Any] | None:
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return None
        morsel = cookie.get("ovpn_session")
        if not morsel:
            return None
        return self.context.sessions.parse(morsel.value)

    def _require_session(self, *, csrf: bool = False) -> dict[str, Any]:
        session = self._session()
        if not session:
            raise APIError("请先登录", 401)
        if csrf and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            raise APIError("CSRF 令牌无效，请刷新页面后重试", 403)
        return session

    def _route_name(self, suffix: str) -> str:
        name = urllib.parse.unquote(suffix)
        if not CLIENT_NAME_RE.fullmatch(name):
            raise APIError("客户端名称无效")
        return name

    def _handle_login(self) -> None:
        ip = self._client_ip()
        if self.context.login_limiter.limited(ip):
            raise APIError("登录失败次数过多，请稍后再试", 429)
        payload = self._read_json()
        username = str(payload.get("username", ""))
        password = str(payload.get("password", ""))
        valid_user = hmac.compare_digest(username, str(self.context.web_config["admin_user"]))
        valid_password = verify_password(password, str(self.context.web_config["password_hash"]))
        if not (valid_user and valid_password):
            self.context.login_limiter.failure(ip)
            LOGGER.warning("failed login from %s", ip)
            raise APIError("用户名或密码错误", 401)
        self.context.login_limiter.success(ip)
        token, session = self.context.sessions.issue(username)
        secure = not self.context.demo
        cookie = f"ovpn_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={self.context.sessions.lifetime}"
        if secure:
            cookie += "; Secure"
        LOGGER.info("successful login from %s", ip)
        self._send_json(200, {"ok": True, "user": username, "csrf": session["csrf"]}, cookie=cookie)

    def _serve_static(self, path: str) -> None:
        relative = urllib.parse.unquote(path).lstrip("/") or "index.html"
        candidate = (STATIC_DIR / relative).resolve()
        root = STATIC_DIR.resolve()
        if root not in candidate.parents and candidate != root:
            raise APIError("页面不存在", 404)
        if not candidate.is_file():
            candidate = root / "index.html"
        body = candidate.read_bytes()
        mime, _ = mimetypes.guess_type(candidate.name)
        self.send_response(200)
        self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if candidate.name == "index.html" else "public, max-age=3600")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _dispatch_get(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/api/session":
            session = self._require_session()
            self._send_json(200, {"user": session["u"], "csrf": session["csrf"]})
            return
        if path == "/api/overview":
            self._require_session()
            self._send_json(200, self.context.agent.request({"action": "overview"}))
            return
        if path == "/api/clients":
            self._require_session()
            self._send_json(200, {"clients": self.context.agent.request({"action": "list_clients"})})
            return
        if path == "/api/logs":
            self._require_session()
            try:
                lines = int(query.get("lines", ["120"])[0])
            except ValueError:
                lines = 120
            self._send_json(200, self.context.agent.request({"action": "logs", "lines": lines}))
            return
        client_logs_match = re.fullmatch(r"/api/clients/([^/]+)/logs", path)
        if client_logs_match:
            self._require_session()
            name = self._route_name(client_logs_match.group(1))
            try:
                lines = int(query.get("lines", ["120"])[0])
            except ValueError:
                lines = 120
            self._send_json(
                200,
                self.context.agent.request({"action": "client_logs", "name": name, "lines": lines}),
            )
            return
        if path == "/api/server/settings":
            self._require_session()
            self._send_json(200, self.context.agent.request({"action": "server_settings"}))
            return
        if path == "/api/update":
            self._require_session()
            self._send_json(200, self.context.agent.request({"action": "update_status"}))
            return
        if path == "/api/update/check":
            self._require_session()
            force = query.get("force", ["0"])[0] == "1"
            self._send_json(200, self.context.agent.request({"action": "check_updates", "force": force}))
            return
        profile_match = re.fullmatch(r"/api/clients/([^/]+)/profile", path)
        if profile_match:
            self._require_session()
            name = self._route_name(profile_match.group(1))
            profile = self.context.agent.request({"action": "get_profile", "name": name})
            if query.get("format", [""])[0] == "json":
                self._send_json(200, profile)
                return
            body = profile["content"].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-openvpn-profile")
            self.send_header("Content-Disposition", f'attachment; filename="{name}.ovpn"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/"):
            raise APIError("接口不存在", 404)
        self._serve_static(path)

    def _dispatch_post(self, path: str) -> None:
        if path == "/api/login":
            self._handle_login()
            return
        if path == "/api/logout":
            self._require_session(csrf=True)
            expired = "ovpn_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
            if not self.context.demo:
                expired += "; Secure"
            self._send_json(200, {"ok": True}, cookie=expired)
            return
        if path == "/api/clients":
            self._require_session(csrf=True)
            payload = self._read_json()
            name = self._route_name(str(payload.get("name", "")))
            result = self.context.agent.request(
                {
                    "action": "create_client",
                    "name": name,
                    "lan_subnet": payload.get("lan_subnet", ""),
                    "share_lan": payload.get("share_lan", False),
                }
            )
            LOGGER.info("client profile created: %s", name)
            self._send_json(201, result)
            return
        if path == "/api/server/restart":
            self._require_session(csrf=True)
            LOGGER.warning("OpenVPN restart requested by dashboard user")
            self._send_json(200, self.context.agent.request({"action": "restart"}))
            return
        if path == "/api/server/settings":
            self._require_session(csrf=True)
            payload = self._read_json()
            LOGGER.warning("OpenVPN server settings update requested by dashboard user")
            result = self.context.agent.request(
                {"action": "update_server_settings", "settings": payload}
            )
            self._send_json(200, result)
            return
        disconnect_match = re.fullmatch(r"/api/clients/([^/]+)/disconnect", path)
        if disconnect_match:
            self._require_session(csrf=True)
            name = self._route_name(disconnect_match.group(1))
            result = self.context.agent.request({"action": "disconnect_client", "name": name})
            LOGGER.warning("client disconnected: %s", name)
            self._send_json(200, result)
            return
        network_match = re.fullmatch(r"/api/clients/([^/]+)/network", path)
        if network_match:
            self._require_session(csrf=True)
            name = self._route_name(network_match.group(1))
            payload = self._read_json()
            result = self.context.agent.request(
                {
                    "action": "set_client_network",
                    "name": name,
                    "lan_subnet": payload.get("lan_subnet", ""),
                    "share_lan": payload.get("share_lan", False),
                }
            )
            LOGGER.warning("client network settings changed: %s", name)
            self._send_json(200, result)
            return
        if path == "/api/update":
            self._require_session(csrf=True)
            LOGGER.warning("manager and OpenVPN update requested by dashboard user")
            self._send_json(202, self.context.agent.request({"action": "start_update"}))
            return
        if path == "/api/password":
            self._require_session(csrf=True)
            payload = self._read_json()
            self.context.change_password(str(payload.get("current_password", "")), str(payload.get("new_password", "")))
            LOGGER.info("dashboard password changed")
            self._send_json(200, {"changed": True})
            return
        raise APIError("接口不存在", 404)

    def _dispatch_delete(self, path: str) -> None:
        match = re.fullmatch(r"/api/clients/([^/]+)", path)
        if not match:
            raise APIError("接口不存在", 404)
        self._require_session(csrf=True)
        name = self._route_name(match.group(1))
        result = self.context.agent.request({"action": "revoke_client", "name": name})
        LOGGER.warning("client revoked: %s", name)
        self._send_json(200, result)

    def _handle(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        try:
            if self.command == "GET":
                self._dispatch_get(parsed.path, urllib.parse.parse_qs(parsed.query))
            elif self.command == "POST":
                self._dispatch_post(parsed.path)
            elif self.command == "DELETE":
                self._dispatch_delete(parsed.path)
            else:
                raise APIError("不支持此请求方法", 405)
        except APIError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except (OSError, KeyError, ValueError) as exc:
            LOGGER.exception("request failed")
            self._send_json(500, {"error": "服务器内部错误"})

    do_GET = _handle
    do_POST = _handle
    do_DELETE = _handle


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenVPN 管理中心 HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--hash-password-stdin", action="store_true")
    args = parser.parse_args()
    if args.hash_password_stdin:
        print(hash_password(input_stream_read()))
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    context = AppContext(demo=args.demo)
    RequestHandler.context = context
    server = ThreadedHTTPServer((args.host, args.port), RequestHandler)
    scheme = "http"
    LOGGER.info("OpenVPN 管理中心正在监听 %s://%s:%s", scheme, args.host, args.port)
    if args.demo:
        LOGGER.info("演示账号：admin / demo")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def input_stream_read() -> str:
    import sys

    return sys.stdin.read()


if __name__ == "__main__":
    raise SystemExit(main())
