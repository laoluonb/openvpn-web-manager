#!/usr/bin/env python3
"""Privileged OpenVPN/Easy-RSA control agent.

The agent exposes a deliberately small JSON protocol over a Unix socket.  The
socket is available only to root and the unprivileged dashboard service group.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import ipaddress
import json
import os
import pathlib
import re
import shlex
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
DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
PASSWORD_HASH_RE = re.compile(r"^scrypt\$16384\$8\$1\$[A-Za-z0-9_-]{16,}\$[A-Za-z0-9_-]{32,}$")
MUTATION_LOCK = threading.Lock()
PRIVATE_V4_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


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


def validate_endpoint(value: Any) -> str:
    if not isinstance(value, str):
        raise AgentError("公网地址必须是 IPv4 地址或域名")
    endpoint = value.strip().rstrip(".")
    if not endpoint or len(endpoint) > 253 or any(char.isspace() for char in endpoint):
        raise AgentError("公网地址必须是 IPv4 地址或域名")
    try:
        address = ipaddress.ip_address(endpoint)
    except ValueError:
        labels = endpoint.split(".")
        if len(labels) < 2 or any(not DOMAIN_LABEL_RE.fullmatch(label) for label in labels):
            raise AgentError("公网地址必须是有效的 IPv4 地址或域名")
    else:
        if address.version != 4:
            raise AgentError("当前仅支持 IPv4 公网地址")
    return endpoint


def validate_port(value: Any, label: str = "端口") -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise AgentError(f"{label}必须是 1 到 65535 的整数") from exc
    if port < 1 or port > 65535:
        raise AgentError(f"{label}必须是 1 到 65535 的整数")
    return port


def validate_private_network(value: Any, label: str, *, max_prefix: int = 30) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError as exc:
        raise AgentError(f"{label}必须是有效的 IPv4 CIDR，例如 192.168.10.0/24") from exc
    if network.version != 4 or network.prefixlen < 8 or network.prefixlen > max_prefix:
        raise AgentError(f"{label}必须是 /8 到 /{max_prefix} 的 IPv4 私有网段")
    if not any(network.subnet_of(private) for private in PRIVATE_V4_NETWORKS):
        raise AgentError(f"{label}必须使用 10/8、172.16/12 或 192.168/16 私有地址")
    return network


def normalize_dns_servers(value: Any) -> list[str]:
    if value is None:
        return []
    values = value.split(",") if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)):
        raise AgentError("DNS 服务器必须是 IPv4 地址列表")
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        try:
            address = ipaddress.ip_address(text)
        except ValueError as exc:
            raise AgentError(f"DNS 服务器地址无效：{text}") from exc
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            raise AgentError(f"DNS 服务器地址无效：{text}")
        normalized = str(address)
        if normalized not in result:
            result.append(normalized)
    if len(result) > 3:
        raise AgentError("最多可以配置 3 个 DNS 服务器")
    return result


def normalize_server_settings(current: dict[str, Any], requested: dict[str, Any]) -> dict[str, Any]:
    endpoint = validate_endpoint(requested.get("endpoint", current.get("endpoint", "")))
    vpn_port = validate_port(requested.get("vpn_port", current.get("vpn_port", 1194)), "VPN 端口")
    protocol = str(requested.get("vpn_protocol", current.get("vpn_protocol", "udp"))).strip().lower()
    if protocol not in {"udp", "tcp"}:
        raise AgentError("VPN 协议只能选择 UDP 或 TCP")
    vpn_network = validate_private_network(
        requested.get("vpn_subnet", current.get("vpn_subnet", "10.8.0.0/24")),
        "VPN 子网",
        max_prefix=29,
    )
    dns_servers = normalize_dns_servers(requested.get("dns_servers", current.get("dns_servers", ["1.1.1.1", "9.9.9.9"])))
    redirect_gateway = requested.get("redirect_gateway", current.get("redirect_gateway", True))
    if not isinstance(redirect_gateway, bool):
        raise AgentError("全局流量转发选项必须是布尔值")
    try:
        max_clients = int(requested.get("max_clients", current.get("max_clients", 100)))
    except (TypeError, ValueError) as exc:
        raise AgentError("最大客户端数必须是整数") from exc
    if max_clients < 1 or max_clients > 1000:
        raise AgentError("最大客户端数必须在 1 到 1000 之间")
    if vpn_port == int(current.get("web_port", 8443)):
        raise AgentError("VPN 端口不能与管理控制台端口相同")

    result = dict(current)
    result.update(
        endpoint=endpoint,
        vpn_port=vpn_port,
        vpn_protocol=protocol,
        vpn_subnet=vpn_network.with_prefixlen,
        dns_servers=dns_servers,
        redirect_gateway=redirect_gateway,
        max_clients=max_clients,
    )
    return result


def normalize_client_network(
    value: Any,
    share_lan: Any,
    vpn_subnet: Any,
    existing: dict[str, dict[str, Any]],
    client_name: str,
) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not isinstance(share_lan, bool):
        raise AgentError("内网共享选项必须是布尔值")
    network = validate_private_network(text, "客户端下级内网")
    vpn_network = validate_private_network(vpn_subnet, "VPN 子网", max_prefix=29)
    if network.overlaps(vpn_network):
        raise AgentError("客户端下级内网不能与 VPN 子网重叠")
    for name, item in existing.items():
        if name == client_name or not item.get("lan_subnet"):
            continue
        other = validate_private_network(item["lan_subnet"], f"客户端 {name} 的下级内网")
        if network.overlaps(other):
            raise AgentError(f"该网段与客户端 {name} 的下级内网 {other.with_prefixlen} 重叠")
    return {"lan_subnet": network.with_prefixlen, "share_lan": share_lan}


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


def render_server_config(config: dict[str, Any], client_networks: dict[str, dict[str, Any]]) -> str:
    settings = normalize_server_settings(config, {})
    vpn_network = ipaddress.ip_network(settings["vpn_subnet"])
    protocol = settings["vpn_protocol"]
    server_protocol = "udp" if protocol == "udp" else "tcp-server"
    tunnel_interface = str(settings.get("tunnel_interface", "tun0"))
    if not re.fullmatch(r"tun[0-9]+", tunnel_interface):
        raise AgentError("隧道接口名称无效")
    openvpn_dir = str(settings.get("openvpn_dir", "/etc/openvpn/server"))
    status_file = str(settings.get("status_file", "/var/log/openvpn/status.log"))
    management_socket = str(settings.get("management_socket", "/run/openvpn-manager/openvpn.sock"))
    ipp_path = str(settings.get("ipp_path", "/var/lib/openvpn/server/ipp.txt"))

    lines = [
        f"port {settings['vpn_port']}",
        f"proto {server_protocol}",
        f"dev {tunnel_interface}",
        "topology subnet",
        f"server {vpn_network.network_address} {vpn_network.netmask}",
        f"ifconfig-pool-persist {ipp_path}",
        f"client-config-dir {openvpn_dir}/ccd",
        "",
        f"ca {openvpn_dir}/ca.crt",
        f"cert {openvpn_dir}/server.crt",
        f"key {openvpn_dir}/server.key",
        "dh none",
        f"crl-verify {openvpn_dir}/crl.pem",
        f"tls-crypt {openvpn_dir}/tls-crypt.key",
        "remote-cert-tls client",
        "tls-version-min 1.2",
        "",
        "data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305",
        "data-ciphers-fallback AES-256-GCM",
        "auth SHA256",
        "",
    ]
    if settings["redirect_gateway"]:
        lines.append('push "redirect-gateway def1 bypass-dhcp"')
    for dns_server in settings["dns_servers"]:
        lines.append(f'push "dhcp-option DNS {dns_server}"')

    routes: list[tuple[str, ipaddress.IPv4Network, bool]] = []
    for client_name, item in client_networks.items():
        validate_client_name(client_name)
        if not item.get("lan_subnet"):
            continue
        network = validate_private_network(item["lan_subnet"], f"客户端 {client_name} 的下级内网")
        if network.overlaps(vpn_network):
            raise AgentError(f"客户端 {client_name} 的下级内网与 VPN 子网重叠")
        routes.append((client_name, network, bool(item.get("share_lan", False))))
    if routes:
        lines.extend(["", "# BEGIN OPENVPN-MANAGER CLIENT ROUTES"])
        for client_name, network, share_lan in sorted(routes):
            lines.append(f"# {client_name}: {'shared' if share_lan else 'server-only'}")
            lines.append(f"route {network.network_address} {network.netmask}")
        lines.append("# END OPENVPN-MANAGER CLIENT ROUTES")

    lines.extend(
        [
            "",
            "keepalive 10 120",
            "persist-key",
            "persist-tun",
            "user nobody",
            "group nogroup",
        ]
    )
    if protocol == "udp":
        lines.append("explicit-exit-notify 1")
    lines.extend(
        [
            f"max-clients {settings['max_clients']}",
            f"status {status_file} 3",
            "status-version 3",
            f"management {management_socket} unix",
            "management-client-user root",
            "management-client-group root",
            "verb 3",
        ]
    )
    return "\n".join(lines) + "\n"


def render_ccd_configs(
    client_networks: dict[str, dict[str, Any]],
    active_clients: list[str],
) -> dict[str, str]:
    """Render per-client iroute and route pushes without routing an owner back to its own LAN."""
    marker = "# Managed by openvpn-web-manager"
    normalized_routes: list[tuple[str, ipaddress.IPv4Network, bool]] = []
    for owner, item in client_networks.items():
        validate_client_name(owner)
        network = validate_private_network(item.get("lan_subnet"), f"客户端 {owner} 的下级内网")
        normalized_routes.append((owner, network, bool(item.get("share_lan", False))))

    active_names = {validate_client_name(name) for name in active_clients}
    names = set(client_networks) | active_names

    rendered: dict[str, str] = {}
    for name in sorted(names):
        lines = [marker]
        owned = next((network for owner, network, _shared in normalized_routes if owner == name), None)
        if owned:
            lines.append(f"iroute {owned.network_address} {owned.netmask}")
        for owner, network, shared in sorted(normalized_routes):
            if shared and owner != name and name in active_names:
                lines.append(f'push "route {network.network_address} {network.netmask}"')
        if len(lines) > 1:
            rendered[name] = "\n".join(lines) + "\n"
    return rendered


def render_profile(config: dict[str, Any], client_name: str) -> str:
    easy_rsa = pathlib.Path(config["easy_rsa_dir"])
    openvpn_dir = pathlib.Path(config["openvpn_dir"])
    ca = extract_pem(easy_rsa / "pki" / "ca.crt", "CERTIFICATE")
    cert = extract_pem(easy_rsa / "pki" / "issued" / f"{client_name}.crt", "CERTIFICATE")
    key = extract_private_key(easy_rsa / "pki" / "private" / f"{client_name}.key")
    tls_crypt = extract_pem(openvpn_dir / "tls-crypt.key", "OpenVPN Static key V1")
    endpoint = config["endpoint"]
    port = int(config["vpn_port"])
    protocol = str(config.get("vpn_protocol", "udp")).lower()
    client_protocol = "udp" if protocol == "udp" else "tcp-client"
    server_name = config.get("server_name", "server")
    return f"""client
