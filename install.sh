#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="openvpn-web-manager"
VERSION="1.2.9"
INSTALL_DIR="/opt/$APP_NAME"
CONFIG_DIR="/etc/openvpn-manager"
STATE_DIR="/var/lib/openvpn-manager"
LEGACY_CONFIG_DIR="/etc/openvpn-web-manager"
LEGACY_STATE_DIR="/var/lib/openvpn-web-manager"
OPENVPN_DIR="/etc/openvpn/server"
EASYRSA_DIR="$OPENVPN_DIR/easy-rsa"
CREDENTIAL_FILE="/root/openvpn-manager-credentials.txt"
WEB_USER="openvpn-web"
WEB_GROUP="openvpn-web"
CLI_COMMAND="/usr/local/bin/openvpn-manager"
LEGACY_CLI_COMMAND="/usr/local/bin/openvpn-managerctl"
LEGACY_CLI_ALIAS="/usr/local/bin/bt"
VPN_SUBNET="10.8.0.0/24"
VPN_PROTOCOL="udp"
DNS_SERVERS="1.1.1.1,9.9.9.9"
REDIRECT_GATEWAY="yes"
MAX_CLIENTS="100"
TUN_MTU="1500"
MSSFIX="1450"
KEEPALIVE_PING="10"
KEEPALIVE_TIMEOUT="120"
DATA_CIPHER="AES-256-GCM"
AUTH_DIGEST="SHA256"
LOG_VERB="3"
PUSH_ROUTES=""
OLD_VPN_PORT=""
OLD_VPN_SUBNET=""

ENDPOINT=""
VPN_PORT="random"
WEB_PORT="8443"
WEB_ALLOW="0.0.0.0/0"
ADMIN_USER="admin"
ADMIN_PASSWORD=""
PASSWORD_EXPLICIT="0"
INITIAL_CLIENT="admin"
TLS_CERT=""
TLS_KEY=""
EXISTING_ACTION="auto"
EXISTING_BACKUP=""
MANAGED_EXISTING="0"
OPENVPN_CONFIG_PRESENT="0"
OPENVPN_PACKAGE_PRESENT="0"
ENDPOINT_EXPLICIT="0"
VPN_PORT_EXPLICIT="0"
VPN_PROTOCOL_EXPLICIT="0"
VPN_SUBNET_EXPLICIT="0"
DNS_SERVERS_EXPLICIT="0"
REDIRECT_GATEWAY_EXPLICIT="0"
MAX_CLIENTS_EXPLICIT="0"
TUN_MTU_EXPLICIT="0"
MSSFIX_EXPLICIT="0"
KEEPALIVE_PING_EXPLICIT="0"
KEEPALIVE_TIMEOUT_EXPLICIT="0"
DATA_CIPHER_EXPLICIT="0"
AUTH_DIGEST_EXPLICIT="0"
LOG_VERB_EXPLICIT="0"
PUSH_ROUTES_EXPLICIT="0"
WEB_PORT_EXPLICIT="0"
WEB_ALLOW_EXPLICIT="0"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
OpenVPN 管理中心安装器

用法：sudo ./install.sh [选项]

选项：
  --endpoint HOST          公网 IPv4 地址或域名
  --vpn-port PORT          OpenVPN 服务端口（全新安装默认随机 10000-29999；可传 random）
  --vpn-protocol PROTO     OpenVPN 协议：udp 或 tcp（默认：udp）
  --vpn-subnet CIDR        VPN 私有子网（默认：10.8.0.0/24）
  --dns-servers LIST       推送的 DNS，逗号分隔（默认：1.1.1.1,9.9.9.9）
  --redirect-gateway MODE  是否转发客户端全部流量：yes 或 no（默认：yes）
  --max-clients COUNT      最大并发客户端数（默认：100）
  --tun-mtu MTU            TUN MTU（默认：1500）
  --mssfix MTU             MSS Fix，0 表示关闭（默认：1450）
  --keepalive-ping SEC     Keepalive 检测间隔（默认：10）
  --keepalive-timeout SEC  Keepalive 超时（默认：120）
  --data-cipher CIPHER     首选数据加密：AES-256-GCM、AES-128-GCM 或 CHACHA20-POLY1305
  --auth-digest DIGEST     HMAC 摘要：SHA256、SHA384 或 SHA512（默认：SHA256）
  --log-verb LEVEL         OpenVPN 日志等级 0-11（默认：3）
  --push-routes LIST       推送给客户端的私有 CIDR，逗号或空格分隔
  --web-port PORT          HTTPS 管理端口（默认：8443）
  --web-allow CIDR         允许访问控制台的来源（默认：0.0.0.0/0）
  --admin-user USER        控制台用户名（默认：admin）
  --admin-password PASS    控制台密码（省略时自动生成）
  --initial-client NAME    首个客户端名称（默认：admin）
  --tls-cert PATH          已有 PEM 证书（必须同时指定 --tls-key）
  --tls-key PATH           已有 PEM 私钥（必须同时指定 --tls-cert）
  --existing-action MODE   已有 OpenVPN 的处理方式：preserve、remove 或 abort
                           默认：交互终端询问；非交互环境自动 preserve
  -h, --help               显示帮助
EOF
}

