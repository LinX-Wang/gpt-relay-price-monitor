from __future__ import annotations

import argparse
import csv
import html
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
SITES_FILE = ROOT / "sites.json"
QUALITY_SITES_FILE = ROOT / "quality_sites.json"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
DB_FILE = DATA_DIR / "price_monitor.sqlite3"
SITE_FIELDS = [
    "name",
    "url",
    "category",
    "balance",
    "welfare_rate",
    "plus_rate",
    "pro_rate",
    "signup_bonus",
    "daily_checkin_bonus",
    "checkin_mode",
    "notes",
    "invite_url",
    "usage_status",
]


@dataclass
class SiteSnapshot:
    name: str
    url: str
    category: str
    invite_url: str | None
    welfare_rate: float | None
    plus_rate: float | None
    pro_rate: float | None
    lowest_rate: float | None
    balance: float | None
    signup_bonus: float | None
    daily_checkin_bonus: Any
    checkin_mode: str
    source: str
    status: str
    notes: str
    usage_status: str
    checked_at: str


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def empty_to_none(value: Any) -> Any:
    if value is None or value == "":
        return None
    return value


def has_value(value: Any) -> bool:
    return value is not None and value != ""


def normalize_optional_http_url(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "undefined", "n/a", "na", "-", "暂无", "无", "没有"}:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text