dev tun
proto {client_protocol}
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
        self.config = normalize_server_settings(config, {})
        self.easy_rsa_dir = pathlib.Path(self.config["easy_rsa_dir"])
        self.openvpn_dir = pathlib.Path(self.config["openvpn_dir"])
        self.state_dir = pathlib.Path(self.config["state_dir"])
        self.profiles_dir = self.state_dir / "clients"
        self.status_file = pathlib.Path(self.config["status_file"])
        self.service_name = self.config.get("service_name", "openvpn-server@server.service")
        self.web_group = self.config.get("web_group", "openvpn-web")
        self.client_networks_path = pathlib.Path(
            self.config.get("client_networks_path", "/etc/openvpn-manager/client-networks.json")
        )
        self.firewall_env_path = pathlib.Path(
            self.config.get("firewall_env_path", "/etc/openvpn-manager/firewall.env")
        )
        self.firewall_routes_path = pathlib.Path(
            self.config.get("firewall_routes_path", "/etc/openvpn-manager/client-routes.conf")
        )
        self.management_socket = pathlib.Path(
            self.config.get("management_socket", "/run/openvpn-manager/openvpn.sock")
        )
        self.ipp_path = pathlib.Path(
            self.config.get("ipp_path", "/var/lib/openvpn/server/ipp.txt")
        )
        self.install_state_path = pathlib.Path(
            self.config.get("install_state_path", "/etc/openvpn-manager/install-state.json")
        )
        self.update_request_path = pathlib.Path(
            self.config.get("update_request_path", "/var/lib/openvpn-manager/update-request.json")
        )
        self.update_status_path = pathlib.Path(
            self.config.get("update_status_path", "/var/lib/openvpn-manager/update-status.json")
        )
        self.update_service_name = self.config.get(
            "update_service_name", "openvpn-manager-update.service"
        )

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

    def _atomic_text(
        self,
        target: pathlib.Path,
        content: str,
        *,
        mode: int,
        group_name: str | None = None,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, mode)
            if group_name:
                os.chown(temp_name, 0, group_gid(group_name))
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _atomic_json(
        self,
        target: pathlib.Path,
        data: dict[str, Any],
        *,
        mode: int = 0o640,
        group_name: str | None = None,
    ) -> None:
        self._atomic_text(
            target,
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            mode=mode,
            group_name=group_name,
        )

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

    def _read_client_networks(self) -> dict[str, dict[str, Any]]:
        try:
            raw = load_json(self.client_networks_path)
        except AgentError as exc:
            if not self.client_networks_path.exists():
                return {}
            raise exc
        networks: dict[str, dict[str, Any]] = {}
        for name, item in raw.items():
            validate_client_name(name)
            if not isinstance(item, dict):
                raise AgentError(f"客户端 {name} 的内网配置无效")
            share_lan = item.get("share_lan", False)
            if not isinstance(share_lan, bool):
                raise AgentError(f"客户端 {name} 的内网共享选项无效")
            normalized = normalize_client_network(
                item.get("lan_subnet"),
                share_lan,
                self.config["vpn_subnet"],
                networks,
                name,
            )
            if normalized:
                networks[name] = normalized
        return networks

    def _write_client_networks(self, networks: dict[str, dict[str, Any]]) -> None:
        self._atomic_json(
            self.client_networks_path,
            networks,
            group_name=self.web_group,
        )

    def _manager_version(self) -> str:
        try:
            value = load_json(self.install_state_path).get("version")
        except AgentError:
            return "未知"
        return str(value or "未知")

    def server_settings(self) -> dict[str, Any]:
        return {
            "endpoint": self.config["endpoint"],
            "vpn_port": int(self.config["vpn_port"]),
            "vpn_protocol": self.config.get("vpn_protocol", "udp"),
            "vpn_subnet": self.config["vpn_subnet"],
            "dns_servers": list(self.config.get("dns_servers", [])),
            "redirect_gateway": bool(self.config.get("redirect_gateway", True)),
            "max_clients": int(self.config.get("max_clients", 100)),
            "web_port": int(self.config["web_port"]),
            "web_allow": self.config.get("web_allow", "0.0.0.0/0"),
        }

    def list_clients(self) -> list[dict[str, Any]]:
        online_records = self._read_status()
        online = {item["name"]: item for item in online_records}
        networks = self._read_client_networks()
        clients = parse_index(self._read_index(), self.profiles_dir, online)
        for client in clients:
            network = networks.get(client["name"], {})
            client["lan_subnet"] = network.get("lan_subnet")
            client["share_lan"] = bool(network.get("share_lan", False))
        return clients

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
            "dns_servers": list(self.config.get("dns_servers", [])),
            "redirect_gateway": bool(self.config.get("redirect_gateway", True)),
            "max_clients": int(self.config.get("max_clients", 100)),
            "manager_version": self._manager_version(),
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

    def _write_ccd_files(
        self,
        networks: dict[str, dict[str, Any]],
        active_clients: list[str],
    ) -> None:
        ccd_dir = self.openvpn_dir / "ccd"
        ccd_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        # OpenVPN reads CCD files when a client connects, after dropping to
        # nobody:nogroup.  Keep the directory private from other users while
        # allowing the runtime group to traverse it and read each client file.
        os.chown(ccd_dir, 0, group_gid("nogroup"))
        os.chmod(ccd_dir, 0o750)
        marker = "# Managed by openvpn-web-manager"
        rendered = render_ccd_configs(networks, active_clients)
        expected = set(rendered)
        for candidate in ccd_dir.iterdir():
            if not candidate.is_file() or candidate.name in expected:
                continue
            try:
                first_line = candidate.read_text(encoding="utf-8", errors="replace").splitlines()[0]
            except (OSError, IndexError):
                continue
            if first_line == marker:
                candidate.unlink()
        for name, content in rendered.items():
            self._atomic_text(ccd_dir / name, content, mode=0o640, group_name="nogroup")

    def _write_firewall_files(self, networks: dict[str, dict[str, Any]]) -> None:
        values = {
            "PUBLIC_INTERFACE": self.config["public_interface"],
            "VPN_INTERFACE": self.config.get("tunnel_interface", "tun0"),
            "VPN_SUBNET": self.config["vpn_subnet"],
            "VPN_PORT": str(self.config["vpn_port"]),
            "VPN_PROTOCOL": self.config.get("vpn_protocol", "udp"),
            "WEB_PORT": str(self.config["web_port"]),
            "WEB_ALLOW": self.config.get("web_allow", "0.0.0.0/0"),
            "CLIENT_ROUTES_FILE": str(self.firewall_routes_path),
        }
        env_content = "".join(f"{key}={shlex.quote(str(value))}\n" for key, value in values.items())
        self._atomic_text(self.firewall_env_path, env_content, mode=0o600)
        route_lines = [
            f"{item['lan_subnet']} {'allow' if item.get('share_lan') else 'deny'}"
            for _name, item in sorted(networks.items())
        ]
        self._atomic_text(
            self.firewall_routes_path,
            "\n".join(route_lines) + ("\n" if route_lines else ""),
            mode=0o600,
        )

    def sync_runtime(self, *, restart: bool = False) -> dict[str, Any]:
        networks = self._read_client_networks()
        clients = parse_index(self._read_index(), self.profiles_dir)
        active_clients = [item["name"] for item in clients if item["status"] == "active"]
        self._atomic_text(
            self.openvpn_dir / "server.conf",
            render_server_config(self.config, networks),
            mode=0o600,
        )
        self._write_ccd_files(networks, active_clients)
        self._write_firewall_files(networks)
        for client in clients:
            if client["status"] == "active":
                self._atomic_profile(client["name"], render_profile(self.config, client["name"]))
        if restart:
            self.run(["systemctl", "restart", "openvpn-manager-firewall.service"], timeout=60)
            return self.restart()
        return {"synced": True, "synced_at": utc_now()}

    def _apply_client_networks(self, networks: dict[str, dict[str, Any]]) -> None:
        old_networks = self._read_client_networks()
        try:
            self._write_client_networks(networks)
            self.sync_runtime(restart=True)
        except (AgentError, OSError) as exc:
            try:
                self._write_client_networks(old_networks)
                self.sync_runtime(restart=True)
            except (AgentError, OSError):
                pass
            raise AgentError(f"客户端内网配置应用失败，已尝试回滚：{exc}") from exc

    def create_client(self, name: Any, lan_subnet: Any = "", share_lan: Any = False) -> dict[str, Any]:
        name = validate_client_name(name)
        with MUTATION_LOCK:
            clients = {item["name"] for item in self.list_clients()}
            if name in clients:
                raise AgentError(f"客户端 {name} 已存在或曾经被吊销")
            networks = self._read_client_networks()
            network = normalize_client_network(
                lan_subnet,
                share_lan,
                self.config["vpn_subnet"],
                networks,
                name,
            )
            self.run([str(self.easy_rsa_dir / "easyrsa"), "--batch", "build-client-full", name, "nopass"], cwd=self.easy_rsa_dir)
            self._atomic_profile(name, render_profile(self.config, name))
            if network:
                networks[name] = network
                self._apply_client_networks(networks)
            else:
                # A new ordinary client still needs CCD route pushes for every shared branch LAN.
                self.sync_runtime(restart=False)
            created = next((item for item in self.list_clients() if item["name"] == name), None)
            if not created:
                raise AgentError("证书已创建，但无法从索引中读取")
            return created

    def set_client_network(self, name: Any, lan_subnet: Any, share_lan: Any) -> dict[str, Any]:
        name = validate_client_name(name)
        with MUTATION_LOCK:
            clients = {item["name"]: item for item in self.list_clients()}
            if name not in clients or clients[name]["status"] != "active":
                raise AgentError("只能修改有效客户端的下级内网")
            networks = self._read_client_networks()
            normalized = normalize_client_network(
                lan_subnet,
                share_lan,
                self.config["vpn_subnet"],
                networks,
                name,
            )
            updated = dict(networks)
            if normalized:
                updated[name] = normalized
            else:
                updated.pop(name, None)
            self._apply_client_networks(updated)
            client = next(item for item in self.list_clients() if item["name"] == name)
            return client

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
            networks = self._read_client_networks()
            networks.pop(name, None)
            self._write_client_networks(networks)
            self.sync_runtime(restart=True)
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
        return {
            "name": name,
            "filename": f"{name}.ovpn",
            "content": content,
            "ikuai": {
                "dial_name": name,
                "server": self.config["endpoint"],
                "port": int(self.config["vpn_port"]),
                "protocol": str(self.config.get("vpn_protocol", "udp")).upper(),
                "authentication": "静态密钥（tls-crypt）",
                "ca_certificate": extract_pem(self.easy_rsa_dir / "pki" / "ca.crt", "CERTIFICATE"),
                "client_certificate": extract_pem(
                    self.easy_rsa_dir / "pki" / "issued" / f"{name}.crt", "CERTIFICATE"
                ),
                "private_key": extract_private_key(
                    self.easy_rsa_dir / "pki" / "private" / f"{name}.key"
                ),
                "tls_crypt_key": extract_pem(
                    self.openvpn_dir / "tls-crypt.key", "OpenVPN Static key V1"
                ),
            },
        }

    def _management_command(self, command: str) -> str:
        if "\n" in command or "\r" in command:
            raise AgentError("管理命令格式无效")
        chunks: list[bytes] = []
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(5)
                client.connect(str(self.management_socket))
                try:
                    client.recv(4096)
                except TimeoutError:
                    pass
                client.sendall((command + "\n").encode("utf-8"))
                total = 0
                while total < 64_000:
                    block = client.recv(4096)
                    if not block:
                        break
                    chunks.append(block)
                    total += len(block)
                    text = b"".join(chunks)
                    if b"SUCCESS:" in text or b"ERROR:" in text:
                        break
                try:
                    client.sendall(b"quit\n")
                except OSError:
                    pass
        except (OSError, TimeoutError) as exc:
            raise AgentError(f"无法连接 OpenVPN 管理接口：{exc}") from exc
        response = b"".join(chunks).decode("utf-8", errors="replace").strip()
        if "ERROR:" in response or "SUCCESS:" not in response:
            raise AgentError(response or "OpenVPN 管理接口没有返回成功结果")
        return response

    def disconnect_client(self, name: Any) -> dict[str, Any]:
        name = validate_client_name(name)
        online = [item for item in self._read_status() if item["name"] == name]
        if not online:
            raise AgentError(f"客户端 {name} 当前不在线")
        result = self._management_command(f"kill {name}")
        return {
            "name": name,
            "disconnected": True,
            "sessions": len(online),
            "message": result.splitlines()[-1],
            "disconnected_at": utc_now(),
        }

    def update_server_settings(self, requested: Any) -> dict[str, Any]:
        if not isinstance(requested, dict):
            raise AgentError("服务端设置必须是 JSON 对象")
        with MUTATION_LOCK:
            old_config = dict(self.config)
            new_config = normalize_server_settings(self.config, requested)
            subnet_changed = old_config["vpn_subnet"] != new_config["vpn_subnet"]
            old_ipp: str | None = None
            if subnet_changed:
                try:
                    old_ipp = self.ipp_path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise AgentError(f"无法读取现有客户端地址池：{exc}") from exc
            networks = self._read_client_networks()
            for name, item in networks.items():
                normalize_client_network(
                    item["lan_subnet"],
                    bool(item.get("share_lan", False)),
                    new_config["vpn_subnet"],
                    networks,
                    name,
                )
            try:
                self.config = new_config
                self._atomic_json(pathlib.Path(CONFIG_PATH), self.config, group_name=self.web_group)
                if subnet_changed:
                    try:
                        self.ipp_path.unlink()
                    except FileNotFoundError:
                        pass
                status = self.sync_runtime(restart=True)
            except (AgentError, OSError) as exc:
                self.config = old_config
                try:
                    self._atomic_json(pathlib.Path(CONFIG_PATH), self.config, group_name=self.web_group)
                    if subnet_changed:
                        if old_ipp is None:
                            try:
                                self.ipp_path.unlink()
                            except FileNotFoundError:
                                pass
                        else:
                            self._atomic_text(self.ipp_path, old_ipp, mode=0o660, group_name="nogroup")
                    self.sync_runtime(restart=True)
                except (AgentError, OSError):
                    pass
                raise AgentError(f"服务端设置应用失败，已尝试回滚：{exc}") from exc
            return {"settings": self.server_settings(), "status": status}

    def update_status(self) -> dict[str, Any]:
        try:
            status = load_json(self.update_status_path)
        except AgentError:
            status = {"state": "idle", "message": "尚未执行在线更新"}
        status["manager_version"] = self._manager_version()
        try:
            status["openvpn_version"] = self.run(["openvpn", "--version"], timeout=15).splitlines()[0]
        except (AgentError, IndexError):
            status["openvpn_version"] = "OpenVPN"
        return status

    def start_update(self) -> dict[str, Any]:
        completed = subprocess.run(
            ["systemctl", "is-active", self.update_service_name],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
        if completed.stdout.strip() in {"active", "activating"}:
            raise AgentError("更新任务正在运行，请勿重复提交")
        request = {"mode": "all", "requested_at": utc_now()}
        self._atomic_json(self.update_request_path, request, mode=0o600)
        self._atomic_json(
            self.update_status_path,
            {"state": "queued", "message": "更新任务已排队", "updated_at": utc_now()},
            mode=0o640,
            group_name=self.web_group,
        )
        subprocess.run(
            ["systemctl", "reset-failed", self.update_service_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
        self.run(["systemctl", "--no-block", "start", self.update_service_name], timeout=15)
        return self.update_status()

    def set_web_password(self, encoded_hash: Any) -> dict[str, Any]:
        if not isinstance(encoded_hash, str) or not PASSWORD_HASH_RE.fullmatch(encoded_hash):
            raise AgentError("密码哈希格式无效")
        with MUTATION_LOCK:
            web_config = load_json(WEB_CONFIG_PATH)
            web_config["password_hash"] = encoded_hash
            self._atomic_json(pathlib.Path(WEB_CONFIG_PATH), web_config, group_name=self.web_group)
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
            return self.create_client(
                request.get("name"),
                request.get("lan_subnet", ""),
                request.get("share_lan", False),
            )
        if action == "set_client_network":
            return self.set_client_network(
                request.get("name"),
                request.get("lan_subnet", ""),
                request.get("share_lan", False),
            )
        if action == "disconnect_client":
            return self.disconnect_client(request.get("name"))
        if action == "revoke_client":
            return self.revoke_client(request.get("name"))
        if action == "server_settings":
            return self.server_settings()
        if action == "update_server_settings":
            return self.update_server_settings(request.get("settings"))
        if action == "restart":
            return self.restart()
        if action == "logs":
            return self.logs(request.get("lines", 100))
        if action == "get_profile":
            return self.get_profile(request.get("name"))
        if action == "set_web_password":
            return self.set_web_password(request.get("password_hash"))
        if action == "update_status":
            return self.update_status()
        if action == "start_update":
            return self.start_update()
        if action == "sync_runtime":
            return self.sync_runtime(restart=bool(request.get("restart", False)))
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
    parser.add_argument(
        "--direct",
        choices=[
            "status",
            "overview",
            "list_clients",
            "create_client",
            "set_client_network",
            "disconnect_client",
            "revoke_client",
            "restart",
            "logs",
            "sync_runtime",
            "update_status",
            "start_update",
        ],
    )
    parser.add_argument("--name")
    parser.add_argument("--lines", type=int, default=100)
    parser.add_argument("--lan-subnet", default="")
    parser.add_argument("--share-lan", action="store_true")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    try:
        controller = OpenVPNController(load_json(CONFIG_PATH))
        if args.direct:
            payload: dict[str, Any] = {
                "action": args.direct,
                "lines": args.lines,
                "lan_subnet": args.lan_subnet,
                "share_lan": args.share_lan,
                "restart": args.restart,
            }
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
