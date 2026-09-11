# 更新日志

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