log() { printf '\033[1;36m[openvpn-manager]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warning]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --endpoint) ENDPOINT="${2:-}"; ENDPOINT_EXPLICIT="1"; shift 2 ;;
    --vpn-port) VPN_PORT="${2:-}"; VPN_PORT_EXPLICIT="1"; shift 2 ;;
    --vpn-protocol) VPN_PROTOCOL="${2:-}"; VPN_PROTOCOL_EXPLICIT="1"; shift 2 ;;
    --vpn-subnet) VPN_SUBNET="${2:-}"; VPN_SUBNET_EXPLICIT="1"; shift 2 ;;
    --dns-servers) DNS_SERVERS="${2:-}"; DNS_SERVERS_EXPLICIT="1"; shift 2 ;;
    --redirect-gateway) REDIRECT_GATEWAY="${2:-}"; REDIRECT_GATEWAY_EXPLICIT="1"; shift 2 ;;
    --max-clients) MAX_CLIENTS="${2:-}"; MAX_CLIENTS_EXPLICIT="1"; shift 2 ;;
    --tun-mtu) TUN_MTU="${2:-}"; TUN_MTU_EXPLICIT="1"; shift 2 ;;
    --mssfix) MSSFIX="${2:-}"; MSSFIX_EXPLICIT="1"; shift 2 ;;
    --keepalive-ping) KEEPALIVE_PING="${2:-}"; KEEPALIVE_PING_EXPLICIT="1"; shift 2 ;;
    --keepalive-timeout) KEEPALIVE_TIMEOUT="${2:-}"; KEEPALIVE_TIMEOUT_EXPLICIT="1"; shift 2 ;;
    --data-cipher) DATA_CIPHER="${2:-}"; DATA_CIPHER_EXPLICIT="1"; shift 2 ;;
    --auth-digest) AUTH_DIGEST="${2:-}"; AUTH_DIGEST_EXPLICIT="1"; shift 2 ;;
    --log-verb) LOG_VERB="${2:-}"; LOG_VERB_EXPLICIT="1"; shift 2 ;;
    --push-routes) PUSH_ROUTES="${2:-}"; PUSH_ROUTES_EXPLICIT="1"; shift 2 ;;
    --web-port) WEB_PORT="${2:-}"; WEB_PORT_EXPLICIT="1"; shift 2 ;;
    --web-allow) WEB_ALLOW="${2:-}"; WEB_ALLOW_EXPLICIT="1"; shift 2 ;;
    --admin-user) ADMIN_USER="${2:-}"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="${2:-}"; PASSWORD_EXPLICIT="1"; shift 2 ;;
    --initial-client) INITIAL_CLIENT="${2:-}"; shift 2 ;;
    --tls-cert) TLS_CERT="${2:-}"; shift 2 ;;
    --tls-key) TLS_KEY="${2:-}"; shift 2 ;;
    --existing-action) EXISTING_ACTION="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知选项：$1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "请以 root 身份运行安装器，例如：sudo ./install.sh。"
[[ -f "$SCRIPT_DIR/backend/agent.py" && -f "$SCRIPT_DIR/web/index.html" ]] || \
  die "请在完整的项目目录中运行 install.sh。"
[[ -d /run/systemd/system ]] || die "系统必须使用 systemd。"

