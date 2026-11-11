# 部署与迁移

需要 Python 3.10+。创建虚拟环境、安装 requirements.txt 后运行 python app.py。默认只监听 127.0.0.1:8765，首次启动创建空白站点和数据库。可选运行 python scripts/seed_demo.py 创建虚构演示数据；脚本拒绝覆盖已有数据。

## Linux + systemd + Caddy

安装到 /opt/gpt-relay-price-monitor，创建专用系统用户 linx，把项目归该用户所有，然后执行：

~~~bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/configure.py
sudo cp deploy/ai-price-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-price-monitor
~~~

配置脚本交互填写域名、管理员账号和密码，生成本地 .env，密码仅存 PBKDF2 哈希。已有 .env 时不覆盖。请用 linx 用户创建虚拟环境和配置；路径或用户名不同则同步修改 service 模板。

将 deploy/Caddyfile 的 monitor.example.com 换成自己的域名后加载。APP_PUBLIC_URL 必须与真实访问源一致，例如 https://monitor.example.com。主程序留在回环地址，由反向代理提供 HTTPS。

## Docker Compose

~~~bash
python scripts/configure.py
docker compose up -d --build
~~~

Compose 将端口绑定至宿主机回环地址。命名卷 monitor-data 保存站点、SQLite、报告、加密连接和图标缓存。Docker 配置只运行主程序；检测工具按 [DETECTORS.md](DETECTORS.md) 单独安装。不要使用 docker compose down -v 删除需要保留的数据卷。

## 环境配置

| 配置 | 默认值 / 用途 |
| --- | --- |
| APP_HOST | 127.0.0.1，监听地址 |
| APP_PORT | 8765 |
| APP_PUBLIC_URL | http://127.0.0.1:8765，对外访问源 |
| APP_AUTH_REQUIRED | 本机默认 0；共享部署设为 1 |
| APP_USERNAME | 自己的管理员账号 |
| APP_PASSWORD_HASH | 配置脚本生成的密码哈希 |
| APP_SESSION_SECRET | 配置脚本生成的会话签名密钥 |
| APP_DATA_DIR | 可选，独立持久化目录 |

自动读取 .env，外部环境变量优先。共享部署请使用认证；默认本机模式没有账号隔离。所有获授权的登录客户端访问同一套数据。

## 数据和迁移

默认站点在 sites.json；数据库、用量连接在 data/；报告在 reports/。设置 APP_DATA_DIR 后，上述数据统一放进该目录，报告位于其 reports/ 子目录。

从旧版迁移：停止旧实例，备份 sites.json、quality_sites.json（如有）、整个 data/ 和 .env；安装新依赖后恢复原文件。使用自定义存储时，将 sites.json 与原 data/ 内容一起放入 APP_DATA_DIR。首次启动后检查编辑器、报告和验纯记录；过期的站点登录令牌需要重新填写。

加密连接与加密密钥必须一起备份。两个检测工具的数据在各自独立目录，不在主数据库中。备份包含个人信息，不要提交 Git。

Windows 安装依赖后可用原有 .cmd / .vbs 启动；Linux/macOS 可用 start_editor.sh、generate_report.sh。

## 用量兼容范围

当前适配 Sub2API 风格的 /api/v1/auth/me、/api/v1/usage 等接口。Cloudflare 验证、不同面板 API、上游超时和令牌过期均可能影响连接，并非支持所有中转站。

用量监测只读取账户和使用记录，不发起模型生成请求。缓存率按缓存读取 Token / 全部输入 Token 加权计算。记录高亮代表性能或费用提醒，不等于请求失败。
