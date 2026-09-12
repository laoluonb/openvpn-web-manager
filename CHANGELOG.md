# 更新日志

## 1.2.4 - 2026-09-12

- 修复 OpenVPN 2.6 `status-version 3` 使用制表符分隔字段而后端按逗号解析，导致日志显示已连接但
  客户端页面全部显示“离线”的问题。
- 修复 `/etc/openvpn/server` 父目录不可遍历导致 `ccd/ikuai` 和 `ccd/DEFAULT` 报 `Permission denied`；
  仅增加目录遍历权限，不放开目录列表或私钥读取权限。

## 1.2.3 - 2026-09-12

- 修复 OpenVPN 重启后 `/run/openvpn-manager` 被 systemd 重新创建为错误属主，导致 Web 页面无法连接
  `agent.sock`、日志和更新状态显示“控制代理当前不可用”的问题。现在由 `tmpfiles.d` 统一管理运行目录，
  OpenVPN 和管理代理不再各自声明 `RuntimeDirectory`。

## 1.2.2 - 2026-09-12

- 修复系统页面加载日志时出现 `控制代理当前不可用：[Errno 13] Permission denied`：代理启动时会
  自动修复 `/run/openvpn-manager` 的用户组和目录权限，确保 Web 服务可以连接 `agent.sock`。

## 1.2.1 - 2026-09-11

- Web 一键更新和普通 `preserve` 升级现在保留当前 OpenVPN 端口，只升级管理面板和软件包；首次、
  全新安装或显式传入 `--vpn-port random` 时才随机端口。
- 修复应用服务端设置或重启 OpenVPN 时，systemd 依赖关系同时停止特权代理，导致页面显示
  `请求失败（502）` 的问题。
- 修复 CCD 目录和文件仅允许 root 读取，导致 OpenVPN 降权后无法载入客户端 `iroute`，从而只能
  访问客户端的 `10.8.0.x` 隧道地址、无法访问其下级内网的问题。
- 客户端离线时，页面明确显示“内网不可达”，并补充 OpenWrt/爱快本机防火墙和返回路由提示。

## 1.2.0 - 2026-09-11

- 新增 Web“服务端”页面，可修改公网地址、VPN 端口、UDP/TCP、VPN 子网、DNS、全流量转发
  和最大并发客户端数；保存时自动校验、重建配置并重启 OpenVPN。
- 新增客户端配置查看器：点击客户端名称即可查看和复制完整 `.ovpn`，并为爱快 iKuai 单独拆分
  CA 证书、客户端证书、客户端私钥和 `tls-crypt` 静态密钥。
- 新增“踢出连接”，通过 OpenVPN 本地管理套接字断开当前在线会话，但不吊销客户端证书。
- 新增客户端下级内网管理，可选择“仅服务端可访问”或“允许其他 VPN 客户端访问”；自动生成
  `route`、`iroute`、逐客户端 CCD 路由推送和独立 iptables 访问策略。
- 共享 LAN 路由只下发给其他有效客户端，不会推回拥有该 LAN 的分支客户端；新建普通客户端时
  也会立即获得现有共享 LAN 的定向路由。
- 新增“一键更新面板与 OpenVPN”：从 GitHub 最新稳定 Release 获取并安全检查源码包，自动备份
  当前配置、保留服务端参数，并通过系统软件源更新 OpenVPN。
- 安装器新增 VPN 协议、子网、DNS、全流量转发和最大客户端数参数；首次或全新安装会在
  `10000-29999` 中选择可用的随机 OpenVPN 端口。
- 重复安装时会保留所有未在命令行显式覆盖的现有服务端参数。
- VPN 子网变化时自动清理旧的 `ipp.txt` 地址池记录；特权代理补充对应 systemd 写权限。
- CLI 新增 `network`、`disconnect`、`profile`、`update` 和 `update-status` 命令。
- 更新中文文档、前端 DOM 回归测试、客户端路由测试及 GitHub Actions ShellCheck 范围。

## 1.1.2 - 2026-09-11

- 修复安装器将受管配置写入 `/etc/openvpn-web-manager`、将状态写入
  `/var/lib/openvpn-web-manager`，而 Nginx、systemd 和后端统一读取
  `/etc/openvpn-manager` 与 `/var/lib/openvpn-manager`，导致 HTTPS 配置阶段找不到证书的问题。
- 安装器现在会识别 v1.1.1 遗留的配置、Web 认证、TLS 证书和客户端状态；在备份后将其安全
  迁移到规范目录，并保留 `/root/openvpn-manager-credentials.txt` 中的现有控制台密码。
- `preserve` 备份现在同时包含规范目录和 v1.1.1 遗留目录；`remove` 与 `--purge` 会同时清理
  两组目录，避免残留配置影响后续安装。
- 新增部署路径一致性和旧目录迁移回归测试。

## 1.1.1 - 2026-09-11

- 修复安装在控制台认证之后中断时，仅保存密码哈希但尚未保存明文凭据，导致再次执行后用户
  无法获知控制台密码的问题。
- 新生成或显式重置的凭据现在会在后续网络和服务配置之前立即写入
  `/root/openvpn-manager-credentials.txt`。
- 检测到未完成的旧安装且没有完成标记、服务单元或凭据文件时，会自动生成新密码；已完成安装
  仍会保留原密码。

## 1.1.0 - 2026-09-11

- 新增已有 OpenVPN 检测与处理流程：可交互选择备份保留、彻底删除或退出，也可通过
  `--existing-action preserve|remove|abort` 用于无人值守安装。
- 对本项目已有安装自动执行备份升级，保留 CA、客户端证书、控制台账号和配置。
- 备份模式会归档原 OpenVPN 配置、PKI、状态目录及相关 systemd 自定义单元。
- 修复安装器调用 `sysctl --system` 时会加载主机上所有第三方 sysctl 配置、并可能因无关的
  conntrack 参数错误而中止安装的问题。
- 安装器现在只加载自身的 `/etc/sysctl.d/99-openvpn-manager.conf`，并在宿主机确实无法启用
  IPv4 转发时给出明确错误。
- 卸载时不再重新加载全局 sysctl 配置，也不会擅自关闭可能被其他服务使用的运行时转发状态。

## 1.0.0 - 2026-09-11

- 新增 Debian/Ubuntu 一键安装器，自动配置 OpenVPN、Easy-RSA、路由、防火墙、systemd 与 HTTPS。
- 新增仅使用 Python 标准库的轻量控制平面，Web 服务与 root 代理通过 Unix 套接字隔离。
- 新增全中文响应式管理界面，支持客户端创建、下载、吊销、在线状态、服务重启与日志查看。
- 新增卸载回滚、本地演示模式、单元测试和 GitHub Actions CI。