def load_sites() -> list[dict[str, Any]]:
    with SITES_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_sites(sites: list[dict[str, Any]]) -> None:
    SITES_FILE.write_text(
        json.dumps(sites, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_quality_sites() -> list[dict[str, Any]]:
    if not QUALITY_SITES_FILE.exists():
        return []
    with QUALITY_SITES_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '收费站',
            invite_url TEXT,
            welfare_rate REAL,
            plus_rate REAL,
            pro_rate REAL,
            lowest_rate REAL,
            balance REAL,
            signup_bonus REAL,
            daily_checkin_bonus REAL,
            checkin_mode TEXT NOT NULL DEFAULT '自动',
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            usage_status TEXT NOT NULL DEFAULT '',
            checked_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    if "category" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN category TEXT NOT NULL DEFAULT '收费站'")
    if "invite_url" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN invite_url TEXT")
    if "pro_rate" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN pro_rate REAL")
    if "usage_status" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN usage_status TEXT NOT NULL DEFAULT ''")
    if "checkin_mode" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN checkin_mode TEXT NOT NULL DEFAULT '自动'")
    conn.commit()


def init_sites_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sites (
            name TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '收费站',
            balance REAL,
            welfare_rate REAL,
            plus_rate REAL,
            pro_rate REAL,
            signup_bonus REAL,
            daily_checkin_bonus REAL,
            checkin_mode TEXT NOT NULL DEFAULT '自动',
            notes TEXT,
            invite_url TEXT,
            usage_status TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sites)")}
    migrations = {
        "balance": "ALTER TABLE sites ADD COLUMN balance REAL",
        "category": "ALTER TABLE sites ADD COLUMN category TEXT NOT NULL DEFAULT '收费站'",
        "pro_rate": "ALTER TABLE sites ADD COLUMN pro_rate REAL",
        "sort_order": "ALTER TABLE sites ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0",
        "updated_at": "ALTER TABLE sites ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        "usage_status": "ALTER TABLE sites ADD COLUMN usage_status TEXT NOT NULL DEFAULT ''",
        "checkin_mode": "ALTER TABLE sites ADD COLUMN checkin_mode TEXT NOT NULL DEFAULT '自动'",
    }
    for column, sql in migrations.items():
        if column not in columns:
            conn.execute(sql)
    conn.commit()


def sync_sites_to_db(conn: sqlite3.Connection, sites: list[dict[str, Any]]) -> None:
    init_sites_table(conn)
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for sort_order, site in enumerate(sites):
        rows.append(
            {
                "name": site.get("name", ""),
                "url": site.get("url", ""),
                "category": site.get("category") or "收费站",
                "balance": as_float(site.get("balance")),
                "welfare_rate": as_float(site.get("welfare_rate")),
                "plus_rate": as_float(site.get("plus_rate")),
                "pro_rate": as_float(site.get("pro_rate")),
                "signup_bonus": as_float(site.get("signup_bonus")),
                "daily_checkin_bonus": empty_to_none(site.get("daily_checkin_bonus")),
                "checkin_mode": "手动" if site.get("checkin_mode") == "手动" else "自动",
                "notes": site.get("notes", ""),
                "invite_url": site.get("invite_url"),
                "usage_status": "常用" if site.get("usage_status") == "常用" else "",
                "sort_order": sort_order,
                "updated_at": now,
            }
        )
    conn.execute("DELETE FROM sites")
    conn.executemany(
        """
        INSERT INTO sites (
            name, url, category, balance, welfare_rate, plus_rate, pro_rate, signup_bonus,
            daily_checkin_bonus, checkin_mode, notes, invite_url, usage_status, sort_order, updated_at
        ) VALUES (
            :name, :url, :category, :balance, :welfare_rate, :plus_rate, :pro_rate, :signup_bonus,
            :daily_checkin_bonus, :checkin_mode, :notes, :invite_url, :usage_status, :sort_order, :updated_at
        )
        """,
        rows,
    )
    conn.commit()


def load_sites_from_db(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    init_sites_table(conn)
    rows = conn.execute(
        """
        SELECT name, url, category, balance, welfare_rate, plus_rate, pro_rate, signup_bonus,
               daily_checkin_bonus, checkin_mode, notes, invite_url, usage_status
        FROM sites
        ORDER BY sort_order, name
        """
    ).fetchall()
    return [dict(zip(SITE_FIELDS, row)) for row in rows]


def lowest(*values: float | None) -> float | None:
    real_values = [value for value in values if value is not None]
    return min(real_values) if real_values else None


def snapshot_from_site(site: dict[str, Any]) -> SiteSnapshot:
    welfare_rate = as_float(site.get("welfare_rate"))
    plus_rate = as_float(site.get("plus_rate"))
    pro_rate = as_float(site.get("pro_rate"))
    balance = as_float(site.get("balance"))
    lowest_rate = lowest(welfare_rate, plus_rate, pro_rate)

    return SiteSnapshot(
        name=site["name"],
        url=site["url"],
        category=site.get("category") or "收费站",
        invite_url=site.get("invite_url"),
        welfare_rate=welfare_rate,
        plus_rate=plus_rate,
        pro_rate=pro_rate,
        lowest_rate=lowest_rate,
        balance=balance,
        signup_bonus=as_float(site.get("signup_bonus")),
        daily_checkin_bonus=empty_to_none(site.get("daily_checkin_bonus")),
        checkin_mode="手动" if site.get("checkin_mode") == "手动" else "自动",
        source="manual",
        status="manual_seed",
        notes=site.get("notes", ""),
        usage_status="常用" if site.get("usage_status") == "常用" else "",
        checked_at=datetime.now().isoformat(timespec="seconds"),
    )


def save_snapshots(conn: sqlite3.Connection, snapshots: list[SiteSnapshot]) -> None:
    conn.executemany(
        """
        INSERT INTO snapshots (
            name, url, category, invite_url, welfare_rate, plus_rate, pro_rate, lowest_rate, balance,
            signup_bonus, daily_checkin_bonus, checkin_mode, source, status, notes, usage_status, checked_at
        ) VALUES (
            :name, :url, :category, :invite_url, :welfare_rate, :plus_rate, :pro_rate, :lowest_rate, :balance,
            :signup_bonus, :daily_checkin_bonus, :checkin_mode, :source, :status, :notes, :usage_status, :checked_at
        )
        """,
        [snapshot.__dict__ for snapshot in snapshots],
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    if "invite_url" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN invite_url TEXT")
    if "pro_rate" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN pro_rate REAL")
    if "usage_status" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN usage_status TEXT NOT NULL DEFAULT ''")
    if "checkin_mode" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN checkin_mode TEXT NOT NULL DEFAULT '自动'")
    conn.commit()


def export_csv(snapshots: list[SiteSnapshot]) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / "latest.csv"
    fields = [
        "name",
        "category",
        "lowest_rate",
        "plus_rate",
        "pro_rate",
        "signup_bonus",
        "daily_checkin_bonus",
        "checkin_mode",
        "balance",
        "url",
        "invite_url",
        "notes",
        "usage_status",
        "checked_at",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for snapshot in sort_snapshots(snapshots):
            row = {field: getattr(snapshot, field) for field in fields if hasattr(snapshot, field)}
            writer.writerow(row)
    return path


def format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.12f}".rstrip("0").rstrip(".")
    return str(value)


def format_rate(value: Any) -> str:
    if value is None:
        return "-"
    return f"{format_value(value)}<span>x</span>"

def invite_cell(invite_url: str | None) -> str:
    normalized_url = normalize_optional_http_url(invite_url)
    if not normalized_url:
        return ""
    escaped = html.escape(normalized_url, quote=True)
    return (
        f"<div class=\"invite-actions\">"
        f"<button class=\"copy-link\" type=\"button\" data-copy=\"{escaped}\">复制</button>"
        f"<a class=\"invite-open\" href=\"{escaped}\" target=\"_blank\" rel=\"noopener noreferrer\">打开</a>"
        f"</div>"
    )


def checkin_cell(value: Any, site_key: str) -> str:
    if not has_value(value):
        return "-"
    escaped_value = html.escape(format_value(value))
    escaped_key = html.escape(site_key, quote=True)
    return (
        f"<div class=\"checkin-actions\">"
        f"<span>{escaped_value}</span>"
        f"<button class=\"checkin-toggle\" type=\"button\" "
        f"data-checkin-key=\"{escaped_key}\" aria-pressed=\"false\">标记已签</button>"
        f"</div>"
    )


def render_quality_sites(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    cards = []
    for item in items:
        name = html.escape(str(item.get("name", "")))
        url = html.escape(str(item.get("url", "")), quote=True)
        notes = html.escape(str(item.get("notes", "")))
        kind_value = str(item.get("kind", "模型验纯"))
        kind = html.escape(kind_value)
        card_class = "tool-card-network" if kind_value == "IP 纯度" else "tool-card-quality"
        safety_tip = "。建议使用低额度临时 Key 测试。" if kind_value == "模型验纯" else "。建议结合多个结果综合判断。"
        cards.append(
            f"<article class=\"tool-card {card_class}\">"
            f"<div class=\"tool-kicker\">{kind}</div>"
            f"<div class=\"tool-name\">{name}</div>"
            f"<div class=\"tool-desc\">{notes}{safety_tip}</div>"
            f"<a class=\"tool-link\" href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\">打开 {name}</a>"
            f"</article>"
        )
    return "".join(cards)


def sort_snapshots(snapshots: list[SiteSnapshot]) -> list[SiteSnapshot]:
    return sorted(
        snapshots,
        key=lambda item: (
            item.lowest_rate is None,
            item.lowest_rate if item.lowest_rate is not None else 9999,
            item.plus_rate if item.plus_rate is not None else 9999,
            item.pro_rate if item.pro_rate is not None else 9999,
            item.name,
        ),
    )


def site_category(item: SiteSnapshot) -> str:
    return item.category or "收费站"


def render_rows(items: list[SiteSnapshot]) -> str:
    rows = []
    for rank, item in enumerate(items, start=1):
        if item.lowest_rate is not None and item.lowest_rate <= 0.03:
            row_class = "tier-low"
        elif item.lowest_rate is not None and item.lowest_rate <= 0.1:
            row_class = "tier-mid"
        else:
            row_class = ""
        if item.balance is not None and item.balance > 0:
            row_class = f"{row_class} has-balance".strip()
        if has_value(item.daily_checkin_bonus):
            row_class = f"{row_class} has-checkin".strip()
        if item.usage_status == "常用":
            row_class = f"{row_class} is-common-site".strip()
        balance_sort = item.balance if item.balance is not None else 0
        balance_display = format_value(item.balance)
        open_url_escaped = html.escape(item.url, quote=True)
        open_cell = (
            f"<a class=\"open-link\" href=\"{open_url_escaped}\" target=\"_blank\" rel=\"noopener noreferrer\">打开</a>"
            if item.url
            else "-"
        )
        usage_badge = '<span class="usage-badge">常用</span>' if item.usage_status == "常用" else ""
        rows.append(
            f"<tr class=\"{row_class}\" data-original-rank=\"{rank}\" data-balance=\"{balance_sort}\" data-checkin-mode=\"{html.escape(item.checkin_mode, quote=True)}\">"
            f"<td class=\"rank\">{rank}</td>"
            f"<td><div class=\"site-name\">{html.escape(item.name)}{usage_badge}</div><div class=\"site-url\">{html.escape(item.url or '-')}</div></td>"
            f"<td class=\"rate\">{format_rate(item.lowest_rate)}</td>"
            f"<td class=\"plus-rate\">{format_rate(item.plus_rate)}</td>"
            f"<td class=\"pro-rate\">{format_rate(item.pro_rate)}</td>"
            f"<td>{format_value(item.signup_bonus)}</td>"
            f"<td class=\"checkin-cell\">{checkin_cell(item.daily_checkin_bonus, item.url or item.name)}</td>"
            f"<td class=\"balance-cell\">{balance_display}</td>"
            f"<td>{open_cell}</td>"
            f"<td>{invite_cell(item.invite_url)}</td>"
            f"<td class=\"notes\">{html.escape(item.notes)}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_table_panel(
    panel_id: str,
    title: str,
    items: list[SiteSnapshot],
    search_placeholder: str,
    searchable: bool = False,
    show_top_button: bool = False,
) -> str:
    top_button = '<a class="top-jump" href="#top">顶部</a>' if show_top_button else ""
    return f"""
    <section id="{panel_id}" class="table-panel">
      <div class="panel-bar">
        <div>
          <div class="panel-title">{html.escape(title)}</div>
          <div class="panel-subtitle" data-total-count="{len(items)}">{len(items)} 个站点</div>
        </div>
        <div class="table-tools">
          <div class="legend"><span><i class="dot dot-low"></i>0.03x 及以下</span><span><i class="dot dot-mid"></i>0.1x 及以下</span></div>
          <div class="search-count"></div>
          {top_button}
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>站点</th>
            <th>最低倍率</th>
            <th>Plus</th>
            <th>Pro</th>
            <th>注册送</th>
            <th>签到送</th>
            <th>余额</th>
            <th>页面</th>
            <th>邀请链接</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          {render_rows(items)}
        </tbody>
      </table>
      <div class="no-results">没有找到匹配的站点</div>
    </section>
"""


def export_html(snapshots: list[SiteSnapshot]) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / "latest.html"
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sorted_items = sort_snapshots(snapshots)
    quality_sites = load_quality_sites()
    priced_items = [item for item in sorted_items if item.lowest_rate is not None]
    best_item = priced_items[0] if priced_items else None
    checkin_count = sum(1 for item in snapshots if has_value(item.daily_checkin_bonus))

    paid_items = [item for item in sorted_items if site_category(item) != "公益站"]
    free_items = [item for item in sorted_items if site_category(item) == "公益站"]

    best_name = html.escape(best_item.name) if best_item else "-"
    best_rate = format_value(best_item.lowest_rate) if best_item else "-"
    quality_tools = render_quality_sites(quality_sites)
    paid_section = render_table_panel("paid-sites", "收费站倍率排行", paid_items, "搜索收费站、备注、倍率", searchable=True)
    free_section = render_table_panel("free-sites", "公益站专区", free_items, "搜索公益站、备注、倍率", show_top_button=True)
    balance_filter_button = '<button id="balance-filter" class="jump-link balance-filter" type="button">只看有余额</button>'
    auto_checkin_filter_button = '<button id="auto-checkin-filter" class="jump-link checkin-filter auto-checkin-filter" type="button">自动签到</button>'
    manual_checkin_filter_button = '<button id="manual-checkin-filter" class="jump-link checkin-filter manual-checkin-filter" type="button">手动签到</button>'
    hint_text = "修改数据：用本地编辑器保存，或编辑项目目录下的 <code>sites.json</code> 后重新运行 <code>python monitor.py</code>"
    path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GPT 中转站倍率看板</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eef3f7;
      --panel: #ffffff;
      --text: #182230;
      --muted: #667085;
      --line: #d9dee7;
      --soft-line: #edf0f5;
      --accent: #0f9f8f;
      --accent-dark: #087f73;
      --accent-soft: #e3f7f4;
      --warn-soft: #fff4d7;
      --blue-soft: #eaf3ff;
      --shadow: 0 18px 44px rgba(16, 24, 40, 0.10);
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      min-width: 980px;
      scroll-behavior: smooth;
      background: linear-gradient(180deg, #fbfcfe 0, var(--bg) 310px), var(--bg);
      color: var(--text);
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      font-size: 14px;
    }}
    header {{
      padding: 30px 32px 22px;
      background:
        linear-gradient(135deg, rgba(255,255,255,0.96), rgba(247,251,255,0.94)),
        radial-gradient(circle at 18% 0%, rgba(15, 159, 143, 0.12), transparent 34%),
        radial-gradient(circle at 82% 6%, rgba(23, 92, 211, 0.12), transparent 30%),
        rgba(255, 255, 255, 0.92);
      border-bottom: 1px solid rgba(217, 222, 231, 0.88);
    }}
    .header-row {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      line-height: 1.2;
      font-weight: 820;
      letter-spacing: 0;
    }}
    .meta, .hint {{
      color: var(--muted);
      line-height: 1.7;
    }}
    .hint code {{
      padding: 2px 6px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: #344054;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(150px, 1fr));
      gap: 14px;
      margin-top: 22px;
    }}
    .metric {{
      position: relative;
      overflow: hidden;
      padding: 17px 18px;
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 10px 26px rgba(16, 24, 40, 0.06);
    }}
    .metric::before {{
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: linear-gradient(180deg, var(--accent), var(--blue));
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .metric-value {{
      font-size: 24px;
      font-weight: 820;
      line-height: 1.1;
      font-variant-numeric: tabular-nums;
    }}
    main {{
      padding: 24px 32px 34px;
    }}
    .table-panel {{
      margin-bottom: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .table-panel:nth-of-type(3) {{
      border-color: #b2ddff;
      box-shadow: 0 18px 42px rgba(23, 92, 211, 0.10);
    }}
    .table-panel:nth-of-type(4) {{
      border-color: #abefc6;
      box-shadow: 0 18px 42px rgba(6, 118, 71, 0.10);
    }}
    .tool-panel {{
      margin-bottom: 18px;
    }}
    .tool-panel > .panel-bar {{
      padding: 0 2px 12px;
      border: 0;
      background: transparent;
    }}
    .panel-bar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }}
    .panel-title {{
      font-size: 17px;
      font-weight: 800;
    }}
    .panel-subtitle {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }}
    .legend {{
      display: flex;
      align-items: center;
      gap: 14px;
      color: var(--muted);
      font-size: 14px;
      font-weight: 650;
    }}
    .table-tools {{
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .global-search {{
      margin-bottom: 18px;
      padding: 16px 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background:
        linear-gradient(135deg, rgba(255,255,255,0.98), rgba(248,251,255,0.96));
      box-shadow: var(--shadow);
    }}
    .global-search-row {{
      display: grid;
      grid-template-columns: minmax(260px, 430px) 1fr auto;
      gap: 12px;
      align-items: center;
    }}
    .search-field {{
      position: relative;
      width: 100%;
    }}
    .report-search {{
      width: 100%;
      height: 40px;
      padding: 0 76px 0 14px;
      border: 1px solid #cdd6e1;
      border-radius: 8px;
      background: #fff;
      color: var(--text);
      font: inherit;
      font-size: 14px;
      outline: none;
      transition: 120ms ease;
    }}
    .search-help {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      line-height: 1.5;
      max-width: 100%;
    }}
    .global-search-count {{
      position: absolute;
      right: 12px;
      top: 50%;
      transform: translateY(-50%);
      min-width: 54px;
      color: #175cd3;
      font-size: 13px;
      font-weight: 850;
      text-align: right;
      pointer-events: none;
    }}
    .quick-jumps {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 6px;
      flex-wrap: wrap;
      padding: 4px;
      border: 1px solid #dbe7f2;
      border-radius: 999px;
      background: #f8fbff;
    }}
    .jump-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 32px;
      padding: 0 12px;
      border: 1px solid transparent;
      border-radius: 999px;
      background: transparent;
      color: #344054;
      font-size: 13px;
      font-weight: 850;
      text-decoration: none;
      white-space: nowrap;
      transition: 120ms ease;
    }}
    .jump-link:hover {{
      border-color: #84adff;
      background: #eaf3ff;
      color: #175cd3;
      transform: translateY(-1px);
    }}
    .balance-filter {{
      cursor: pointer;
      font: inherit;
    }}
    .balance-filter.is-active {{
      border-color: #84adff;
      background: #eaf3ff;
      color: #175cd3;
    }}
    .checkin-filter {{
      cursor: pointer;
      font: inherit;
    }}
    .checkin-filter.is-active {{
      border-color: #f4b740;
      background: #fff4d7;
      color: #a15c00;
    }}
    .auto-checkin-filter {{
      border-color: #b2ccff;
      background: #f5f9ff;
      color: #175cd3;
    }}
    .auto-checkin-filter.is-active {{
      border-color: #528bff;
      background: #eaf3ff;
      color: #004eeb;
    }}
    .jump-link.is-free {{
      border-color: #abefc6;
      background: #ecfdf3;
      color: #067647;
    }}
    .report-search:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 159, 143, 0.12);
    }}
    .report-search::placeholder {{
      color: #98a2b3;
    }}
    .report-search::-webkit-search-cancel-button {{
      display: none;
    }}
    .report-search-clear {{
      position: absolute;
      top: 50%;
      right: 6px;
      z-index: 2;
      display: none;
      align-items: center;
      justify-content: center;
      width: 27px;
      height: 27px;
      padding: 0;
      border: 0;
      border-radius: 50%;
      background: transparent;
      color: #667085;
      font: inherit;
      font-size: 20px;
      line-height: 1;
      cursor: pointer;
      transform: translateY(-50%);
    }}
    .report-search-clear:hover {{
      background: #e7edf4;
      color: #182230;
    }}
    .search-field.has-value .report-search-clear {{ display: inline-flex; }}
    .search-field.has-value .global-search-count {{ right: 42px; }}
    .search-count {{
      min-width: 58px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
      text-align: right;
    }}
    .top-jump {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 30px;
      padding: 0 11px;
      border: 1px solid #cdd6e1;
      border-radius: 999px;
      background: #ffffff;
      color: #175cd3;
      font-size: 13px;
      font-weight: 850;
      text-decoration: none;
      transition: transform 0.15s ease, border-color 0.15s ease, background 0.15s ease;
    }}
    .top-jump:hover {{
      border-color: #84adff;
      background: #eaf3ff;
      transform: translateY(-1px);
    }}
    .no-results {{
      display: none;
      padding: 26px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-weight: 800;
      text-align: center;
    }}
    .no-results.is-visible {{
      display: block;
    }}
    .tool-count {{
      padding: 5px 9px;
      border: 1px solid #cdd6e1;
      border-radius: 999px;
      background: #ffffff;
      color: #475467;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .tool-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(440px, 1fr));
      gap: 12px;
    }}
    .tool-card {{
      --tool-accent: #175cd3;
      position: relative;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      grid-template-areas:
        "kicker action"
        "name action"
        "desc action";
      column-gap: 18px;
      padding: 14px 16px;
      overflow: hidden;
      border: 1px solid #d9e1ea;
      border-radius: 8px;
      background: #ffffff;
      box-shadow: 0 8px 24px rgba(16, 24, 40, 0.06);
      transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
    }}
    .tool-card::before {{
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: var(--tool-accent);
    }}
    .tool-card:hover {{
      border-color: #b8c6d5;
      box-shadow: 0 12px 30px rgba(16, 24, 40, 0.10);
      transform: translateY(-2px);
    }}
    .tool-card-relay {{ --tool-accent: #0f9f8f; }}
    .tool-card-reference {{ --tool-accent: #175cd3; }}
    .tool-card-monitor {{ --tool-accent: #d68a00; }}
    .tool-card-prompt {{ --tool-accent: #7a5af8; }}
    .tool-card-quality {{ --tool-accent: #067647; }}
    .tool-card-network {{ --tool-accent: #0e7090; }}
    .tool-kicker {{
      grid-area: kicker;
      align-self: start;
      justify-self: start;
      padding: 3px 7px;
      border-radius: 6px;
      background: #f2f4f7;
      color: var(--tool-accent);
      font-size: 12px;
      font-weight: 850;
    }}
    .tool-name {{
      grid-area: name;
      margin: 8px 0 4px;
      font-size: 16px;
      font-weight: 850;
    }}
    .tool-desc {{
      grid-area: desc;
      color: #475467;
      font-size: 13px;
      line-height: 1.55;
    }}
    .tool-link {{
      grid-area: action;
      display: inline-flex;
      align-self: center;
      justify-self: end;
      align-items: center;
      justify-content: center;
      min-width: 142px;
      height: 42px;
      padding: 0 18px;
      border: 1px solid #b2ccff;
      border-radius: 8px;
      background: #eaf3ff;
      color: #175cd3;
      font-size: 14px;
      font-weight: 800;
      text-decoration: none;
      white-space: nowrap;
      transition: 120ms ease;
    }}
    .tool-link:hover {{
      border-color: #84adff;
      background: #dceaff;
      transform: translateY(-1px);
    }}
    .dot {{
      width: 9px;
      height: 9px;
      display: inline-block;
      border-radius: 50%;
      margin-right: 5px;
      vertical-align: -1px;
    }}
    .dot-low {{ background: #0f9f8f; }}
    .dot-mid {{ background: #d99a00; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
    }}
    th, td {{
      padding: 13px 14px;
      border-bottom: 1px solid var(--soft-line);
      text-align: left;
      white-space: nowrap;
      vertical-align: middle;
    }}
    th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f5f7fb;
      color: #344054;
      font-size: 14px;
      font-weight: 750;
      box-shadow: inset 0 -1px 0 var(--line);
    }}
    tbody tr {{
      transition: background 120ms ease, box-shadow 120ms ease;
    }}
    tbody tr:hover {{
      background: #f7fafc;
      outline: 1px solid #d7e1ec;
      outline-offset: -1px;
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    .rank {{
      width: 52px;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }}
    .site-name {{
      font-weight: 700;
      color: #182230;
    }}
    .site-url {{
      margin-top: 3px;
      max-width: 360px;
      overflow: hidden;
      text-overflow: ellipsis;
      color: var(--muted);
      font-size: 12px;
    }}
    .rate {{
      color: var(--accent-dark);
      font-size: 16px;
      font-weight: 800;
      font-variant-numeric: tabular-nums;
    }}
    .plus-rate span,
    .pro-rate span,
    .rate span {{
      margin-left: 1px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .plus-rate,
    .pro-rate {{
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}
    .tier-low .rate {{
      color: #00806f;
    }}
    .tier-low {{
      background: linear-gradient(90deg, rgba(227, 247, 244, 0.75), #fff 34%);
    }}
    .tier-mid {{
      background: linear-gradient(90deg, rgba(255, 244, 215, 0.65), #fff 34%);
    }}
    .is-common-site {{
      background: linear-gradient(90deg, rgba(255, 230, 143, 0.48), rgba(255, 255, 255, 0.92) 42%);
      outline: 1px solid rgba(217, 154, 0, 0.26);
      outline-offset: -1px;
    }}
    .is-common-site .site-name {{
      color: #7a4b00;
    }}
    .usage-badge {{
      display: inline-flex;
      align-items: center;
      min-height: 20px;
      margin-left: 8px;
      padding: 2px 7px;
      border: 1px solid #fedf89;
      border-radius: 999px;
      background: #fffaeb;
      color: #93370d;
      font-size: 12px;
      font-weight: 800;
      vertical-align: 1px;
    }}
    .has-balance {{
      box-shadow: inset 4px 0 0 #175cd3;
    }}
    .has-balance .site-name::after {{
      content: "有余额";
      display: inline-flex;
      align-items: center;
      height: 19px;
      margin-left: 8px;
      padding: 0 7px;
      border: 1px solid #b2ccff;
      border-radius: 999px;
      background: #eaf3ff;
      color: #175cd3;
      font-size: 12px;
      font-weight: 800;
      vertical-align: 1px;
    }}
    .balance-cell {{
      font-weight: 800;
      font-variant-numeric: tabular-nums;
    }}
    .has-balance .balance-cell {{
      color: #175cd3;
    }}
    .has-checkin {{
      box-shadow: inset 4px 0 0 #d99a00;
    }}
    .has-balance.has-checkin {{
      box-shadow: inset 4px 0 0 #175cd3, inset 8px 0 0 #d99a00;
    }}
    .has-checkin .site-name::before {{
      content: "待签到";
      display: inline-flex;
      align-items: center;
      height: 19px;
      margin-right: 8px;
      padding: 0 7px;
      border: 1px solid #fedf89;
      border-radius: 999px;
      background: #fff4d7;
      color: #93370d;
      font-size: 12px;
      font-weight: 800;
      vertical-align: 1px;
    }}
    .has-checkin.signed-today {{
      box-shadow: inset 4px 0 0 #12b76a;
    }}
    .has-balance.has-checkin.signed-today {{
      box-shadow: inset 4px 0 0 #175cd3, inset 8px 0 0 #12b76a;
    }}
    .has-checkin.signed-today .site-name::before {{
      content: "已签到";
      border-color: #abefc6;
      background: #ecfdf3;
      color: #067647;
    }}
    .checkin-cell {{
      font-weight: 800;
      color: #93370d;
      max-width: 240px;
      white-space: normal;
    }}
    .checkin-actions {{
      display: inline-flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .checkin-toggle {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 28px;
      padding: 0 10px;
      border: 1px solid #fedf89;
      border-radius: 8px;
      background: #fff8e6;
      color: #93370d;
      font: inherit;
      font-size: 12px;
      font-weight: 800;
      cursor: pointer;
      transition: 120ms ease;
    }}
    .checkin-toggle:hover {{
      border-color: #fdb022;
      background: #fff4d7;
      transform: translateY(-1px);
    }}
    .signed-today .checkin-toggle {{
      border-color: #abefc6;
      background: #ecfdf3;
      color: #067647;
    }}
    .open-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 52px;
      height: 30px;
      padding: 0 12px;
      border: 1px solid #b8ddd8;
      border-radius: 8px;
      background: var(--accent-soft);
      color: var(--accent-dark);
      font-weight: 700;
      text-decoration: none;
      transition: 120ms ease;
    }}
    .open-link:hover {{
      border-color: var(--accent);
      background: #d3f1ed;
      transform: translateY(-1px);
    }}
    .invite-actions {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    .copy-link,
    .invite-open {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 30px;
      padding: 0 12px;
      border: 1px solid #cdd6e1;
      border-radius: 8px;
      background: #fff;
      color: #344054;
      font: inherit;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
      transition: 120ms ease;
    }}
    .copy-link {{
      border-color: #9fc8ff;
      background: var(--blue-soft);
      color: #175cd3;
    }}
    .copy-link:hover,
    .invite-open:hover {{
      border-color: #84adff;
      background: #dceaff;
      transform: translateY(-1px);
    }}
    .notes {{
      color: #475467;
      white-space: normal;
      min-width: 180px;
    }}
    @media (max-width: 1100px) {{
      body {{
        min-width: 0;
      }}
      .header-row {{
        display: block;
      }}
      .summary {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      main {{
        padding: 16px;
        overflow-x: auto;
      }}
      header {{
        padding: 20px 16px 16px;
      }}
      .table-panel {{
        min-width: 920px;
      }}
      .table-tools {{
        justify-content: flex-start;
      }}
      .global-search-row {{
        grid-template-columns: 1fr;
      }}
    }}
    @media (max-width: 760px) {{
      .tool-grid {{
        grid-template-columns: 1fr;
      }}
      .tool-card {{
        grid-template-columns: 1fr;
        grid-template-areas:
          "kicker"
          "name"
          "desc"
          "action";
      }}
      .tool-link {{
        justify-self: stretch;
        width: 100%;
        margin-top: 12px;
      }}
    }}
  </style>
  <link rel="stylesheet" href="/theme.css">
  <script src="/theme.js"></script>
</head>
<body id="top">
  <header>
    <div class="header-row">
      <div>
        <h1>GPT 中转站倍率看板</h1>
        <div class="meta">更新时间：{html.escape(checked_at)}</div>
      </div>
      <div class="header-theme"><div class="hint">{hint_text}</div><div data-theme-control></div></div>
    </div>
    <section class="summary" aria-label="summary">
      <div class="metric"><div class="metric-label">站点数</div><div class="metric-value">{len(snapshots)}</div></div>
      <div class="metric"><div class="metric-label">当前最低</div><div class="metric-value">{best_rate}x</div></div>
      <div class="metric"><div class="metric-label">最低站点</div><div class="metric-value">{best_name}</div></div>
      <div class="metric"><div class="metric-label">今日待签到</div><div id="checkin-count" class="metric-value">{checkin_count}</div></div>
    </section>
  </header>
  <main>
    <section class="tool-panel" aria-labelledby="tools-title">
      <div class="panel-bar">
        <div>
          <div id="tools-title" class="panel-title">常用工具</div>
          <div class="panel-subtitle">账号池、免费额度、站点参考、端点监控、提示词与检测工具</div>
        </div>
        <div class="tool-count">{4 + len(quality_sites)} 个工具</div>
      </div>
      <div class="tool-grid">
      <article class="tool-card tool-card-relay">
        <div class="tool-kicker">账号池与转发</div>
        <div class="tool-name">皮皮工艺站</div>
        <div class="tool-desc">导入自己的上游账号后统一管理，并通过公网 API 地址提供访问。上游账号、配额和使用风险由用户自行承担。</div>
        <a class="tool-link" href="https://api.picpi.top/dashboard" target="_blank" rel="noopener noreferrer">打开皮皮工艺站</a>
      </article>
      <article class="tool-card tool-card-reference">
        <div class="tool-kicker">站点参考</div>
        <div class="tool-name">RelayWatch</div>
        <div class="tool-desc">查看公开中转站目录、模型覆盖、最低倍率、公告和站点状态，辅助维护本站价格表。</div>
        <a class="tool-link" href="http://relaywatch.online/" target="_blank" rel="noopener noreferrer">打开 RelayWatch</a>
      </article>
      <article class="tool-card tool-card-monitor">
        <div class="tool-kicker">可用性监控</div>
        <div class="tool-name">AI 端点监控</div>
        <div class="tool-desc">查看公开 API 端点的可用状态、模型数量、响应时间和剩余额度，使用前可快速确认状态。</div>
        <a class="tool-link" href="http://hk.mlorr.online:3001/" target="_blank" rel="noopener noreferrer">打开端点监控</a>
      </article>
      <article class="tool-card tool-card-prompt">
        <div class="tool-kicker">提示词</div>
        <div class="tool-name">提示词优化器</div>
        <div class="tool-desc">把普通需求整理成更清晰、可执行的系统提示词或用户提示词，再投入 GPT 或 Codex 使用。</div>
        <a class="tool-link" href="https://prompt.always200.com/#/basic/system" target="_blank" rel="noopener noreferrer">打开提示词优化器</a>
      </article>
      {quality_tools}
      </div>
    </section>
    <section class="global-search" aria-label="全局搜索">
      <div class="global-search-row">
        <div class="search-field">
          <input id="report-search" class="report-search" type="search" placeholder="搜索全部站点、备注、倍率">
          <div id="global-search-count" class="global-search-count"></div>
          <button id="report-search-clear" class="report-search-clear" type="button" aria-label="清空搜索" title="清空搜索">×</button>
        </div>
        <div class="search-help">同时过滤收费站和公益站；默认不搜网址，输入 <code>api.</code>、<code>/keys</code>、<code>.com</code> 时才匹配网址。</div>
        <nav class="quick-jumps" aria-label="快速跳转">
          {balance_filter_button}
          {auto_checkin_filter_button}
          {manual_checkin_filter_button}
          <a class="jump-link" href="/calculator.html" target="ai_price_monitor_calculator" rel="noopener">成本计算器</a>
          <a class="jump-link" href="#paid-sites">收费站</a>
          <a class="jump-link is-free" href="#free-sites">公益站</a>
        </nav>
      </div>
    </section>
{paid_section}
{free_section}
  </main>
  <script>
    function todayString() {{
      const now = new Date();
      const year = now.getFullYear();
      const month = String(now.getMonth() + 1).padStart(2, "0");
      const day = String(now.getDate()).padStart(2, "0");
      return `${{year}}-${{month}}-${{day}}`;
    }}

    function checkinStorageKey(button) {{
      return `ai-price-monitor:checkin:${{todayString()}}:${{button.dataset.checkinKey}}`;
    }}

    function applyCheckinState() {{
      let remaining = 0;
      document.querySelectorAll(".checkin-toggle").forEach((button) => {{
        const row = button.closest("tr");
        const signed = window.localStorage.getItem(checkinStorageKey(button)) === "1";
        row.classList.toggle("signed-today", signed);
        button.textContent = signed ? "已签到" : "标记已签";
        button.setAttribute("aria-pressed", signed ? "true" : "false");
        if (!signed) remaining += 1;
      }});
      const count = document.querySelector("#checkin-count");
      if (count) count.textContent = String(remaining);
    }}

    function applyReportSearch() {{
      const input = document.querySelector("#report-search");
      const query = input ? input.value.trim().toLowerCase() : "";
      const shouldSearchUrl = /[.:/]/.test(query);
      const balanceFilter = document.querySelector("#balance-filter");
      const balanceOnly = balanceFilter?.classList.contains("is-active") || false;
      const autoCheckinFilter = document.querySelector("#auto-checkin-filter");
      const manualCheckinFilter = document.querySelector("#manual-checkin-filter");
      const checkinMode = autoCheckinFilter?.classList.contains("is-active")
        ? "自动"
        : manualCheckinFilter?.classList.contains("is-active")
          ? "手动"
          : "";
      let totalVisible = 0;
      let totalRows = 0;
      let totalBalanceRows = 0;
      let totalAutoPendingCheckinRows = 0;
      let totalManualPendingCheckinRows = 0;

      document.querySelectorAll(".table-panel").forEach((panel) => {{
        const count = panel.querySelector(".search-count");
        const noResults = panel.querySelector(".no-results");
        const tbody = panel.querySelector("tbody");
        const rows = Array.from(panel.querySelectorAll("tbody tr"));
        const orderedRows = rows.slice().sort((a, b) => {{
          return Number(a.dataset.originalRank || 0) - Number(b.dataset.originalRank || 0);
        }});
        let visible = 0;
        totalRows += rows.length;
        totalBalanceRows += rows.filter((row) => Number(row.dataset.balance || 0) > 0).length;
        rows.forEach((row) => {{
          if (!row.classList.contains("has-checkin") || row.classList.contains("signed-today")) return;
          if (row.dataset.checkinMode === "手动") totalManualPendingCheckinRows += 1;
          else totalAutoPendingCheckinRows += 1;
        }});

        orderedRows.forEach((row) => {{
          if (tbody) tbody.appendChild(row);
          const searchableCells = Array.from(row.children).filter((cell) => {{
            return !cell.querySelector(".site-url") && !cell.querySelector(".open-link") && !cell.querySelector(".invite-actions");
          }});
          const siteName = row.querySelector(".site-name")?.textContent || "";
          const siteUrl = row.querySelector(".site-url")?.textContent || "";
          const searchableText = [siteName, ...searchableCells.map((cell) => cell.textContent)]
            .join(" ")
            .toLowerCase();
          const urlText = shouldSearchUrl ? siteUrl.toLowerCase() : "";
          const hasBalance = Number(row.dataset.balance || 0) > 0;
          const hasPendingCheckin = row.classList.contains("has-checkin") && !row.classList.contains("signed-today");
          const matchesCheckinMode = !checkinMode || row.dataset.checkinMode === checkinMode;
          const matched =
            (!query || searchableText.includes(query) || urlText.includes(query)) &&
            (!balanceOnly || hasBalance) &&
            (!checkinMode || (hasPendingCheckin && matchesCheckinMode));
          row.hidden = !matched;
          if (matched) {{
            visible += 1;
            const rank = row.querySelector(".rank");
            if (rank) rank.textContent = String(visible);
          }}
        }});

        totalVisible += visible;
        const subtitle = panel.querySelector(".panel-subtitle");
        if (subtitle) subtitle.textContent = `${{visible}} 个站点`;
        if (count) count.textContent = `${{visible}} / ${{rows.length}}`;
        if (noResults) noResults.classList.toggle("is-visible", visible === 0);
      }});

      const globalCount = document.querySelector("#global-search-count");
      if (globalCount) globalCount.textContent = `${{totalVisible}} / ${{totalRows}}`;
      if (balanceFilter) {{
        balanceFilter.textContent = balanceOnly ? `显示全部 (${{totalRows}})` : `只看有余额 (${{totalBalanceRows}})`;
      }}
      if (autoCheckinFilter) {{
        autoCheckinFilter.textContent = `自动签到 (${{totalAutoPendingCheckinRows}})`;
      }}
      if (manualCheckinFilter) {{
        manualCheckinFilter.textContent = `手动签到 (${{totalManualPendingCheckinRows}})`;
      }}
    }}

    document.querySelector("#balance-filter")?.addEventListener("click", (event) => {{
      event.currentTarget.classList.toggle("is-active");
      applyReportSearch();
    }});

    function toggleCheckinFilter(mode) {{
      const autoCheckinFilter = document.querySelector("#auto-checkin-filter");
      const manualCheckinFilter = document.querySelector("#manual-checkin-filter");
      const target = mode === "自动" ? autoCheckinFilter : manualCheckinFilter;
      const wasActive = target?.classList.contains("is-active");
      autoCheckinFilter?.classList.remove("is-active");
      manualCheckinFilter?.classList.remove("is-active");
      if (!wasActive) target?.classList.add("is-active");
      applyReportSearch();
    }}

    document.querySelector("#auto-checkin-filter")?.addEventListener("click", () => toggleCheckinFilter("自动"));
    document.querySelector("#manual-checkin-filter")?.addEventListener("click", () => toggleCheckinFilter("手动"));

    document.addEventListener("click", async (event) => {{
      const checkinButton = event.target.closest(".checkin-toggle");
      if (checkinButton) {{
        const key = checkinStorageKey(checkinButton);
        const signed = window.localStorage.getItem(key) === "1";
        if (signed) {{
          window.localStorage.removeItem(key);
        }} else {{
          window.localStorage.setItem(key, "1");
        }}
        applyCheckinState();
        applyReportSearch();
        return;
      }}

      const button = event.target.closest(".copy-link");
      if (!button) return;
      const text = button.dataset.copy;
      try {{
        await navigator.clipboard.writeText(text);
        const oldText = button.textContent;
        button.textContent = "已复制";
        window.setTimeout(() => {{
          button.textContent = oldText;
        }}, 1200);
      }} catch (error) {{
        window.prompt("复制这个邀请链接：", text);
      }}
    }});
    applyCheckinState();
    applyReportSearch();
    const reportSearchInput = document.querySelector("#report-search");
    const reportSearchField = reportSearchInput?.closest(".search-field");
    const reportSearchClear = document.querySelector("#report-search-clear");

    function handleReportSearchInput() {{
      reportSearchField?.classList.toggle("has-value", Boolean(reportSearchInput?.value));
      applyReportSearch();
    }}

    reportSearchInput?.addEventListener("input", handleReportSearchInput);
    reportSearchClear?.addEventListener("click", () => {{
      reportSearchInput.value = "";
      handleReportSearchInput();
      reportSearchInput.focus();
    }});
  </script></body>
</html>
""",
        encoding="utf-8",
    )
    return path

def collect_manual(sites: list[dict[str, Any]]) -> list[SiteSnapshot]:
    return [snapshot_from_site(site) for site in sites]


def build_reports(
    sites: list[dict[str, Any]],
) -> tuple[Path, Path, int]:
    DATA_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)

    snapshots = collect_manual(sites)

    with sqlite3.connect(DB_FILE) as conn:
        init_db(conn)
        sync_sites_to_db(conn, sites)
        save_snapshots(conn, snapshots)

    csv_path = export_csv(snapshots)
    html_path = export_html(snapshots)
    return csv_path, html_path, len(snapshots)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor AI API relay site rates.")
    parser.add_argument("--source", choices=("json", "db"), default="json", help="Load site data from sites.json or SQLite sites table.")
    args = parser.parse_args()

    if args.source == "db":
        with sqlite3.connect(DB_FILE) as conn:
            sites = load_sites_from_db(conn)
    else:
        sites = load_sites()

    csv_path, html_path, count = build_reports(sites)
    print(f"Saved {count} snapshots")
    print(f"CSV: {csv_path}")
    print(f"HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())













