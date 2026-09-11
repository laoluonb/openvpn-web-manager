#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="openvpn-web-manager"
PURGE="0"
ASSUME_YES="0"

usage() {
  cat <<'EOF'
用法：sudo ./uninstall.sh [--purge] [--yes]

不指定 --purge 时只移除应用服务，并保留 PKI、客户端配置和系统配置以便恢复。
指定 --purge 会永久删除这些数据。
EOF
}

while (($#)); do
  case "$1" in
    --purge) PURGE="1"; shift ;;
    --yes|-y) ASSUME_YES="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知选项：$1" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "请以 root 身份运行。" >&2; exit 1; }

if [[ "$ASSUME_YES" != "1" ]]; then
  read -r -p "确认移除 OpenVPN 管理中心服务？[y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || exit 0
  if [[ "$PURGE" == "1" ]]; then
    read -r -p "确认永久删除 CA、私钥、客户端配置和系统配置？[y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || exit 0
  fi
fi

BACKUP_DIR="/var/backups/$APP_NAME/uninstall-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP_DIR"
[[ -d /etc/openvpn-manager ]] && cp -a /etc/openvpn-manager "$BACKUP_DIR/manager-config"
[[ -d /etc/openvpn/server/easy-rsa/pki ]] && \
  tar -C /etc/openvpn/server/easy-rsa -czf "$BACKUP_DIR/pki.tar.gz" pki

systemctl disable --now openvpn-web-manager.service openvpn-manager-agent.service 2>/dev/null || true
systemctl disable --now openvpn-server@server.service 2>/dev/null || true
systemctl disable --now openvpn-manager-firewall.service 2>/dev/null || true
/usr/local/sbin/openvpn-manager-firewall stop 2>/dev/null || true

rm -f /etc/nginx/sites-enabled/openvpn-web-manager /etc/nginx/sites-available/openvpn-web-manager
nginx -t >/dev/null 2>&1 && systemctl reload nginx.service || true

rm -f \
  /etc/systemd/system/openvpn-web-manager.service \
  /etc/systemd/system/openvpn-manager-agent.service \
  /etc/systemd/system/openvpn-manager-firewall.service \
  /etc/systemd/system/openvpn-server@server.service.d/openvpn-manager.conf \
  /usr/local/sbin/openvpn-manager-firewall \
  /usr/local/bin/openvpn-managerctl \
  /etc/sysctl.d/99-openvpn-manager.conf
rm -rf /opt/openvpn-web-manager
systemctl daemon-reload
sysctl --system >/dev/null 2>&1 || true

if [[ "$PURGE" == "1" ]]; then
  rm -rf /etc/openvpn-manager /var/lib/openvpn-manager /etc/openvpn/server
  rm -f /root/openvpn-manager-credentials.txt
  id -u openvpn-web >/dev/null 2>&1 && userdel openvpn-web || true
  getent group openvpn-web >/dev/null && groupdel openvpn-web || true
  echo "应用及受管数据已彻底删除。备份位置：$BACKUP_DIR"
else
  echo "服务已移除，PKI 和配置仍被保留。备份位置：$BACKUP_DIR"
  echo "如需永久删除受管数据，请使用 --purge。"
fi
