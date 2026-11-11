# 可选的两个验纯工具

主程序的验纯记录、结论、倍率分组和折线图可独立使用。Ikale / Meow 两个按钮对应独立的第三方检测服务，默认未随主程序启动；安装后才可使用 /detector/、/meow/。

| 工具 | 本项目中的定位 | 原项目 |
| --- | --- | --- |
| Ikale | 数字分布、顺序指纹，快速初筛 | https://github.com/Ikaleio/lm-detector |
| Meow | 短问答统计，深入复核 | https://github.com/chen-006/meow-llm-detector |

它们的结论是基于参考样本与统计方法的证据，不是模型来源的绝对证明；运行检测会产生上游模型调用和费用。

## 安装源码

~~~bash
python integrations/install.py ikale
python integrations/install.py meow
~~~

安装器从上游固定提交下载，保留上游项目及许可，再叠加 integrations/ 下的托管适配。不会复制本服务器的配置、密钥或检测历史，也不会覆盖已有 vendor 目录。Ikale 固定为部署时的 175355e… 版本，Meow 固定为 v4.5.4 对应提交；升级需重新核对适配代码。

## Ikale

需要 Bun（部署版本使用 1.4.4）。主程序和此服务使用同一个 APP_PUBLIC_URL。

~~~bash
cd vendor/ikale
bun install --frozen-lockfile
bun run sync-data
bun run --cwd web build
bun run server.ts
~~~

主程序 .env 会由托管适配器读取；也可显式设置 APP_PUBLIC_URL、APP_INTERNAL_URL（默认 http://127.0.0.1:8765）。服务默认回环地址端口 8789，通过 Caddy 将 /detector/* 原样转发。

服务器配置同步使用 AES-256-GCM 加密，保存在 vendor/ikale/private/。旧浏览器配置可迁移到服务器，保存采用版本检查，避免另一台电脑的修改被静默覆盖。目录及密钥需要一起备份。

## Meow

~~~bash
python -m venv vendor/meow/.venv
vendor/meow/.venv/bin/pip install -r vendor/meow/requirements.txt
python integrations/meow/serve.py
~~~

Windows 使用 vendor/meow/.venv/Scripts/python.exe integrations/meow/serve.py。默认回环地址端口 8790。此服务读取主程序 .env，通过 /api/detector/auth 检查主程序登录，并保留 Meow 自己的会话校验。数据保存在 vendor/meow/runs/；可用 MEOW_DATA_DIR 指定其他位置。首次使用请按上游界面下载/导入参考基线。参考样本未打包进本仓库。

本站额外的展示规则保存在 integrations/meow/linx_policy.py：声明 GPT-6.1-Sol 时，证据充分且原始判定为强指向 GPT-6.1-Sol 或 GPT-6-Astra，均显示为“好结果”；证据不足仍保留待确认。原始分数和判定阈值不修改。其他申报模型沿用精确匹配规则。

## 反向代理与进程管理

在 deploy/Caddyfile 取消两个工具对应路由的注释。Caddy 不剥离 /detector/ 和 /meow/ 前缀。主程序服务必须先启动。长期运行时为每个工具建立独立的 systemd 服务，工作目录指向 vendor/ikale 或项目根目录，使用各自的运行时和可写数据目录。

这是两个独立的可选运行时，不属于主程序 Docker 镜像。当前仓库提供源码适配和安装入口；Bun/Python 依赖、网络出口和基线下载仍需部署环境满足。

## 上游许可

主程序原有 MIT 许可继续保留。Ikale 上游使用 MIT；Meow v4.5.4 随附 PolyForm Noncommercial License 1.0.0，相关 UI 叠加文件也保留其上游来源，不纳入主程序 MIT 授权。具体条款参阅 integrations/meow/LICENSE 和 THIRD_PARTY_NOTICES.md。修改和分发时保留原项目的许可与声明。