validate_options() {
  [[ "${VPN_PORT,,}" != "random" ]] || VPN_PORT="random"
  if [[ "$VPN_PORT" != "random" && ! "$VPN_PORT" =~ ^[0-9]+$ ]]; then
    die "VPN 端口无效。"
  fi
  if [[ "$VPN_PORT" != "random" ]] && ((VPN_PORT < 1 || VPN_PORT > 65535)); then
    die "VPN 端口无效。"
  fi
  if [[ ! "$WEB_PORT" =~ ^[0-9]+$ ]] || ((WEB_PORT < 1 || WEB_PORT > 65535)); then
    die "管理端口无效。"
  fi
  [[ "$VPN_PORT" == "random" || "$VPN_PORT" != "$WEB_PORT" ]] || die "VPN 端口和管理端口不能相同。"
  case "${VPN_PROTOCOL,,}" in
    udp|tcp) VPN_PROTOCOL="${VPN_PROTOCOL,,}" ;;
    *) die "--vpn-protocol 只能是 udp 或 tcp。" ;;
  esac
  case "${REDIRECT_GATEWAY,,}" in
    yes|true|1) REDIRECT_GATEWAY="yes" ;;
    no|false|0) REDIRECT_GATEWAY="no" ;;
    *) die "--redirect-gateway 只能是 yes 或 no。" ;;
  esac
  if [[ ! "$MAX_CLIENTS" =~ ^[0-9]+$ ]] || ((MAX_CLIENTS < 1 || MAX_CLIENTS > 1000)); then
    die "--max-clients 必须是 1 到 1000 的整数。"
  fi
  if [[ ! "$TUN_MTU" =~ ^[0-9]+$ ]] || ((TUN_MTU < 576 || TUN_MTU > 65535)); then
    die "--tun-mtu 必须是 576 到 65535 的整数。"
  fi
  if [[ ! "$MSSFIX" =~ ^[0-9]+$ ]] || ((MSSFIX < 0 || MSSFIX > 65535)); then
    die "--mssfix 必须是 0 到 65535 的整数。"
  fi
  if [[ ! "$KEEPALIVE_PING" =~ ^[0-9]+$ ]] || ((KEEPALIVE_PING < 1 || KEEPALIVE_PING > 3600)); then
    die "--keepalive-ping 必须是 1 到 3600 的整数。"
  fi
  if [[ ! "$KEEPALIVE_TIMEOUT" =~ ^[0-9]+$ ]] || ((KEEPALIVE_TIMEOUT < 2 || KEEPALIVE_TIMEOUT > 86400)); then
    die "--keepalive-timeout 必须是 2 到 86400 的整数。"
  fi
  ((KEEPALIVE_TIMEOUT > KEEPALIVE_PING)) || die "--keepalive-timeout 必须大于 --keepalive-ping。"
  case "${DATA_CIPHER^^}" in
    AES-256-GCM|AES-128-GCM|CHACHA20-POLY1305) DATA_CIPHER="${DATA_CIPHER^^}" ;;
    *) die "--data-cipher 只能是 AES-256-GCM、AES-128-GCM 或 CHACHA20-POLY1305。" ;;
  esac
  case "${AUTH_DIGEST^^}" in
    SHA256|SHA384|SHA512) AUTH_DIGEST="${AUTH_DIGEST^^}" ;;
    *) die "--auth-digest 只能是 SHA256、SHA384 或 SHA512。" ;;
  esac
  if [[ ! "$LOG_VERB" =~ ^[0-9]+$ ]] || ((LOG_VERB < 0 || LOG_VERB > 11)); then
    die "--log-verb 必须是 0 到 11 的整数。"
  fi
  [[ "$ADMIN_USER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$ ]] || die "管理员用户名无效。"
  [[ "$INITIAL_CLIENT" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$ ]] || die "首个客户端名称无效。"
  [[ "$WEB_ALLOW" =~ ^[0-9./]+$ ]] || die "--web-allow 必须是 IPv4 CIDR。"
  case "$EXISTING_ACTION" in
    auto|preserve|remove|abort) ;;
    *) die "--existing-action 只能是 preserve、remove 或 abort。" ;;
  esac
  if [[ -n "$ENDPOINT" ]]; then
    [[ "$ENDPOINT" =~ ^[A-Za-z0-9.-]+$ ]] || die "公网地址必须是域名或 IPv4 地址。"
  fi
  if [[ -n "$TLS_CERT" || -n "$TLS_KEY" ]]; then
    [[ -r "$TLS_CERT" && -r "$TLS_KEY" ]] || die "--tls-cert 和 --tls-key 必须同时存在且可读。"
  fi
}

validate_options

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
umask 077
BACKUP_ROOT="/var/backups/$APP_NAME"
BACKUP_DIR="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"

