# GPT 中转站倍率看板

一个本地运行的 GPT、Codex、ChatGPT 中转站价格管理工具。项目包含网页编辑器和报告页面，但仓库不附带任何站点、余额、邀请链接或个人数据。

用户第一次启动时看到的是空表，可以在编辑器中自行添加站点。

## 功能

- 网页端新增、修改和删除站点
- 收费站与公益站分类
- 最低倍率、Plus、Pro 排序
- 全局搜索与有余额筛选
- 余额站点和签到站点提示
- 每日签到状态记录
- 邀请链接复制与快速打开
- SQLite、JSON、HTML 和 CSV 同步
- 验纯网站区域

## 环境要求

- Python 3.11 或更高版本
- 不需要安装第三方 Python 包
- 不需要 Node.js

## Windows 使用

1. 下载或克隆本仓库。
2. 双击 `启动价格监控编辑器.cmd`。
3. 浏览器会自动打开 http://127.0.0.1:8765/
4. 点击“新增一行”添加自己的第一个站点。
5. 点击“保存并同步”生成本地数据和报告。

只重新生成报告时，双击 `生成报告.cmd`。

## macOS / Linux 使用

```bash
git clone <你的仓库地址>
cd <仓库目录>
python3 app.py
```

只生成报告：

```bash
python3 monitor.py
```

## 本地文件

这些文件会在本机运行或保存后生成，并已被 `.gitignore` 排除：

- `sites.json`：用户自己的站点、倍率、余额和邀请链接
- `quality_sites.json`：用户自己的验纯网站
- `data/price_monitor.sqlite3`：SQLite 数据库
- `reports/latest.html`：本地报告页面
- `reports/latest.csv`：本地 CSV 报告

空数据格式示例见 `sites.example.json` 和 `quality_sites.example.json`。

## 数据字段

```json
{
  "name": "示例站点",
  "category": "收费站",
  "url": "https://example.com/keys",
  "invite_url": "https://example.com/register?aff=YOUR_CODE",
  "balance": 0,
  "welfare_rate": 0.1,
  "plus_rate": 0.2,
  "pro_rate": 0.3,
  "signup_bonus": null,
  "daily_checkin_bonus": "不固定",
  "notes": ""
}
```

`category` 可选择 `收费站` 或 `公益站`。签到额度既可以填写数字，也可以填写“不固定”等文字。

## 隐私设计

仓库中的演示报告是空数据报告。个人 `sites.json`、数据库和本地报告默认不会被 Git 提交。提交代码前仍建议运行 `git status`，确认没有私人文件进入暂存区。

## GitHub Pages

仓库根目录的 `index.html` 是空数据演示页面，可以直接通过 GitHub Pages 发布。Python 编辑器不能在 GitHub Pages 上运行，使用编辑器需要把项目下载到本机并启动。

## 免责声明

本项目只用于整理公开价格信息。中转站价格、可用性和服务条款可能变化，请以对应服务商实际页面为准。
