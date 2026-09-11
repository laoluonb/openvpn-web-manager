#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="openvpn-web-manager"
VERSION="1.0.0"
INSTALL_DIR="/opt/$APP_NAME"
CONFIG_DIR="/etc/$APP_NAME"
STATE_DIR="/var/lib/$APP_NAME"
OPENVPN_DIR="/etc/openvpn/server"
EASYRSA_DIR="$OPENVPN_DIR/easy-rsa"
WEB_USER="openvpn-web"
WEB_GROUP="openvpn-web"
VPN_SUBNET="10.8.0.0/24"
VPN_NETWORK="10.8.0.0"
VPN_NETMASK="255.255.255.0"

ENDPOINT=""
VPN_PORT="1194"
WEB_PORT="8443"
WEB_ALLOW="0.0.0.0/0"
ADMIN_USER="admin"
ADMIN_PASSWORD=""
PASSWORD_EXPLICIT="0"
INITIAL_CLIENT="admin"
TLS_CERT=""
TLS_KEY=""

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
OpenVPN 管理中心安装器

用法：sudo ./install.sh [选项]

选项：
  --endpoint HOST          公网 IPv4 地址或域名
  --vpn-port PORT          OpenVPN UDP 端口（默认：1194）
  --web-port PORT          HTTPS 管理端口（默认：8443）
  --web-allow CIDR         允许访问控制台的来源（默认：0.0.0.0/0）
  --admin-user USER        控制台用户名（默认：admin）
  --admin-password PASS    控制台密码（省略时自动生成）
  --initial-client NAME    首个客户端名称（默认：admin）
  --tls-cert PATH          已有 PEM 证书（必须同时指定 --tls-key）
  --tls-key PATH           已有 PEM 私钥（必须同时指定 --tls-cert）
  -h, --help               显示帮助
EOF
}

log() { printf '\033[1;36m[openvpn-manager]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warning]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --endpoint) ENDPOINT="${2:-}"; shift 2 ;;
    --vpn-port) VPN_PORT="${2:-}"; shift 2 ;;
    --web-port) WEB_PORT="${2:-}"; shift 2 ;;
    --web-allow) WEB_ALLOW="${2:-}"; shift 2 ;;
    --admin-user) ADMIN_USER="${2:-}"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="${2:-}"; PASSWORD_EXPLICIT="1"; shift 2 ;;
    --initial-client) INITIAL_CLIENT="${2:-}"; shift 2 ;;
    --tls-cert) TLS_CERT="${2:-}"; shift 2 ;;
    --tls-key) TLS_KEY="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知选项：$1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "请以 root 身份运行安装器，例如：sudo ./install.sh。"
[[ -f "$SCRIPT_DIR/backend/agent.py" && -f "$SCRIPT_DIR/web/index.html" ]] || \
  die "请在完整的项目目录中运行 install.sh。"
[[ -d /run/systemd/system ]] || die "系统必须使用 systemd。"
if [[ ! "$VPN_PORT" =~ ^[0-9]+$ ]] || ((VPN_PORT < 1 || VPN_PORT > 65535)); then
  die "VPN 端口无效。"
fi
if [[ ! "$WEB_PORT" =~ ^[0-9]+$ ]] || ((WEB_PORT < 1 || WEB_PORT > 65535)); then
  die "管理端口无效。"
fi
[[ "$VPN_PORT" != "$WEB_PORT" ]] || die "VPN 端口和管理端口不能相同。"
[[ "$ADMIN_USER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$ ]] || die "管理员用户名无效。"
[[ "$INITIAL_CLIENT" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$ ]] || die "首个客户端名称无效。"
[[ "$WEB_ALLOW" =~ ^[0-9./]+$ ]] || die "--web-allow 必须是 IPv4 CIDR。"
python3 - "$WEB_ALLOW" <<'PY' || die "--web-allow 必须是有效的 IPv4 CIDR。"
import ipaddress, sys
network = ipaddress.ip_network(sys.argv[1], strict=False)
if network.version != 4:
    raise ValueError("必须使用 IPv4")
PY
if [[ -n "$ENDPOINT" ]]; then
  [[ "$ENDPOINT" =~ ^[A-Za-z0-9.-]+$ ]] || die "公网地址必须是域名或 IPv4 地址。"
fi
if [[ -n "$TLS_CERT" || -n "$TLS_KEY" ]]; then
  [[ -r "$TLS_CERT" && -r "$TLS_KEY" ]] || die "--tls-cert 和 --tls-key 必须同时存在且可读。"
fi

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
else
  die "无法识别操作系统。"
fi
case "${ID:-}:${ID_LIKE:-}" in
  debian:*|ubuntu:*|*:debian*) ;;
  *) die "安装器当前仅支持 Debian 和 Ubuntu。" ;;