stop_existing_openvpn_units() {
  local units=()
  mapfile -t units < <(
    systemctl list-units --all --type=service --no-legend 'openvpn*' 2>/dev/null \
      | awk '$1 == "openvpn.service" || $1 ~ /^openvpn@.*\.service$/ || $1 ~ /^openvpn-server@.*\.service$/ {print $1}' \
      | sed '/^$/d'
  )
  if ((${#units[@]})); then
    log "正在停止已有 OpenVPN 相关服务"
    systemctl stop "${units[@]}" 2>/dev/null || true
  fi
}

backup_existing_openvpn() {
  local archive_paths=()
  local relative
  for relative in \
    etc/openvpn \
    etc/openvpn-manager \
    etc/openvpn-web-manager \
    var/lib/openvpn \
    var/lib/openvpn-manager \
    var/lib/openvpn-web-manager \
    var/log/openvpn; do
    [[ -e "/$relative" ]] && archive_paths+=("$relative")
  done
  while IFS= read -r relative; do
    relative="${relative#/}"
    [[ -n "$relative" ]] && archive_paths+=("$relative")
  done < <(
    find /etc/systemd/system -maxdepth 2 \
      \( -name 'openvpn*.service' -o -name 'openvpn*.service.d' \) \
      -print 2>/dev/null
  )

  install -d -m 0700 "$BACKUP_DIR"
  if ((${#archive_paths[@]})); then
    tar -C / -czf "$BACKUP_DIR/openvpn-existing.tar.gz" "${archive_paths[@]}"
  fi
  dpkg-query -W -f='${binary:Package}\t${Version}\n' 'openvpn*' 'easy-rsa' \
    >"$BACKUP_DIR/packages.txt" 2>/dev/null || true
  cat >"$BACKUP_DIR/README.txt" <<EOF
OpenVPN 安装前备份
创建时间：$(date -u +%Y-%m-%dT%H:%M:%SZ)
处理方式：保留原配置后安装 OpenVPN 管理中心
归档文件：openvpn-existing.tar.gz
EOF
  EXISTING_BACKUP="$BACKUP_DIR"
  log "已有 OpenVPN 配置已备份到 $BACKUP_DIR"
}

migrate_legacy_tree() {
  local source="$1"
  local destination="$2"
  local description="$3"

  [[ -e "$source" ]] || return 0
  [[ ! -L "$source" && -d "$source" ]] || \
    die "旧版${description}路径不是安全的目录，已停止迁移：$source"

  if [[ ! -e "$destination" ]]; then
    mv -- "$source" "$destination"
  else
    [[ ! -L "$destination" && -d "$destination" ]] || \
      die "新版${description}路径不是安全的目录，已停止迁移：$destination"
    cp -a --no-clobber "$source/." "$destination/"
    rm -rf -- "$source"
  fi

  log "已将旧版${description}迁移到 $destination"
}

migrate_legacy_manager_paths() {
  local migration_needed="0"
  if [[ -e "$LEGACY_CONFIG_DIR" || -e "$LEGACY_STATE_DIR" ]]; then
    migration_needed="1"
    log "检测到 v1.1.1 旧版目录，正在迁移受管配置和客户端状态"
  fi

  migrate_legacy_tree "$LEGACY_CONFIG_DIR" "$CONFIG_DIR" "配置目录"
  migrate_legacy_tree "$LEGACY_STATE_DIR" "$STATE_DIR" "状态目录"

  if [[ "$migration_needed" == "1" && -s "$CREDENTIAL_FILE" ]]; then
    sed -i "s|$LEGACY_STATE_DIR|$STATE_DIR|g" "$CREDENTIAL_FILE"
    chmod 0600 "$CREDENTIAL_FILE"
    log "已保留现有控制台凭据并更新其中的客户端路径"
  fi
}

remove_existing_openvpn_state() {
  local custom_units=()
  mapfile -t custom_units < <(
    find /etc/systemd/system -maxdepth 2 \
      \( -name 'openvpn*.service' -o -name 'openvpn*.service.d' \) \
      -print 2>/dev/null
  )
  if ((${#custom_units[@]})); then
    rm -rf -- "${custom_units[@]}"
  fi
  rm -rf /etc/openvpn /var/lib/openvpn /var/log/openvpn
  if [[ "$MANAGED_EXISTING" == "1" || "$EXISTING_ACTION" == "remove" ]]; then
    cleanup_legacy_cli_commands
    rm -rf "$CONFIG_DIR" "$STATE_DIR" "$LEGACY_CONFIG_DIR" "$LEGACY_STATE_DIR"
    if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
      rm -rf "$INSTALL_DIR"
    fi
    rm -f \
      /etc/systemd/system/openvpn-web-manager.service \
      /etc/systemd/system/openvpn-manager-agent.service \
      /etc/systemd/system/openvpn-manager-update.service \
      /etc/systemd/system/openvpn-manager-firewall.service \
      /usr/local/sbin/openvpn-manager-firewall \
      /usr/local/sbin/openvpn-manager-update \
      "$CLI_COMMAND" \
      /etc/nginx/sites-enabled/openvpn-web-manager \
      /etc/nginx/sites-available/openvpn-web-manager \
      /root/openvpn-manager-credentials.txt
  fi
  systemctl daemon-reload
}

cleanup_legacy_cli_commands() {
  local resolved=""
  if [[ -L "$LEGACY_CLI_ALIAS" ]]; then
    resolved="$(readlink -f "$LEGACY_CLI_ALIAS" 2>/dev/null || true)"
    if [[ "$resolved" == "$LEGACY_CLI_COMMAND" || "$resolved" == "$CLI_COMMAND" ]]; then
      rm -f "$LEGACY_CLI_ALIAS"
    fi
  fi
  rm -f "$LEGACY_CLI_COMMAND"
}

if dpkg-query -W -f='${Status}' openvpn 2>/dev/null | grep -q 'install ok installed'; then
  OPENVPN_PACKAGE_PRESENT="1"
fi
if [[ -n "$(find /etc/openvpn -type f \
  \( -name '*.conf' -o -name '*.ovpn' -o -name '*.key' -o -name 'ca.crt' -o -name 'index.txt' \) \
  -print -quit 2>/dev/null)" ]]; then
  OPENVPN_CONFIG_PRESENT="1"
fi
if [[ -s "$CONFIG_DIR/server.json" \
  || -s "$CONFIG_DIR/web.json" \
  || -s "$CONFIG_DIR/install-state.json" \
  || -s "$CONFIG_DIR/tls/server.crt" \
  || -s "$CONFIG_DIR/tls/server.key" \
  || -s "$LEGACY_CONFIG_DIR/server.json" \
  || -s "$LEGACY_CONFIG_DIR/web.json" \
  || -s "$LEGACY_CONFIG_DIR/install-state.json" \
  || -s "$LEGACY_CONFIG_DIR/tls/server.crt" \
  || -s "$LEGACY_CONFIG_DIR/tls/server.key" \
  || -d "$STATE_DIR/clients" \
  || -d "$LEGACY_STATE_DIR/clients" \
  || -f /etc/systemd/system/openvpn-web-manager.service ]]; then
  MANAGED_EXISTING="1"
fi

if [[ "$OPENVPN_CONFIG_PRESENT" == "1" || "$MANAGED_EXISTING" == "1" ]]; then
  if [[ "$EXISTING_ACTION" == "auto" ]]; then
    if [[ "$MANAGED_EXISTING" == "1" ]]; then
      EXISTING_ACTION="preserve"
      log "检测到本项目已有安装，将保留 CA、客户端和控制台配置并执行升级/修复"
    elif [[ -t 0 && -t 1 ]]; then
      printf '\n检测到已有且并非本项目创建的 OpenVPN 配置。\n'
      printf '  1) 备份原配置后替换安装（推荐）\n'
      printf '  2) 删除原配置后全新安装（不可恢复）\n'
      printf '  3) 退出，不做修改\n'
      read -r -p '请选择 [1-3]：' existing_choice
      case "$existing_choice" in
        1) EXISTING_ACTION="preserve" ;;
        2) EXISTING_ACTION="remove" ;;
        *) EXISTING_ACTION="abort" ;;
      esac
    else
      EXISTING_ACTION="preserve"
      warn "非交互环境检测到已有 OpenVPN 配置，将自动备份后替换安装。"
    fi
  fi

  case "$EXISTING_ACTION" in
    preserve)
      stop_existing_openvpn_units
      backup_existing_openvpn
      if [[ "$MANAGED_EXISTING" != "1" ]]; then
        remove_existing_openvpn_state
      fi
      ;;
    remove)
      warn "将删除已有 OpenVPN 配置并执行全新安装。"
      stop_existing_openvpn_units
      apt-get purge -y openvpn easy-rsa 2>/dev/null || true
      remove_existing_openvpn_state
      ;;
    abort)
      die "已取消安装，现有 OpenVPN 未被修改。"
      ;;
  esac
elif [[ "$OPENVPN_PACKAGE_PRESENT" == "1" ]]; then
  case "$EXISTING_ACTION" in
    abort) die "检测到已安装 OpenVPN 软件包，已按要求取消。" ;;
    remove)
      log "检测到 OpenVPN 软件包但没有有效配置，将重新安装软件包"
      apt-get purge -y openvpn easy-rsa 2>/dev/null || true
      ;;
    *) log "检测到已安装但尚未配置的 OpenVPN，将复用软件包并继续初始化" ;;
  esac
