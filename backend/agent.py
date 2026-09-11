#!/usr/bin/env python3
"""Privileged OpenVPN/Easy-RSA control agent.

The agent exposes a deliberately small JSON protocol over a Unix socket.  The
socket is available only to root and the unprivileged dashboard service group.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

try:  # Linux-only account databases; parsing/rendering tests also run on Windows.
    import grp
    import pwd
except ModuleNotFoundError:  # pragma: no cover - production target is Linux
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]


CONFIG_PATH = os.environ.get("OPENVPN_MANAGER_CONFIG", "/etc/openvpn-manager/server.json")
WEB_CONFIG_PATH = os.environ.get("OPENVPN_MANAGER_WEB_CONFIG", "/etc/openvpn-manager/web.json")
SOCKET_PATH = os.environ.get("OPENVPN_MANAGER_SOCKET", "/run/openvpn-manager/agent.sock")
CLIENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
PASSWORD_HASH_RE = re.compile(r"^scrypt\$16384\$8\$1\$[A-Za-z0-9_-]{16,}\$[A-Za-z0-9_-]{32,}$")
MUTATION_LOCK = threading.Lock()


class AgentError(RuntimeError):
    """Safe operational error returned to the local caller."""


def group_gid(name: str) -> int:
    if grp is None:
        raise AgentError("当前平台不支持 Unix 用户组查询")
    return grp.getgrnam(name).gr_gid


def user_uid(name: str) -> int:
    if pwd is None:
        raise AgentError("当前平台不支持 Unix 用户查询")
    return pwd.getpwnam(name).pw_uid


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: str | pathlib.Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentError(f"无法读取配置 {path}：{exc}") from exc
    if not isinstance(data, dict):
        raise AgentError(f"配置 {path} 必须包含 JSON 对象")
    return data


def validate_client_name(name: Any) -> str:
    if not isinstance(name, str) or not CLIENT_NAME_RE.fullmatch(name):
        raise AgentError("客户端名称必须以字母或数字开头，只能包含字母、数字、下划线或连字符，最多 32 个字符")
    if name.lower() in {"server", "ca", "openvpn", "root"}:
        raise AgentError("该客户端名称属于保留名称")
    return name


def parse_time(value: str) -> str | None:
    if not value:
        return None
    for fmt in ("%y%m%d%H%M%SZ", "%Y%m%d%H%M%SZ"):
        try:
            parsed = dt.datetime.strptime(value, fmt).replace(tzinfo=dt.timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return None


def parse_status(text: str) -> list[dict[str, Any]]:
    """Parse OpenVPN status-version 3 CSV into online-client records."""
    headers: list[str] | None = None
    clients: list[dict[str, Any]] = []
    for row in csv.reader(text.splitlines()):
        if len(row) >= 3 and row[0] == "HEADER" and row[1] == "CLIENT_LIST":
            headers = row[2:]
            continue
        if not row or row[0] != "CLIENT_LIST":
            continue
        values = row[1:]
        if headers:
            item = dict(zip(headers, values))
            common_name = item.get("Common Name", "")
            real_address = item.get("Real Address", "")
            virtual_address = item.get("Virtual Address", "")
            bytes_received = item.get("Bytes Received", "0")
            bytes_sent = item.get("Bytes Sent", "0")
            connected_since = item.get("Connected Since", "")
            cipher = item.get("Data Channel Cipher", "")
        else:
            common_name = values[0] if len(values) > 0 else ""
            real_address = values[1] if len(values) > 1 else ""
            virtual_address = values[2] if len(values) > 2 else ""
            bytes_received = values[4] if len(values) > 4 else "0"
            bytes_sent = values[5] if len(values) > 5 else "0"
            connected_since = values[6] if len(values) > 6 else ""
            cipher = values[-1] if values else ""
        if not common_name:
            continue
        try:
            received_number = int(bytes_received)
        except (TypeError, ValueError):
            received_number = 0
        try:
            sent_number = int(bytes_sent)
        except (TypeError, ValueError):
            sent_number = 0
        clients.append(
            {
                "name": common_name,
                "real_address": real_address,
                "virtual_address": virtual_address,
                "bytes_received": received_number,
                "bytes_sent": sent_number,
                "connected_since": connected_since,
                "cipher": cipher,
            }
        )
    return clients


def parse_index(
    text: str,
    profiles_dir: pathlib.Path | None = None,
    online: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Parse an OpenSSL/Easy-RSA index.txt database."""
    online = online or {}
    clients: list[dict[str, Any]] = []
    status_names = {"V": "active", "R": "revoked", "E": "expired"}
    for raw_line in text.splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 6 or parts[0] not in status_names:
            continue
        subject = parts[-1]
        match = re.search(r"(?:^|/)CN=([^/]+)", subject)
        if not match:
            continue
        name = match.group(1)
        if name == "server" or not CLIENT_NAME_RE.fullmatch(name):
            continue
        profile = profiles_dir / f"{name}.ovpn" if profiles_dir else None
        created_at = None
        has_profile = bool(profile and profile.is_file())
        if profile and profile.exists():
            created_at = dt.datetime.fromtimestamp(profile.stat().st_mtime, dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        online_item = online.get(name)
        clients.append(
            {
                "name": name,
                "status": status_names[parts[0]],
                "serial": parts[3],
                "expires_at": parse_time(parts[1]),
                "revoked_at": parse_time(parts[2]),
                "created_at": created_at,
                "has_profile": has_profile,
                "online": online_item is not None and parts[0] == "V",
                "connection": online_item,
            }
        )
    return sorted(clients, key=lambda item: (item["status"] != "active", item["name"].lower()))


def extract_pem(path: pathlib.Path, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentError(f"无法读取 {path.name}：{exc}") from exc
    pattern = re.compile(
        rf"-----BEGIN {re.escape(label)}-----.*?-----END {re.escape(label)}-----",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise AgentError(f"在 {path} 中未找到 {label} PEM 数据块")
    return match.group(0).strip() + "\n"


def extract_private_key(path: pathlib.Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentError(f"无法读取 {path.name}：{exc}") from exc
    match = re.search(
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----.*?-----END (?:RSA |EC )?PRIVATE KEY-----",
        text,
        re.DOTALL,
    )
    if not match:
        raise AgentError(f"在 {path} 中未找到私钥 PEM 数据块")
    return match.group(0).strip() + "\n"


def render_profile(config: dict[str, Any], client_name: str) -> str:
    easy_rsa = pathlib.Path(config["easy_rsa_dir"])
    openvpn_dir = pathlib.Path(config["openvpn_dir"])
    ca = extract_pem(easy_rsa / "pki" / "ca.crt", "CERTIFICATE")
    cert = extract_pem(easy_rsa / "pki" / "issued" / f"{client_name}.crt", "CERTIFICATE")
    key = extract_private_key(easy_rsa / "pki" / "private" / f"{client_name}.key")
    tls_crypt = extract_pem(openvpn_dir / "tls-crypt.key", "OpenVPN Static key V1")
    endpoint = config["endpoint"]
    port = int(config["vpn_port"])
    protocol = config.get("vpn_protocol", "udp")
    server_name = config.get("server_name", "server")
    return f"""client
dev tun
proto {protocol}
remote {endpoint} {port}
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
verify-x509-name {server_name} name
auth SHA256
auth-nocache
cipher AES-256-GCM
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
setenv opt block-outside-dns
verb 3

<ca>
{ca}</ca>
<cert>
{cert}</cert>
<key>
{key}</key>
<tls-crypt>
{tls_crypt}</tls-crypt>
"""


class OpenVPNController:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.easy_rsa_dir = pathlib.Path(config["easy_rsa_dir"])
        self.openvpn_dir = pathlib.Path(config["openvpn_dir"])
        self.state_dir = pathlib.Path(config["state_dir"])
        self.profiles_dir = self.state_dir / "clients"
        self.status_file = pathlib.Path(config["status_file"])
        self.service_name = config.get("service_name", "openvpn-server@server.service")
        self.web_group = config.get("web_group", "openvpn-web")

    def run(self, args: list[str], *, cwd: pathlib.Path | None = None, timeout: int = 120) -> str:
        environment = os.environ.copy()
        environment["EASYRSA_BATCH"] = "1"
        try:
            completed = subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentError(f"命令无法执行：{exc}") from exc
        output = completed.stdout.strip()
        if completed.returncode != 0:
            safe_tail = "\n".join(output.splitlines()[-8:])
            raise AgentError(safe_tail or f"命令退出状态为 {completed.returncode}")
        return output

    def _read_status(self) -> list[dict[str, Any]]:
        try:
            return parse_status(self.status_file.read_text(encoding="utf-8", errors="replace"))
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise AgentError(f"无法读取 OpenVPN 状态：{exc}") from exc

    def _read_index(self) -> str:
        try:
            return (self.easy_rsa_dir / "pki" / "index.txt").read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise AgentError(f"无法读取 Easy-RSA 索引：{exc}") from exc

    def list_clients(self) -> list[dict[str, Any]]:
        online_records = self._read_status()
        online = {item["name"]: item for item in online_records}
        return parse_index(self._read_index(), self.profiles_dir, online)

    def status(self) -> dict[str, Any]:
        completed = subprocess.run(
            ["systemctl", "is-active", self.service_name],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
        state = completed.stdout.strip() or "unknown"
        online = self._read_status()
        try:
            version_line = self.run(["openvpn", "--version"], timeout=15).splitlines()[0]
        except (AgentError, IndexError):
            version_line = "OpenVPN"
        return {
            "service": state,
            "healthy": state == "active",
            "online_count": len(online),
            "online_clients": online,
            "endpoint": self.config["endpoint"],
            "vpn_port": self.config["vpn_port"],
            "vpn_protocol": self.config.get("vpn_protocol", "udp"),
            "web_port": self.config["web_port"],
            "vpn_subnet": self.config["vpn_subnet"],
            "version": version_line,
            "checked_at": utc_now(),
        }

    def overview(self) -> dict[str, Any]:
        status = self.status()
        clients = self.list_clients()
        return {"status": status, "clients": clients}

    def _atomic_profile(self, name: str, content: str) -> pathlib.Path:
        self.profiles_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        target = self.profiles_dir / f"{name}.ovpn"
        group_id = group_gid(self.web_group)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=self.profiles_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o640)
            os.chown(temp_name, 0, group_id)
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return target

    def create_client(self, name: Any) -> dict[str, Any]:
        name = validate_client_name(name)
        with MUTATION_LOCK:
            existing = {item["name"] for item in self.list_clients()}
            if name in existing:
                raise AgentError(f"客户端 {name} 已存在或曾经被吊销")
            self.run([str(self.easy_rsa_dir / "easyrsa"), "--batch", "build-client-full", name, "nopass"], cwd=self.easy_rsa_dir)
            self._atomic_profile(name, render_profile(self.config, name))
            created = next((item for item in self.list_clients() if item["name"] == name), None)
            if not created:
                raise AgentError("证书已创建，但无法从索引中读取")
            return created

    def revoke_client(self, name: Any) -> dict[str, Any]:
        name = validate_client_name(name)
        with MUTATION_LOCK:
            clients = {item["name"]: item for item in self.list_clients()}
            if name not in clients:
                raise AgentError(f"客户端 {name} 不存在")
            if clients[name]["status"] != "active":
                raise AgentError(f"客户端 {name} 当前状态已是 {clients[name]['status']}")
            easyrsa = str(self.easy_rsa_dir / "easyrsa")
            self.run([easyrsa, "--batch", "revoke", name], cwd=self.easy_rsa_dir)
            self.run([easyrsa, "--batch", "gen-crl"], cwd=self.easy_rsa_dir)
            source_crl = self.easy_rsa_dir / "pki" / "crl.pem"
            target_crl = self.openvpn_dir / "crl.pem"
            descriptor, temp_name = tempfile.mkstemp(prefix=".crl.", dir=self.openvpn_dir)
            os.close(descriptor)
            try:
                shutil.copyfile(source_crl, temp_name)
                os.chmod(temp_name, 0o644)
                os.replace(temp_name, target_crl)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            profile = self.profiles_dir / f"{name}.ovpn"
            try:
                profile.unlink()
            except FileNotFoundError:
                pass
            self.restart()
            return {"name": name, "status": "revoked", "revoked_at": utc_now()}

    def restart(self) -> dict[str, Any]:
        self.run(["systemctl", "restart", self.service_name], timeout=60)
        time.sleep(0.4)
        state = self.status()
        if not state["healthy"]:
            raise AgentError(f"OpenVPN 已完成重启，但服务状态为 {state['service']}")
        return state

    def logs(self, lines: Any) -> dict[str, Any]:
        try:
            count = max(20, min(int(lines), 500))
        except (TypeError, ValueError):
            count = 100
        output = self.run(
            ["journalctl", "-u", self.service_name, "-n", str(count), "--no-pager", "--output=short-iso"],
            timeout=30,
        )
        return {"lines": output.splitlines(), "count": count, "checked_at": utc_now()}

    def get_profile(self, name: Any) -> dict[str, Any]:
        name = validate_client_name(name)
        active = {item["name"]: item for item in self.list_clients() if item["status"] == "active"}
        if name not in active:
            raise AgentError("只能下载有效客户端的配置")
        profile = self.profiles_dir / f"{name}.ovpn"
        try:
            content = profile.read_text(encoding="utf-8")
        except OSError as exc:
            raise AgentError(f"客户端配置不可用：{exc}") from exc
        if len(content) > 128_000:
            raise AgentError("客户端配置超过安全大小限制")
        return {"name": name, "filename": f"{name}.ovpn", "content": content}

    def set_web_password(self, encoded_hash: Any) -> dict[str, Any]:
        if not isinstance(encoded_hash, str) or not PASSWORD_HASH_RE.fullmatch(encoded_hash):
            raise AgentError("密码哈希格式无效")
        with MUTATION_LOCK:
            web_config = load_json(WEB_CONFIG_PATH)
            web_config["password_hash"] = encoded_hash
            target = pathlib.Path(WEB_CONFIG_PATH)
            group_id = group_gid(self.web_group)
            descriptor, temp_name = tempfile.mkstemp(prefix=".web.", suffix=".json", dir=target.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(web_config, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp_name, 0o640)
                os.chown(temp_name, 0, group_id)
                os.replace(temp_name, target)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        return {"changed": True, "changed_at": utc_now()}

    def dispatch(self, request: dict[str, Any]) -> Any:
        action = request.get("action")
        if action == "status":
            return self.status()
        if action == "overview":
            return self.overview()
        if action == "list_clients":
            return self.list_clients()
        if action == "create_client":
            return self.create_client(request.get("name"))
        if action == "revoke_client":
            return self.revoke_client(request.get("name"))
        if action == "restart":
            return self.restart()
        if action == "logs":
            return self.logs(request.get("lines", 100))
        if action == "get_profile":
            return self.get_profile(request.get("name"))
        if action == "set_web_password":
            return self.set_web_password(request.get("password_hash"))
        raise AgentError("不支持此代理操作")


class AgentRequestHandler(socketserver.StreamRequestHandler):
    controller: OpenVPNController
    allowed_uid: int

    def _send(self, payload: dict[str, Any]) -> None:
        self.wfile.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())

    def handle(self) -> None:
        try:
            credentials = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", credentials)
            if uid not in {0, self.allowed_uid}:
                raise AgentError("调用方没有权限")
            raw = self.rfile.readline(8193)
            if not raw or len(raw) > 8192:
                raise AgentError("请求大小无效")
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise AgentError("请求必须是 JSON 对象")
            data = self.controller.dispatch(request)
            self._send({"ok": True, "data": data})
        except (AgentError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send({"ok": False, "error": str(exc)})
        except Exception:
            self._send({"ok": False, "error": "控制代理内部错误"})


if hasattr(socketserver, "UnixStreamServer"):
    class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):  # type: ignore[attr-defined]
        daemon_threads = True
else:  # pragma: no cover - production target is Linux
    ThreadedUnixServer = None  # type: ignore[assignment,misc]


def serve(controller: OpenVPNController, socket_path: str) -> None:
    if ThreadedUnixServer is None:
        raise AgentError("当前平台不支持 Unix 域套接字")
    socket_file = pathlib.Path(socket_path)
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        socket_file.unlink()
    except FileNotFoundError:
        pass
    allowed_uid = user_uid(controller.web_group)
    group_id = group_gid(controller.web_group)
    AgentRequestHandler.controller = controller
    AgentRequestHandler.allowed_uid = allowed_uid
    with ThreadedUnixServer(socket_path, AgentRequestHandler) as server:
        os.chown(socket_path, 0, group_id)
        os.chmod(socket_path, 0o660)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            try:
                socket_file.unlink()
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenVPN 管理中心特权代理")
    parser.add_argument("--direct", choices=["status", "overview", "list_clients", "create_client", "revoke_client", "restart", "logs"])
    parser.add_argument("--name")
    parser.add_argument("--lines", type=int, default=100)
    args = parser.parse_args()
    try:
        controller = OpenVPNController(load_json(CONFIG_PATH))
        if args.direct:
            payload: dict[str, Any] = {"action": args.direct, "lines": args.lines}
            if args.name:
                payload["name"] = args.name
            print(json.dumps(controller.dispatch(payload), indent=2))
            return 0
        serve(controller, SOCKET_PATH)
        return 0
    except (AgentError, KeyError, OSError) as exc:
        print(f"代理错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