esac

export DEBIAN_FRONTEND=noninteractive
log "正在安装系统软件包"
apt-get update -y
apt-get install -y --no-install-recommends \
  openvpn easy-rsa python3 nginx openssl iptables iproute2 ca-certificates curl

if [[ -z "$ENDPOINT" ]]; then
  ENDPOINT="$(curl -4fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"
  if [[ ! "$ENDPOINT" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    ENDPOINT="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')"
    warn "公网 IP 探测失败，将使用路由出口地址 $ENDPOINT。"
  fi
fi
[[ "$ENDPOINT" =~ ^[A-Za-z0-9.-]+$ ]] || die "无法确定有效的公网地址，请使用 --endpoint 指定。"

PUBLIC_INTERFACE="$(ip -4 route show default | awk '/default/ {print $5; exit}')"
[[ "$PUBLIC_INTERFACE" =~ ^[A-Za-z0-9_.:@-]+$ ]] || die "无法确定公网网络接口。"

if [[ "$PASSWORD_EXPLICIT" == "1" && ${#ADMIN_PASSWORD} -lt 12 ]]; then
  die "控制台密码至少需要 12 个字符。"
fi

umask 077
BACKUP_ROOT="/var/backups/$APP_NAME"
BACKUP_DIR="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -e "$CONFIG_DIR" || -e "$OPENVPN_DIR/server.conf" ]]; then
  log "正在备份现有配置到 $BACKUP_DIR"
  install -d -m 0700 "$BACKUP_DIR"
  [[ -d "$CONFIG_DIR" ]] && cp -a "$CONFIG_DIR" "$BACKUP_DIR/manager-config"
  [[ -f "$OPENVPN_DIR/server.conf" ]] && cp -a "$OPENVPN_DIR/server.conf" "$BACKUP_DIR/server.conf"
  if [[ -d "$EASYRSA_DIR/pki" ]]; then
    tar -C "$EASYRSA_DIR" -czf "$BACKUP_DIR/pki.tar.gz" pki
  fi
fi

log "正在安装应用文件"
getent group "$WEB_GROUP" >/dev/null || groupadd --system "$WEB_GROUP"
id -u "$WEB_USER" >/dev/null 2>&1 || \
  useradd --system --gid "$WEB_GROUP" --home-dir /nonexistent --shell /usr/sbin/nologin "$WEB_USER"

install -d -m 0755 "$INSTALL_DIR" "$INSTALL_DIR/backend" "$INSTALL_DIR/web"
cp -a "$SCRIPT_DIR/backend/." "$INSTALL_DIR/backend/"
cp -a "$SCRIPT_DIR/web/." "$INSTALL_DIR/web/"
find "$INSTALL_DIR" -type d -exec chmod 0755 {} +
find "$INSTALL_DIR" -type f -exec chmod 0644 {} +

install -d -m 0750 -o root -g "$WEB_GROUP" "$CONFIG_DIR" "$CONFIG_DIR/tls"
install -d -m 0750 -o root -g "$WEB_GROUP" "$STATE_DIR" "$STATE_DIR/clients"
install -d -m 0750 "$OPENVPN_DIR" "$OPENVPN_DIR/ccd" "$EASYRSA_DIR"
install -d -m 0770 -o root -g nogroup /var/log/openvpn /var/lib/openvpn/server

if [[ ! -f "$EASYRSA_DIR/easyrsa" ]]; then
  cp -a /usr/share/easy-rsa/. "$EASYRSA_DIR/"
fi
chmod 0755 "$EASYRSA_DIR/easyrsa"

log "正在初始化证书颁发机构和服务器身份"
pushd "$EASYRSA_DIR" >/dev/null
export EASYRSA_BATCH=1
if [[ ! -f pki/ca.crt ]]; then
  ./easyrsa init-pki
  EASYRSA_REQ_CN="OpenVPN Web Manager CA" ./easyrsa build-ca nopass
fi
if [[ ! -f pki/issued/server.crt || ! -f pki/private/server.key ]]; then
  ./easyrsa build-server-full server nopass
fi
./easyrsa gen-crl
popd >/dev/null

install -m 0644 "$EASYRSA_DIR/pki/ca.crt" "$OPENVPN_DIR/ca.crt"
install -m 0644 "$EASYRSA_DIR/pki/issued/server.crt" "$OPENVPN_DIR/server.crt"
install -m 0600 "$EASYRSA_DIR/pki/private/server.key" "$OPENVPN_DIR/server.key"
install -m 0644 "$EASYRSA_DIR/pki/crl.pem" "$OPENVPN_DIR/crl.pem"
if [[ ! -f "$OPENVPN_DIR/tls-crypt.key" ]]; then
  openvpn --genkey secret "$OPENVPN_DIR/tls-crypt.key"
fi
chmod 0600 "$OPENVPN_DIR/tls-crypt.key"

cat >"$OPENVPN_DIR/server.conf" <<EOF
port $VPN_PORT
proto udp
dev tun
topology subnet
server $VPN_NETWORK $VPN_NETMASK
ifconfig-pool-persist /var/lib/openvpn/server/ipp.txt
client-config-dir $OPENVPN_DIR/ccd

ca $OPENVPN_DIR/ca.crt
cert $OPENVPN_DIR/server.crt
key $OPENVPN_DIR/server.key
dh none
crl-verify $OPENVPN_DIR/crl.pem
tls-crypt $OPENVPN_DIR/tls-crypt.key
remote-cert-tls client
tls-version-min 1.2

data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
data-ciphers-fallback AES-256-GCM
auth SHA256

push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 1.1.1.1"
push "dhcp-option DNS 9.9.9.9"

keepalive 10 120
persist-key
persist-tun
user nobody
group nogroup
explicit-exit-notify 1
max-clients 100
status /var/log/openvpn/status.log 10
status-version 3
verb 3
EOF
chmod 0600 "$OPENVPN_DIR/server.conf"

python3 - "$CONFIG_DIR/server.json" "$ENDPOINT" "$VPN_PORT" "$WEB_PORT" "$PUBLIC_INTERFACE" "$VPN_SUBNET" "$WEB_GROUP" <<'PY'
import json, pathlib, sys
path, endpoint, vpn_port, web_port, interface, subnet, web_group = sys.argv[1:]
data = {
    "endpoint": endpoint,
    "vpn_port": int(vpn_port),
    "vpn_protocol": "udp",
    "web_port": int(web_port),
    "public_interface": interface,
    "vpn_subnet": subnet,
    "server_name": "server",
    "easy_rsa_dir": "/etc/openvpn/server/easy-rsa",
    "openvpn_dir": "/etc/openvpn/server",
    "state_dir": "/var/lib/openvpn-manager",
    "status_file": "/var/log/openvpn/status.log",
    "service_name": "openvpn-server@server.service",
    "web_group": web_group,
}
pathlib.Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
chown root:"$WEB_GROUP" "$CONFIG_DIR/server.json"
chmod 0640 "$CONFIG_DIR/server.json"

log "正在配置控制台认证"
WEB_CONFIG_EXISTS="0"
if [[ -s "$CONFIG_DIR/web.json" && "$PASSWORD_EXPLICIT" == "0" ]]; then
  WEB_CONFIG_EXISTS="1"
  warn "已保留现有控制台密码。"
else
  if [[ -z "$ADMIN_PASSWORD" ]]; then
    ADMIN_PASSWORD="$(openssl rand -hex 16)"
  fi
  PASSWORD_HASH="$(printf '%s' "$ADMIN_PASSWORD" | python3 "$INSTALL_DIR/backend/server.py" --hash-password-stdin)"
  SESSION_SECRET="$(openssl rand -hex 32)"
  python3 - "$CONFIG_DIR/web.json" "$ADMIN_USER" "$PASSWORD_HASH" "$SESSION_SECRET" <<'PY'
import json, pathlib, sys
path, user, password_hash, secret = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({
    "admin_user": user,
    "password_hash": password_hash,
    "session_secret": secret,
    "session_hours": 8,
}, indent=2) + "\n", encoding="utf-8")
PY
fi
chown root:"$WEB_GROUP" "$CONFIG_DIR/web.json"
chmod 0640 "$CONFIG_DIR/web.json"

cat >"$CONFIG_DIR/firewall.env" <<EOF
PUBLIC_INTERFACE=$(printf '%q' "$PUBLIC_INTERFACE")
VPN_SUBNET=$(printf '%q' "$VPN_SUBNET")
VPN_PORT=$(printf '%q' "$VPN_PORT")
VPN_PROTOCOL=udp
WEB_PORT=$(printf '%q' "$WEB_PORT")
WEB_ALLOW=$(printf '%q' "$WEB_ALLOW")
EOF
chmod 0600 "$CONFIG_DIR/firewall.env"

cat >/etc/sysctl.d/99-openvpn-manager.conf <<'EOF'
net.ipv4.ip_forward = 1
EOF
sysctl --system >/dev/null

log "正在配置 HTTPS"
if [[ -n "$TLS_CERT" ]]; then
  install -m 0644 "$TLS_CERT" "$CONFIG_DIR/tls/server.crt"
  install -m 0600 "$TLS_KEY" "$CONFIG_DIR/tls/server.key"
else
  if [[ ! -s "$CONFIG_DIR/tls/server.crt" || ! -s "$CONFIG_DIR/tls/server.key" ]]; then
    SAN_KIND="DNS"
    [[ "$ENDPOINT" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && SAN_KIND="IP"
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
      -keyout "$CONFIG_DIR/tls/server.key" \
      -out "$CONFIG_DIR/tls/server.crt" \
      -subj "/CN=$ENDPOINT" \
      -addext "subjectAltName=$SAN_KIND:$ENDPOINT" >/dev/null 2>&1
  fi
fi
chown root:"$WEB_GROUP" "$CONFIG_DIR/tls/server.key" "$CONFIG_DIR/tls/server.crt"
chmod 0640 "$CONFIG_DIR/tls/server.key"
chmod 0644 "$CONFIG_DIR/tls/server.crt"

sed "s/__WEB_PORT__/$WEB_PORT/g" "$SCRIPT_DIR/config/nginx-site.conf.tpl" \
  >"/etc/nginx/sites-available/$APP_NAME"
ln -sfn "/etc/nginx/sites-available/$APP_NAME" "/etc/nginx/sites-enabled/$APP_NAME"
rm -f /etc/nginx/sites-enabled/default
nginx -t

log "正在安装系统服务和命令行工具"
install -m 0755 "$SCRIPT_DIR/scripts/openvpn-manager-firewall" /usr/local/sbin/openvpn-manager-firewall
install -m 0755 "$SCRIPT_DIR/scripts/openvpn-managerctl" /usr/local/bin/openvpn-managerctl
install -m 0644 "$SCRIPT_DIR/config/openvpn-manager-firewall.service" /etc/systemd/system/openvpn-manager-firewall.service
install -m 0644 "$SCRIPT_DIR/config/openvpn-manager-agent.service" /etc/systemd/system/openvpn-manager-agent.service
install -m 0644 "$SCRIPT_DIR/config/openvpn-web-manager.service" /etc/systemd/system/openvpn-web-manager.service
install -d -m 0755 /etc/systemd/system/openvpn-server@server.service.d
install -m 0644 "$SCRIPT_DIR/config/openvpn-service-override.conf" \
  /etc/systemd/system/openvpn-server@server.service.d/openvpn-manager.conf

systemctl daemon-reload
systemctl enable --now openvpn-manager-firewall.service
systemctl enable --now openvpn-server@server.service

log "正在创建首个客户端配置"
python3 "$INSTALL_DIR/backend/agent.py" --direct create_client --name "$INITIAL_CLIENT" || \
  warn "首个客户端配置已存在或无法重新创建，请检查上方代理输出。"

systemctl enable --now openvpn-manager-agent.service
systemctl enable --now openvpn-web-manager.service
systemctl enable --now nginx.service
systemctl restart nginx.service openvpn-web-manager.service

sleep 1
systemctl is-active --quiet openvpn-server@server.service || die "OpenVPN 启动失败，请运行 journalctl -u openvpn-server@server 查看日志。"
systemctl is-active --quiet openvpn-manager-agent.service || die "管理代理启动失败。"
systemctl is-active --quiet openvpn-web-manager.service || die "管理 Web 服务启动失败。"

CREDENTIAL_FILE="/root/openvpn-manager-credentials.txt"
if [[ "$WEB_CONFIG_EXISTS" == "0" ]]; then
  cat >"$CREDENTIAL_FILE" <<EOF
OpenVPN 管理中心 $VERSION
管理地址：https://$ENDPOINT:$WEB_PORT
用户名：$ADMIN_USER
密码：$ADMIN_PASSWORD
首个配置：$STATE_DIR/clients/$INITIAL_CLIENT.ovpn
创建时间：$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  chmod 0600 "$CREDENTIAL_FILE"
fi

printf '\n\033[1;32m安装完成。\033[0m\n'
printf '  管理地址：       https://%s:%s\n' "$ENDPOINT" "$WEB_PORT"
printf '  OpenVPN：       %s:%s/udp\n' "$ENDPOINT" "$VPN_PORT"
printf '  首个客户端：     %s/clients/%s.ovpn\n' "$STATE_DIR" "$INITIAL_CLIENT"
if [[ "$WEB_CONFIG_EXISTS" == "0" ]]; then
  printf '  用户名：         %s\n' "$ADMIN_USER"
  printf '  密码：           %s\n' "$ADMIN_PASSWORD"
  printf '  凭据文件：       %s\n' "$CREDENTIAL_FILE"
else
  printf '  凭据文件：       未变更（已保留现有 web.json）\n'
fi
printf '\n'
if [[ "$WEB_ALLOW" == "0.0.0.0/0" ]]; then
  warn "当前允许任意 IPv4 来源访问控制台，请使用 --web-allow 或上游防火墙限制来源。"
fi
if [[ -z "$TLS_CERT" ]]; then
  warn "已安装自签名 TLS 证书，生产环境请替换为受信任证书。"
fi