fi

load_preserved_server_settings() {
  local source=""
  local values=()
  [[ "$MANAGED_EXISTING" == "1" && "$EXISTING_ACTION" == "preserve" ]] || return 0

  # v1.2.0 的在线更新器会错误地显式传入 --vpn-port random。更新进程由
  # systemd 启动且已经写入更新请求文件时，忽略这个旧参数，确保升级到
  # v1.2.1 的过程中也不会意外改变现有客户端使用的端口。
  if [[ "$VPN_PORT_EXPLICIT" == "1" && "${VPN_PORT,,}" == "random" \
    && -n "${INVOCATION_ID:-}" && -s "$STATE_DIR/update-request.json" ]]; then
    VPN_PORT_EXPLICIT="0"
    warn "检测到旧版在线更新调用，将保留当前 VPN 端口而不是重新随机。"
  fi

  if [[ -s "$CONFIG_DIR/server.json" ]]; then
    source="$CONFIG_DIR/server.json"
  elif [[ -s "$LEGACY_CONFIG_DIR/server.json" ]]; then
    source="$LEGACY_CONFIG_DIR/server.json"
  else
    return 0
  fi

  mapfile -t values < <(python3 - "$source" <<'PY'
import json, pathlib, sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(data.get("endpoint", ""))
print(data.get("vpn_port", 1194))
print(data.get("vpn_protocol", "udp"))
print(data.get("vpn_subnet", "10.8.0.0/24"))
print(",".join(data.get("dns_servers", ["1.1.1.1", "9.9.9.9"])))
print("yes" if data.get("redirect_gateway", True) else "no")
print(data.get("max_clients", 100))
print(data.get("web_port", 8443))
print(data.get("web_allow", "0.0.0.0/0"))
print(data.get("tun_mtu", 1500))
print(data.get("mssfix", 1450) if data.get("mssfix", 1450) not in (None, "") else 0)
print(data.get("keepalive_ping", 10))
print(data.get("keepalive_timeout", 120))
print(data.get("data_cipher", "AES-256-GCM"))
print(data.get("auth_digest", "SHA256"))
print(data.get("log_verb", 3))
print(" ".join(data.get("push_routes", [])))
PY
  ) || die "无法读取已有服务端设置：$source"
  ((${#values[@]} == 17)) || die "已有服务端设置内容不完整：$source"

  OLD_VPN_SUBNET="${values[3]}"
  OLD_VPN_PORT="${values[1]}"
  [[ "$ENDPOINT_EXPLICIT" == "1" ]] || ENDPOINT="${values[0]}"
  [[ "$VPN_PORT_EXPLICIT" == "1" ]] || VPN_PORT="${values[1]}"
  [[ "$VPN_PROTOCOL_EXPLICIT" == "1" ]] || VPN_PROTOCOL="${values[2]}"
  [[ "$VPN_SUBNET_EXPLICIT" == "1" ]] || VPN_SUBNET="${values[3]}"
  [[ "$DNS_SERVERS_EXPLICIT" == "1" ]] || DNS_SERVERS="${values[4]}"
  [[ "$REDIRECT_GATEWAY_EXPLICIT" == "1" ]] || REDIRECT_GATEWAY="${values[5]}"
  [[ "$MAX_CLIENTS_EXPLICIT" == "1" ]] || MAX_CLIENTS="${values[6]}"
  [[ "$WEB_PORT_EXPLICIT" == "1" ]] || WEB_PORT="${values[7]}"
  [[ "$WEB_ALLOW_EXPLICIT" == "1" ]] || WEB_ALLOW="${values[8]}"
  [[ "$TUN_MTU_EXPLICIT" == "1" ]] || TUN_MTU="${values[9]}"
  [[ "$MSSFIX_EXPLICIT" == "1" ]] || MSSFIX="${values[10]}"
  [[ "$KEEPALIVE_PING_EXPLICIT" == "1" ]] || KEEPALIVE_PING="${values[11]}"
  [[ "$KEEPALIVE_TIMEOUT_EXPLICIT" == "1" ]] || KEEPALIVE_TIMEOUT="${values[12]}"
  [[ "$DATA_CIPHER_EXPLICIT" == "1" ]] || DATA_CIPHER="${values[13]}"
  [[ "$AUTH_DIGEST_EXPLICIT" == "1" ]] || AUTH_DIGEST="${values[14]}"
  [[ "$LOG_VERB_EXPLICIT" == "1" ]] || LOG_VERB="${values[15]}"
  [[ "$PUSH_ROUTES_EXPLICIT" == "1" ]] || PUSH_ROUTES="${values[16]}"
  log "已载入并保留现有服务端参数，包括当前 VPN 端口；命令行显式参数仍具有最高优先级"
}

generate_random_vpn_port() {
  VPN_PORT="$(python3 - "$WEB_PORT" "$OLD_VPN_PORT" <<'PY'
import secrets, socket, sys

web_port = int(sys.argv[1])
previous_port = int(sys.argv[2]) if sys.argv[2].isdigit() else None
for _ in range(256):
    port = 10000 + secrets.randbelow(20000)
    if port == web_port or port == previous_port:
        continue
    opened = []
    try:
        for socket_type in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            item = socket.socket(socket.AF_INET, socket_type)
            opened.append(item)
            item.bind(("0.0.0.0", port))
    except OSError:
        continue
    finally:
        for item in opened:
            item.close()
    print(port)
    raise SystemExit(0)
raise SystemExit("无法找到可用的随机 OpenVPN 端口")
PY
  )" || die "无法生成随机 OpenVPN 端口。"
  if [[ -n "$OLD_VPN_PORT" ]]; then
    log "本次安装已将 OpenVPN 端口从 $OLD_VPN_PORT 重新随机为：$VPN_PORT/$VPN_PROTOCOL"
  else
    log "本次安装已随机选择 OpenVPN 端口：$VPN_PORT/$VPN_PROTOCOL"
  fi
}

log "正在安装系统软件包"
apt-get update -y
apt-get install -y --no-install-recommends \
  openvpn easy-rsa python3 nginx openssl iptables iproute2 ca-certificates curl tar util-linux

load_preserved_server_settings
[[ "$VPN_PORT" != "random" ]] || generate_random_vpn_port
validate_options

python3 - "$WEB_ALLOW" <<'PY' || die "--web-allow 必须是有效的 IPv4 CIDR。"
import ipaddress, sys
network = ipaddress.ip_network(sys.argv[1], strict=False)
if network.version != 4:
    raise ValueError("必须使用 IPv4")
PY

VALIDATED_NETWORK_SETTINGS="$(python3 - "$VPN_SUBNET" "$DNS_SERVERS" <<'PY'
import ipaddress, sys

private = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
network = ipaddress.ip_network(sys.argv[1], strict=False)
if network.version != 4 or network.prefixlen < 8 or network.prefixlen > 29:
    raise SystemExit("VPN 子网必须是 /8 到 /29 的 IPv4 私有网段")
if not any(network.subnet_of(item) for item in private):
    raise SystemExit("VPN 子网必须使用 10/8、172.16/12 或 192.168/16 私有地址")
dns_servers = []
for value in sys.argv[2].split(","):
    value = value.strip()
    if not value:
        continue
    address = ipaddress.ip_address(value)
    if address.version != 4 or address.is_unspecified or address.is_multicast:
        raise SystemExit(f"DNS 地址无效：{value}")
    normalized = str(address)
    if normalized not in dns_servers:
        dns_servers.append(normalized)
if len(dns_servers) > 3:
    raise SystemExit("最多可以配置 3 个 DNS 服务器")
print(f"{network.with_prefixlen}\t{','.join(dns_servers)}")
PY
)" || die "--vpn-subnet 或 --dns-servers 无效。"
IFS=$'\t' read -r VPN_SUBNET DNS_SERVERS <<<"$VALIDATED_NETWORK_SETTINGS"

VALIDATED_PUSH_ROUTES="$(python3 - "$VPN_SUBNET" "$PUSH_ROUTES" <<'PY'
import ipaddress, re, sys

private = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
vpn = ipaddress.ip_network(sys.argv[1], strict=False)
routes = []
for raw in re.split(r"[\s,]+", sys.argv[2].strip()):
    if not raw:
        continue
    network = ipaddress.ip_network(raw, strict=False)
    if network.version != 4 or network.prefixlen < 8 or network.prefixlen > 32:
        raise SystemExit(f"自定义推送路由必须是 /8 到 /32 的 IPv4 私有网段：{raw}")
    if not any(network.subnet_of(item) for item in private):
        raise SystemExit(f"自定义推送路由必须使用 RFC1918 私有地址：{raw}")
    if network.overlaps(vpn):
        raise SystemExit(f"自定义推送路由不能与 VPN 子网 {vpn.with_prefixlen} 重叠：{raw}")
    if any(network.overlaps(existing) for existing in routes):
        raise SystemExit(f"自定义推送路由之间不能重叠：{raw}")
    routes.append(network)
print(" ".join(item.with_prefixlen for item in routes))
PY
)" || die "--push-routes 无效。"
PUSH_ROUTES="$VALIDATED_PUSH_ROUTES"

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

