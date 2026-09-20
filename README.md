# AI 模型中转站倍率看板

这个项目用于整理 GPT、Claude、国模等 AI 模型中转站和公益站的价格倍率、余额、注册送、签到送、邀请链接，并生成本地网页报告。

## 日常使用

双击：

- `启动价格监控编辑器.cmd`：自动关闭本项目之前启动的旧进程，并在后台启动编辑器。
- `启动价格监控编辑器.vbs`：功能相同，并且连启动时短暂的窗口闪烁也不会出现。

编辑器会自动在浏览器中打开。也可以手动打开：

- `http://127.0.0.1:8765/`

在编辑器里修改数据后点“保存并同步”，会同时更新：

- `sites.json`
- `data/price_monitor.sqlite3`
- `reports/latest.html`
- `reports/latest.csv`

只想重新生成报告时，双击：

- `生成报告.cmd`

## 备份

双击：

- `备份项目.cmd`

会在 `backups/` 里生成一个 zip，包含代码、站点数据、报告和 SQLite 数据库。

## 文件说明

- `sites.json`：站点清单和人工维护的数据，是最核心的数据文件。
- `quality_sites.json`：验纯 / 模型检测网站清单。
- `monitor.py`：读取数据、排序、生成 HTML / CSV / SQLite。
- `app.py`：本地网页编辑器。
- `reports/latest.html`：报告页面。
- `reports/latest.csv`：表格导出。
- `data/price_monitor.sqlite3`：SQLite 数据库。
- `calculator.html`：单次 API 成本计算器，会读取本地站点的最低 / Plus / Pro 倍率。

## 成本计算器

在编辑器或报告页面点击“成本计算器”，或直接打开：

- `http://127.0.0.1:8765/calculator.html`

可选一个已维护的站点和倍率类型自动带入倍率；充值金额、获得额度、模型输入/输出价格和 Token 量由你按本次估算填写。计算器参数只保存在当前浏览器，不会修改 `sites.json`、余额或报告数据。

## 手动命令

生成报告：

```powershell
python monitor.py
```

启动编辑器：

```powershell
python app.py
```

按 SQLite 数据重新生成报告：

```powershell
python monitor.py --source db
```

## 字段说明

`sites.json` 中每个站点大致长这样：

```json
{
  "name": "小白Code",
  "category": "收费站",
  "url": "https://token.dialoguedui.com/keys",
  "invite_url": "https://token.dialoguedui.com/register?aff=...",
  "balance": 0.14,
  "welfare_rate": 0.07,
  "plus_rate": null,
  "pro_rate": 0.19,
  "signup_bonus": 1,
  "daily_checkin_bonus": 0.25,
  "notes": "福利分组"
}
```

- `category`：只能填 `收费站` 或 `公益站`。
- `balance`：当前账号余额；没有就填 `0` 或 `null`。
- `welfare_rate`：福利 / 特价 / 最低分组倍率。
- `plus_rate`：Plus 倍率。
- `pro_rate`：Pro 倍率。
- `signup_bonus`：注册送。
- `daily_checkin_bonus`：签到送；可以填数字，也可以填 `不固定` 这类文字。