log "正在安装应用文件"
getent group "$WEB_GROUP" >/dev/null || groupadd --system "$WEB_GROUP"
id -u "$WEB_USER" >/dev/null 2>&1 || \
  useradd --system --gid "$WEB_GROUP" --home-dir /nonexistent --shell /usr/sbin/nologin "$WEB_USER"

migrate_legacy_manager_paths

install -d -m 0755 "$INSTALL_DIR" "$INSTALL_DIR/backend" "$INSTALL_DIR/web"
cp -a "$SCRIPT_DIR/backend/." "$INSTALL_DIR/backend/"
cp -a "$SCRIPT_DIR/web/." "$INSTALL_DIR/web/"
find "$INSTALL_DIR" -type d -exec chmod 0755 {} +
find "$INSTALL_DIR" -type f -exec chmod 0644 {} +

install -d -m 0750 -o root -g "$WEB_GROUP" "$CONFIG_DIR" "$CONFIG_DIR/tls"
install -d -m 0750 -o root -g "$WEB_GROUP" "$STATE_DIR" "$STATE_DIR/clients"
install -d -m 0751 "$OPENVPN_DIR"
install -d -m 0750 "$EASYRSA_DIR"
install -d -m 0750 -o root -g nogroup "$OPENVPN_DIR/ccd"
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

python3 - "$CONFIG_DIR/server.json" "$ENDPOINT" "$VPN_PORT" "$VPN_PROTOCOL" "$WEB_PORT" "$WEB_ALLOW" "$PUBLIC_INTERFACE" "$VPN_SUBNET" "$DNS_SERVERS" "$REDIRECT_GATEWAY" "$MAX_CLIENTS" "$TUN_MTU" "$MSSFIX" "$KEEPALIVE_PING" "$KEEPALIVE_TIMEOUT" "$DATA_CIPHER" "$AUTH_DIGEST" "$LOG_VERB" "$PUSH_ROUTES" "$WEB_GROUP" <<'PY'
import json, pathlib, sys
(
    path,
    endpoint,
    vpn_port,
    vpn_protocol,
    web_port,
    web_allow,
    interface,
    subnet,
    dns_servers,
    redirect_gateway,
    max_clients,
    tun_mtu,
    mssfix,
    keepalive_ping,
    keepalive_timeout,
    data_cipher,
    auth_digest,
    log_verb,
    push_routes,
    web_group,
) = sys.argv[1:]
data = {
    "endpoint": endpoint,
    "vpn_port": int(vpn_port),
    "vpn_protocol": vpn_protocol,
    "web_port": int(web_port),
    "web_allow": web_allow,
    "public_interface": interface,
    "vpn_subnet": subnet,
    "dns_servers": [item for item in dns_servers.split(",") if item],
    "redirect_gateway": redirect_gateway == "yes",
    "max_clients": int(max_clients),
    "tun_mtu": int(tun_mtu),
    "mssfix": int(mssfix),
    "keepalive_ping": int(keepalive_ping),
    "keepalive_timeout": int(keepalive_timeout),
    "data_cipher": data_cipher,
    "auth_digest": auth_digest,
    "log_verb": int(log_verb),
    "push_routes": [item for item in push_routes.replace(",", " ").split() if item],
    "server_name": "server",
    "easy_rsa_dir": "/etc/openvpn/server/easy-rsa",
    "openvpn_dir": "/etc/openvpn/server",
    "state_dir": "/var/lib/openvpn-manager",
    "status_file": "/var/log/openvpn/status.log",
    "service_name": "openvpn-server@server.service",
    "tunnel_interface": "tun0",
    "management_socket": "/run/openvpn-manager/openvpn.sock",
    "ipp_path": "/var/lib/openvpn/server/ipp.txt",
    "client_networks_path": "/etc/openvpn-manager/client-networks.json",
    "firewall_env_path": "/etc/openvpn-manager/firewall.env",
    "firewall_routes_path": "/etc/openvpn-manager/client-routes.conf",
    "install_state_path": "/etc/openvpn-manager/install-state.json",
    "update_request_path": "/var/lib/openvpn-manager/update-request.json",
    "update_status_path": "/var/lib/openvpn-manager/update-status.json",
    "update_check_path": "/var/lib/openvpn-manager/update-check.json",
    "update_service_name": "openvpn-manager-update.service",
    "web_group": web_group,
}
pathlib.Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
chown root:"$WEB_GROUP" "$CONFIG_DIR/server.json"
chmod 0640 "$CONFIG_DIR/server.json"

log "正在配置控制台认证"
WEB_CONFIG_EXISTS="0"
if [[ -s "$CONFIG_DIR/web.json" && "$PASSWORD_EXPLICIT" == "0" ]]; then
  if [[ -s "$CONFIG_DIR/install-state.json" || -s "$CREDENTIAL_FILE" || -f /etc/systemd/system/openvpn-web-manager.service ]]; then
    WEB_CONFIG_EXISTS="1"
    warn "已保留现有控制台密码。"
  else
    warn "检测到上次安装在保存明文凭据前中断，将自动生成新的控制台密码。"
  fi
fi
if [[ "$WEB_CONFIG_EXISTS" == "0" ]]; then
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
chown root:"$WEB_GROUP" "$CONFIG_DIR/web.json"
chmod 0640 "$CONFIG_DIR/web.json"

if systemctl is-active --quiet openvpn-manager-firewall.service 2>/dev/null; then
  systemctl stop openvpn-manager-firewall.service || true
fi
log "正在生成 OpenVPN、客户端路由和防火墙配置"
python3 "$INSTALL_DIR/backend/agent.py" --direct sync_runtime >/dev/null || \
  die "无法生成 OpenVPN 运行配置。"
if [[ -n "$OLD_VPN_SUBNET" && "$OLD_VPN_SUBNET" != "$VPN_SUBNET" ]]; then
  rm -f /var/lib/openvpn/server/ipp.txt
  log "VPN 子网已变更，已清理旧客户端地址池记录"
fi

cat >/etc/sysctl.d/99-openvpn-manager.conf <<'EOF'
net.ipv4.ip_forward = 1
EOF
if ! sysctl -q -p /etc/sysctl.d/99-openvpn-manager.conf; then
  die "无法启用 IPv4 转发。请确认当前 VPS/容器允许修改 net.ipv4.ip_forward。"
fi

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
cleanup_legacy_cli_commands
install -m 0755 "$SCRIPT_DIR/scripts/openvpn-manager-firewall" /usr/local/sbin/openvpn-manager-firewall
install -m 0755 "$SCRIPT_DIR/scripts/openvpn-manager-update" /usr/local/sbin/openvpn-manager-update
install -m 0755 "$SCRIPT_DIR/scripts/openvpn-manager" "$CLI_COMMAND"
install -m 0644 "$SCRIPT_DIR/config/openvpn-manager-firewall.service" /etc/systemd/system/openvpn-manager-firewall.service
install -m 0644 "$SCRIPT_DIR/config/openvpn-manager-agent.service" /etc/systemd/system/openvpn-manager-agent.service
install -m 0644 "$SCRIPT_DIR/config/openvpn-manager-update.service" /etc/systemd/system/openvpn-manager-update.service
install -m 0644 "$SCRIPT_DIR/config/openvpn-web-manager.service" /etc/systemd/system/openvpn-web-manager.service
install -m 0644 "$SCRIPT_DIR/config/openvpn-manager.tmpfiles" /etc/tmpfiles.d/openvpn-manager.conf
install -d -m 0755 /etc/systemd/system/openvpn-server@server.service.d
install -m 0644 "$SCRIPT_DIR/config/openvpn-service-override.conf" \
  /etc/systemd/system/openvpn-server@server.service.d/openvpn-manager.conf

systemd-tmpfiles --create /etc/tmpfiles.d/openvpn-manager.conf
systemctl daemon-reload
systemctl enable openvpn-manager-firewall.service
systemctl restart openvpn-manager-firewall.service
systemctl enable openvpn-server@server.service
systemctl restart openvpn-server@server.service

log "正在创建首个客户端配置"
python3 "$INSTALL_DIR/backend/agent.py" --direct create_client --name "$INITIAL_CLIENT" || \
  warn "首个客户端配置已存在或无法重新创建，请检查上方代理输出。"

systemctl enable openvpn-manager-agent.service openvpn-web-manager.service nginx.service
systemctl restart openvpn-manager-agent.service openvpn-web-manager.service nginx.service

sleep 1
systemctl is-active --quiet openvpn-server@server.service || die "OpenVPN 启动失败，请运行 journalctl -u openvpn-server@server 查看日志。"
systemctl is-active --quiet openvpn-manager-agent.service || die "管理代理启动失败。"
systemctl is-active --quiet openvpn-web-manager.service || die "管理 Web 服务启动失败。"

python3 - "$CONFIG_DIR/install-state.json" "$VERSION" "$EXISTING_ACTION" "$EXISTING_BACKUP" <<'PY'
import datetime, json, pathlib, sys
path, version, existing_action, backup = sys.argv[1:]
data = {
    "managed_by": "openvpn-web-manager",
    "version": version,
    "installed_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "existing_action": existing_action,
    "existing_backup": backup or None,
}
pathlib.Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
chown root:"$WEB_GROUP" "$CONFIG_DIR/install-state.json"
chmod 0640 "$CONFIG_DIR/install-state.json"

printf '\n\033[1;32m安装完成。\033[0m\n'
printf '  管理地址：       https://%s:%s\n' "$ENDPOINT" "$WEB_PORT"
printf '  OpenVPN：       %s:%s/%s\n' "$ENDPOINT" "$VPN_PORT" "$VPN_PROTOCOL"
printf '  命令行管理：     sudo openvpn-manager\n'
printf '  首个客户端：     %s/clients/%s.ovpn\n' "$STATE_DIR" "$INITIAL_CLIENT"
if [[ "$WEB_CONFIG_EXISTS" == "0" ]]; then
  printf '  用户名：         %s\n' "$ADMIN_USER"
  printf '  密码：           %s\n' "$ADMIN_PASSWORD"
  printf '  凭据文件：       %s\n' "$CREDENTIAL_FILE"
else
  printf '  凭据文件：       未变更（已保留现有 web.json）\n'
fi
if [[ -n "$EXISTING_BACKUP" ]]; then
  printf '  原配置备份：     %s\n' "$EXISTING_BACKUP"
fi
printf '\n'
if [[ "$WEB_ALLOW" == "0.0.0.0/0" ]]; then
  warn "当前允许任意 IPv4 来源访问控制台，请使用 --web-allow 或上游防火墙限制来源。"
fi
if [[ -z "$TLS_CERT" ]]; then
  warn "已安装自签名 TLS 证书，生产环境请替换为受信任证书。"
fi

\n