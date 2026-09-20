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
    "recharge_ratio",
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


def normalized_checkin_mode(site: dict[str, Any], daily_checkin_bonus: Any) -> str:
    if daily_checkin_bonus is None:
        return "无签到"
    return "手动" if site.get("checkin_mode") == "手动" else "自动"


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
            recharge_ratio TEXT NOT NULL DEFAULT '1:1',
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
        "recharge_ratio": "ALTER TABLE sites ADD COLUMN recharge_ratio TEXT NOT NULL DEFAULT '1:1'",
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
                "recharge_ratio": site.get("recharge_ratio") or "",
                "welfare_rate": as_float(site.get("welfare_rate")),
                "plus_rate": as_float(site.get("plus_rate")),
                "pro_rate": as_float(site.get("pro_rate")),
                "signup_bonus": as_float(site.get("signup_bonus")),
                "daily_checkin_bonus": empty_to_none(site.get("daily_checkin_bonus")),
                "checkin_mode": normalized_checkin_mode(site, empty_to_none(site.get("daily_checkin_bonus"))),
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
            name, url, category, balance, recharge_ratio, welfare_rate, plus_rate, pro_rate, signup_bonus,
            daily_checkin_bonus, checkin_mode, notes, invite_url, usage_status, sort_order, updated_at
        ) VALUES (
            :name, :url, :category, :balance, :recharge_ratio, :welfare_rate, :plus_rate, :pro_rate, :signup_bonus,
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
        SELECT name, url, category, balance, recharge_ratio, welfare_rate, plus_rate, pro_rate, signup_bonus,
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
        checkin_mode=normalized_checkin_mode(site, empty_to_none(site.get("daily_checkin_bonus"))),
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
        is_checkin = bool(item.get("daily_checkin"))
        card_class = (
            "tool-card-checkin" if is_checkin
            else "tool-card-network" if kind_value == "IP 纯度"
            else "tool-card-quality"
        )
        kicker = "每日签到提醒" if is_checkin else kind
        safety_tip = (
            "。每天打开后先完成签到，领取 HVO 积分。"
            if is_checkin
            else "。建议使用低额度临时 Key 测试。" if kind_value == "模型验纯"
            else "。建议结合多个结果综合判断。"
        )
        cards.append(
            f"<article class=\"tool-card {card_class}\">"
            f"<div class=\"tool-kicker\">{kicker}</div>"
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
        elif item.balance is not None and item.balance < 0:
            row_class = f"{row_class} has-debt".strip()
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
        status_badges = []
        if item.usage_status == "常用":
            status_badges.append('<span class="status-badge badge-common">★ 常用</span>')
        if item.balance is not None and item.balance > 0:
            status_badges.append('<span class="status-badge badge-balance">💰 有余额</span>')
        if has_value(item.daily_checkin_bonus):
            status_badges.append('<span class="status-badge badge-checkin">⏳ 待签到</span>')
        status_badges_html = "".join(status_badges)
        rows.append(
            f"<tr class=\"{row_class}\" data-original-rank=\"{rank}\" data-balance=\"{balance_sort}\" data-checkin-mode=\"{html.escape(item.checkin_mode, quote=True)}\" data-usage-status=\"{html.escape(item.usage_status, quote=True)}\">"
            f"<td class=\"rank\">{rank}</td>"
            f"<td class=\"site-col\"><div class=\"site-name\">{html.escape(item.name)}</div><div class=\"site-badges\">{status_badges_html}</div><div class=\"site-url\">{html.escape(item.url or '-')}</div></td>"
            f"<td class=\"rate\">{format_rate(item.lowest_rate)}</td>"
            f"<td class=\"plus-rate\">{format_rate(item.plus_rate)}</td>"
            f"<td class=\"pro-rate\">{format_rate(item.pro_rate)}</td>"
            f"<td class=\"signup-cell\">{format_value(item.signup_bonus)}</td>"
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
    balance_filter_button = '<button id="balance-filter" class="jump-link balance-filter" type="button" aria-pressed="false">只看有余额</button>'
    common_filter_button = '<button id="common-filter" class="jump-link common-filter" type="button">只看常用站</button>'
    auto_checkin_filter_button = '<button id="auto-checkin-filter" class="jump-link checkin-filter auto-checkin-filter" type="button" aria-pressed="false">自动签到</button>'
    auto_checkin_complete_button = '<button id="auto-checkin-complete" class="jump-link auto-checkin-complete" type="button">自动全部已签</button>'
    manual_checkin_filter_button = '<button id="manual-checkin-filter" class="jump-link checkin-filter manual-checkin-filter" type="button" aria-pressed="false">手动签到</button>'
    hint_text = "修改数据：用本地编辑器保存，或编辑项目目录下的 <code>sites.json</code> 后重新运行 <code>python monitor.py</code>"
    path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI 模型中转站倍率看板</title>
  <style>
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      min-width: 980px;
      scroll-behavior: smooth;
      background:
        radial-gradient(circle at 12% -6%, rgba(15, 159, 143, 0.10), transparent 30%),
        radial-gradient(circle at 88% -8%, rgba(23, 92, 211, 0.09), transparent 28%),
        linear-gradient(180deg, #fbfcfe 0, var(--bg) 310px);
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
      letter-spacing: -0.02em;
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
    .metric-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      margin-bottom: 10px;
      border-radius: 10px;
      background: var(--accent-soft);
      color: var(--accent-dark);
      font-size: 16px;
    }}
    .metric:nth-child(2) .metric-icon {{ background: var(--blue-soft); color: var(--blue); }}
    .metric:nth-child(3) .metric-icon {{ background: var(--gold-soft); color: var(--alert); }}
    .metric:nth-child(4) .metric-icon {{ background: var(--warn-soft); color: var(--warn); }}
    .metric {{
      position: relative;
      overflow: hidden;
      padding: 17px 18px;
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid var(--line);
      border-radius: var(--radius-m);
      box-shadow: var(--shadow-card);
      transition: transform 140ms ease, box-shadow 140ms ease, border-color 140ms ease;
    }}
    .metric:hover {{
      transform: translateY(-2px);
      border-color: rgba(15, 159, 143, 0.45);
      box-shadow: 0 14px 30px rgba(16, 24, 40, 0.10);
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
      border-radius: var(--radius-l);
      box-shadow: var(--shadow-panel);
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
      box-shadow: var(--shadow-panel);
    }}
    .global-search-row {{
      display: grid;
      grid-template-columns: minmax(300px, 430px) minmax(0, 1fr);
      gap: 12px 18px;
      align-items: center;
    }}
    .search-help {{ grid-column: 1; grid-row: 2; margin: 0; }}
    .status-legend {{ grid-column: 2; grid-row: 2; display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:6px 12px; margin:0; color:var(--muted); font-size:12px; font-weight:750; }}
    .status-legend-title {{ color:var(--text); font-weight:850; }}
    .status-legend-item {{ white-space:nowrap; }}
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
      grid-column: 2;
      grid-row: 1;
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 6px;
      flex-wrap: wrap;
      padding: 4px;
      border: 1px solid #dbe7f2;
      border-radius: 999px;
      background: #f8fbff;
      margin-top: 0;
      border-radius: 12px;
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
    .group-link.is-current {{
      border-color: #5f8ca7;
      background: #dceaf2;
      color: #244d66;
      box-shadow: inset 0 0 0 1px rgba(95,140,167,.22);
    }}
    .balance-filter {{
      cursor: pointer;
      font: inherit;
    }}
    .balance-filter.is-active {{
      border-color: #2f7fc1;
      background: #d8ebfb;
      color: #064f96;
      box-shadow: 0 0 0 2px rgba(47,127,193,.28), inset 0 0 0 1px rgba(255,255,255,.55);
      font-weight: 900;
    }}
    .common-filter {{
      cursor: pointer;
      font: inherit;
    }}
    .common-filter.is-active {{
      border-color: #e7a23b;
      background: #fff4d6;
      color: #9a5b00;
      box-shadow: 0 0 0 2px rgba(231,162,59,.25), inset 0 0 0 1px rgba(255,255,255,.55);
      font-weight: 900;
    }}
    .checkin-filter {{
      cursor: pointer;
      font: inherit;
    }}
    .checkin-filter.is-active {{
      border-color: #f4b740;
      background: #fff4d7;
      color: #a15c00;
      box-shadow: 0 0 0 2px rgba(217,154,22,.24), inset 0 0 0 1px rgba(255,255,255,.35);
      font-weight: 900;
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
      box-shadow: 0 0 0 2px rgba(82,139,255,.25), inset 0 0 0 1px rgba(255,255,255,.55);
      font-weight: 900;
    }}
    .auto-checkin-complete {{
      border-color: #175cd3;
      background: #175cd3;
      color: #fff;
    }}
    .auto-checkin-complete:hover {{
      border-color: #004eeb;
      background: #004eeb;
      color: #fff;
    }}
    .auto-checkin-complete:disabled {{
      border-color: #d0d5dd;
      background: #f2f4f7;
      color: #98a2b3;
      cursor: not-allowed;
      transform: none;
    }}
    .manual-open-control {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px;
      border: 1px solid #c7d3df;
      border-radius: 999px;
      background: #f4f7fa;
    }}
    .manual-open-count {{
      width: 46px;
      height: 28px;
      padding: 0 5px 0 8px;
      border: 0;
      outline: 0;
      background: transparent;
      color: #344054;
      font: inherit;
      font-size: 13px;
      font-weight: 850;
    }}
    .manual-open-count:focus {{
      border-radius: 6px;
      background: #fff;
      box-shadow: inset 0 0 0 1px #7b9bb5;
    }}
    .manual-checkin-open {{
      cursor: pointer;
      border-color: #9bb3c7;
      background: #e8f0f6;
      color: #31536d;
      font: inherit;
    }}
    .manual-checkin-open:hover {{
      border-color: #6f93ad;
      background: #d8e5ee;
      color: #27475e;
    }}
    .manual-checkin-open:disabled {{
      border-color: transparent;
      background: transparent;
      color: #98a2b3;
      cursor: not-allowed;
      transform: none;
    }}
    .manual-checkin-complete {{
      cursor: pointer;
      border-color: #5f8ca7;
      background: #5f8ca7;
      color: #fff;
      font: inherit;
    }}
    .manual-checkin-complete:hover {{
      border-color: #436f89;
      background: #436f89;
      color: #fff;
    }}
    .manual-checkin-complete:disabled {{
      border-color: transparent;
      background: transparent;
      color: #98a2b3;
      cursor: not-allowed;
      transform: none;
    }}
    .jump-link.is-free {{
      border-color: #abefc6;
      background: #ecfdf3;
      color: #067647;
    }}
    .group-link.is-current {{
      border-color: #5f8ca7;
      background: #dceaf2;
      color: #244d66;
      box-shadow: 0 0 0 2px rgba(95,140,167,.24), inset 0 0 0 1px rgba(255,255,255,.55);
      font-weight: 900;
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
      border-radius: var(--radius-l);
      background: var(--panel);
      box-shadow: var(--shadow-card);
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
    .tool-card-checkin {{
      --tool-accent: #d97706;
      border-color: #f2c46d;
      background: #fffaf0;
      box-shadow: 0 10px 26px rgba(217, 119, 6, 0.14);
    }}
    .tool-card-checkin .tool-kicker {{
      background: #fff0c2;
      color: #9a5b00;
    }}
    .tool-card-checkin .tool-name {{ color: #8a4b00; }}
    .tool-card-checkin .tool-desc {{ color: #7a5a20; }}
    .tool-card-monitor {{ --tool-accent: #d68a00; }}
    .tool-card-prompt {{ --tool-accent: #7a5af8; }}
    .tool-card-quality {{ --tool-accent: #067647; }}
    .tool-card-network {{ --tool-accent: #0e7090; }}
    .tool-card-chief {{
      --tool-accent: #b42318;
      border: 2px solid #f04438;
      background: linear-gradient(135deg, #fff1f0, #ffffff 72%);
      box-shadow: 0 10px 28px rgba(240, 68, 56, 0.18);
    }}
    .tool-card-chief .tool-kicker {{ background: #fecdca; color: #b42318; }}
    .tool-card-chief .tool-name {{ color: #b42318; }}
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
      min-width: 1500px;
      table-layout: fixed;
      border-collapse: collapse;
      font-variant-numeric: tabular-nums;
      background: var(--panel);
    }}
    th, td {{
      padding: 10px 10px;
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
      text-align: right;
    }}
    table th:nth-child(1), table td:nth-child(1) {{ width:3%; }}
    table th:nth-child(2), table td:nth-child(2) {{ width:17%; }}
    table th:nth-child(3), table td:nth-child(3),
    table th:nth-child(4), table td:nth-child(4),
    table th:nth-child(5), table td:nth-child(5),
    table th:nth-child(6), table td:nth-child(6) {{ width:6%; }}
    table th:nth-child(7), table td:nth-child(7) {{ width:11%; }}
    table th:nth-child(8), table td:nth-child(8) {{ width:6%; }}
    table th:nth-child(9), table td:nth-child(9) {{ width:6%; }}
    table th:nth-child(10), table td:nth-child(10) {{ width:8%; }}
    table th:nth-child(11), table td:nth-child(11) {{ width:25%; }}
    td.rank, td.rate, td.plus-rate, td.pro-rate, td.balance-cell, td.signup-cell {{ text-align: right; }}
    th:nth-child(6) {{ text-align: right; }}
    th:nth-child(1), th:nth-child(3), th:nth-child(4), th:nth-child(5), th:nth-child(6), th:nth-child(8) {{ text-align: right; }}
    .site-name {{
      font-weight: 700;
      color: #182230;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .site-url {{
      margin-top: 3px;
      max-width: 360px;
      overflow: hidden;
      text-overflow: ellipsis;
      color: var(--muted);
      font-size: 12px;
    }}
    td.notes {{ overflow:hidden; text-overflow:ellipsis; max-width:0; }}
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
      background: linear-gradient(90deg, rgba(255, 218, 92, 0.68), rgba(255, 249, 225, 0.98) 50%);
      outline: 2px solid rgba(217, 154, 0, 0.52);
      outline-offset: -1px;
    }}
    .is-common-site .site-name {{
      color: #7a4b00;
    }}
    .site-badges {{ display:flex; flex-wrap:wrap; gap:4px; margin-top:4px; }}
    .table-panel .site-name::before,
    .table-panel .site-name::after {{ display:none !important; content:none !important; }}
    .status-badge {{ display:inline-flex; align-items:center; min-height:19px; padding:2px 6px; border:1px solid; border-radius:999px; font-size:11px; font-weight:850; line-height:1; }}
    .badge-common {{ border-color:#e7a23b; background:#fff4d6; color:#8a5200; }}
    .badge-balance {{ border-color:#b2ccff; background:#eaf3ff; color:#175cd3; }}
    .badge-checkin {{ border-color:#fedf89; background:#fff4d7; color:#93370d; }}
    .signed-today .badge-checkin {{ border-color:#abefc6; background:#ecfdf3; color:#067647; }}
    .has-balance {{
      box-shadow: inset 4px 0 0 #175cd3;
    }}
    .balance-cell {{
      font-weight: 800;
      font-variant-numeric: tabular-nums;
    }}
    .has-balance .balance-cell {{
      color: #175cd3;
    }}
    .has-debt {{
      background: linear-gradient(90deg, rgba(254, 205, 202, 0.72), #fff 42%);
      box-shadow: inset 4px 0 0 #d92d20;
    }}
    .has-debt .balance-cell {{ color: #b42318; font-weight: 900; }}
    html[data-theme="dark"] .has-debt {{
      background: linear-gradient(90deg, rgba(127, 29, 29, 0.48), #17232d 42%);
      box-shadow: inset 4px 0 0 #f97066;
    }}
    html[data-theme="dark"] .has-debt .balance-cell {{ color: #fda29b; }}
    .has-checkin {{
      box-shadow: inset 4px 0 0 #d99a00;
    }}
    .has-balance.has-checkin {{
      box-shadow: inset 4px 0 0 #175cd3, inset 8px 0 0 #d99a00;
    }}
    .has-checkin.signed-today {{
      box-shadow: inset 4px 0 0 #12b76a;
    }}
    .has-balance.has-checkin.signed-today {{
      box-shadow: inset 4px 0 0 #175cd3, inset 8px 0 0 #12b76a;
    }}
    .has-checkin.signed-today .site-name::before {{
      content: "";
      display:none;
      border-color: #abefc6;
      background: #ecfdf3;
      color: #067647;
    }}
    .checkin-cell {{
      font-weight: 800;
      color: #93370d;
      max-width: 240px;
      min-width: 142px;
      white-space: normal;
      text-align: left;
    }}
    .checkin-actions {{
      display: grid;
      grid-template-columns: minmax(38px, 1fr) auto;
      align-items: center;
      gap: 7px;
      min-width: 132px;
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
  <link rel="stylesheet" href="/tokens.css">
  <link rel="stylesheet" href="/theme.css">
  <script src="/theme.js"></script>
</head>
<body id="top">
  <header>
    <div class="header-row">
      <div>
        <h1>AI 模型中转站倍率看板</h1>
        <div class="meta">更新时间：{html.escape(checked_at)}</div>
      </div>
      <div class="header-theme"><div class="hint">{hint_text}</div><div data-theme-control></div></div>
    </div>
    <section class="summary" aria-label="summary">
      <div class="metric"><div class="metric-icon">▦</div><div class="metric-label">站点数</div><div class="metric-value">{len(snapshots)}</div></div>
      <div class="metric"><div class="metric-icon">↘</div><div class="metric-label">当前最低</div><div class="metric-value">{best_rate}x</div></div>
      <div class="metric"><div class="metric-icon">★</div><div class="metric-label">最低站点</div><div class="metric-value">{best_name}</div></div>
      <div class="metric"><div class="metric-icon">⏳</div><div class="metric-label">今日待签到</div><div id="checkin-count" class="metric-value">{checkin_count}</div></div>
    </section>
  </header>
  <main>
    <section class="tool-panel" aria-labelledby="tools-title">
      <div class="panel-bar">
        <div>
          <div id="tools-title" class="panel-title">常用工具</div>
          <div class="panel-subtitle">账号池、免费额度、站点参考、提示词与检测工具</div>
        </div>
        <div class="tool-count">{5 + len(quality_sites)} 个工具</div>
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
      <article class="tool-card tool-card-checkin">
        <div class="tool-kicker">每日签到提醒</div>
        <div class="tool-name">GoAIHop</div>
        <div class="tool-desc">每天打开后先完成签到，领取 GoAIHop 积分；同时可查看中转站模型报价、可用率、首字延迟和充值比例。</div>
        <a class="tool-link" href="https://goaihop.com/providers" target="_blank" rel="noopener noreferrer">打开 GoAIHop</a>
      </article>
      <article class="tool-card tool-card-prompt">
        <div class="tool-kicker">提示词</div>
        <div class="tool-name">提示词优化器</div>
        <div class="tool-desc">把普通需求整理成更清晰、可执行的系统提示词或用户提示词，再投入 GPT 或 Codex 使用。</div>
        <a class="tool-link" href="https://prompt.always200.com/#/basic/system" target="_blank" rel="noopener noreferrer">打开提示词优化器</a>
      </article>
      <article class="tool-card tool-card-chief">
        <div class="tool-kicker">首席检测网站</div>
        <div class="tool-name">BazaarLink Probe</div>
        <div class="tool-desc">用于检测和评估 AI 中转站连接质量、模型可用性与服务状态。建议在使用新站点前先进行检测。</div>
        <a class="tool-link" href="https://bazaarlink.ai/probe" target="_blank" rel="noopener noreferrer">打开 BazaarLink Probe</a>
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
        <nav class="quick-jumps" aria-label="快速跳转">
          {balance_filter_button}
          {common_filter_button}
          {auto_checkin_filter_button}
          {auto_checkin_complete_button}
          {manual_checkin_filter_button}
          <div class="manual-open-control" title="按报告顺序打开待签到的手动站页面">
            <input id="manual-checkin-open-count" class="manual-open-count" type="number" min="1" step="1" value="10" aria-label="打开手动签到站点数量">
            <button id="manual-checkin-open" class="jump-link manual-checkin-open" type="button">打开手动站</button>
          </div>
          <div class="manual-open-control" title="按报告顺序标记待签到的手动站">
            <input id="manual-checkin-complete-count" class="manual-open-count" type="number" min="1" step="1" value="10" aria-label="标记手动签到站点数量">
            <button id="manual-checkin-complete" class="jump-link manual-checkin-complete" type="button">标记已签</button>
          </div>
          <a class="jump-link" href="/calculator.html" target="ai_price_monitor_calculator" rel="noopener">成本计算器</a>
          <a class="jump-link group-link" href="#paid-sites" data-group-target="paid-sites">收费站</a>
          <a class="jump-link is-free group-link" href="#free-sites" data-group-target="free-sites">公益站</a>
        </nav>
        <div class="search-help">同时过滤收费站和公益站；默认不搜网址，输入 <code>api.</code>、<code>/keys</code>、<code>.com</code> 时才匹配网址。</div>
        <div class="status-legend" aria-label="状态图标说明">
          <span class="status-legend-title">图标说明</span>
          <span class="status-legend-item">★ 常用</span>
          <span class="status-legend-item">💰 有余额</span>
          <span class="status-legend-item">⏳ 待签到</span>
          <span class="status-legend-item">✓ 已签到</span>
        </div>
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
        const badge = row.querySelector(".badge-checkin");
        if (badge) badge.textContent = signed ? "✓ 已签到" : "⏳ 待签到";
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
      const commonFilter = document.querySelector("#common-filter");
      const commonOnly = commonFilter?.classList.contains("is-active") || false;
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
      let totalCommonRows = 0;
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
        totalCommonRows += rows.filter((row) => row.dataset.usageStatus === "常用").length;
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
          const isCommon = row.dataset.usageStatus === "常用";
          const hasPendingCheckin = row.classList.contains("has-checkin") && !row.classList.contains("signed-today");
          const matchesCheckinMode = !checkinMode || row.dataset.checkinMode === checkinMode;
          const matched =
            (!query || searchableText.includes(query) || urlText.includes(query)) &&
            (!balanceOnly || hasBalance) &&
            (!commonOnly || isCommon) &&
            (!checkinMode || (hasPendingCheckin && matchesCheckinMode));
          row.hidden = !matched;
          row.style.display = matched ? "" : "none";
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
        balanceFilter.textContent = balanceOnly ? `当前：有余额 (${{totalBalanceRows}})` : `只看有余额 (${{totalBalanceRows}})`;
      }}
      if (commonFilter) {{
        commonFilter.textContent = commonOnly ? `当前：常用站 (${{totalCommonRows}})` : `只看常用站 (${{totalCommonRows}})`;
      }}
      if (autoCheckinFilter) {{
        autoCheckinFilter.textContent = `${{autoCheckinFilter.classList.contains("is-active") ? "当前：" : ""}}自动签到 (${{totalAutoPendingCheckinRows}})`;
      }}
      const autoCheckinComplete = document.querySelector("#auto-checkin-complete");
      if (autoCheckinComplete) {{
        autoCheckinComplete.textContent = `自动全部已签 (${{totalAutoPendingCheckinRows}})`;
        autoCheckinComplete.disabled = totalAutoPendingCheckinRows === 0;
      }}
      if (manualCheckinFilter) {{
        manualCheckinFilter.textContent = `${{manualCheckinFilter.classList.contains("is-active") ? "当前：" : ""}}手动签到 (${{totalManualPendingCheckinRows}})`;
      }}
      const manualCheckinOpen = document.querySelector("#manual-checkin-open");
      if (manualCheckinOpen) {{
        manualCheckinOpen.textContent = `打开手动站 (${{totalManualPendingCheckinRows}})`;
        manualCheckinOpen.disabled = totalManualPendingCheckinRows === 0;
      }}
      const manualCheckinComplete = document.querySelector("#manual-checkin-complete");
      if (manualCheckinComplete) {{
        manualCheckinComplete.textContent = `标记已签 (${{totalManualPendingCheckinRows}})`;
        manualCheckinComplete.disabled = totalManualPendingCheckinRows === 0;
      }}
    }}

    document.querySelector("#balance-filter")?.addEventListener("click", (event) => {{
      const button = event.currentTarget;
      button.classList.toggle("is-active");
      button.setAttribute("aria-pressed", button.classList.contains("is-active") ? "true" : "false");
      applyReportSearch();
    }});
    document.querySelector("#common-filter")?.addEventListener("click", (event) => {{
      event.currentTarget.classList.toggle("is-active");
      applyReportSearch();
    }});

    function updateGroupHighlight() {{
      const sections = Array.from(document.querySelectorAll(".table-panel[id]"));
      const links = document.querySelectorAll(".group-link[data-group-target]");
      if (!sections.length || !links.length) return;
      const marker = window.scrollY + 150;
      let current = sections[0].id;
      sections.forEach((section) => {{
        if (section.offsetTop <= marker) current = section.id;
      }});
      links.forEach((link) => link.classList.toggle("is-current", link.dataset.groupTarget === current));
    }}
    document.querySelectorAll(".group-link[data-group-target]").forEach((link) => link.addEventListener("click", () => {{
      document.querySelectorAll(".group-link[data-group-target]").forEach((item) => item.classList.remove("is-current"));
      link.classList.add("is-current");
    }}));
    window.addEventListener("scroll", updateGroupHighlight, {{passive:true}});
    window.addEventListener("hashchange", updateGroupHighlight);
    updateGroupHighlight();

    function toggleCheckinFilter(mode) {{
      const autoCheckinFilter = document.querySelector("#auto-checkin-filter");
      const manualCheckinFilter = document.querySelector("#manual-checkin-filter");
      const target = mode === "自动" ? autoCheckinFilter : manualCheckinFilter;
      const wasActive = target?.classList.contains("is-active");
      autoCheckinFilter?.classList.remove("is-active");
      manualCheckinFilter?.classList.remove("is-active");
      if (!wasActive) target?.classList.add("is-active");
      autoCheckinFilter?.setAttribute("aria-pressed", (!wasActive && mode === "自动") ? "true" : "false");
      manualCheckinFilter?.setAttribute("aria-pressed", (!wasActive && mode === "手动") ? "true" : "false");
      applyReportSearch();
    }}

    document.querySelector("#auto-checkin-filter")?.addEventListener("click", () => toggleCheckinFilter("自动"));
    document.querySelector("#manual-checkin-filter")?.addEventListener("click", () => toggleCheckinFilter("手动"));

    document.querySelector("#manual-checkin-open")?.addEventListener("click", async (event) => {{
      const button = event.currentTarget;
      const countInput = document.querySelector("#manual-checkin-open-count");
      const requested = Math.floor(Number(countInput?.value));
      if (!Number.isFinite(requested) || requested < 1) {{
        countInput?.focus();
        return;
      }}
      const links = Array.from(document.querySelectorAll('tr.has-checkin[data-checkin-mode="手动"]'))
        .filter((row) => !row.classList.contains("signed-today"))
        .map((row) => row.querySelector("a.open-link")?.href)
        .filter(Boolean)
        .slice(0, requested);
      if (!links.length) return;
      button.disabled = true;
      button.textContent = "正在打开...";
      try {{
        const response = await fetch("/api/open-site-pages", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{urls: links}}),
        }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "打开站点失败");
        button.textContent = `已打开 ${{payload.opened}} 个页面`;
      }} catch (error) {{
        button.textContent = error.message || "打开站点失败";
      }} finally {{
        window.setTimeout(applyReportSearch, 1600);
      }}
    }});

    document.querySelector("#manual-checkin-complete")?.addEventListener("click", (event) => {{
      const button = event.currentTarget;
      const countInput = document.querySelector("#manual-checkin-complete-count");
      const requested = Math.floor(Number(countInput?.value));
      if (!Number.isFinite(requested) || requested < 1) {{
        countInput?.focus();
        return;
      }}
      const checkinButtons = Array.from(document.querySelectorAll('tr.has-checkin[data-checkin-mode="手动"] .checkin-toggle'))
        .filter((checkinButton) => !checkinButton.closest("tr")?.classList.contains("signed-today"))
        .slice(0, requested);
      if (!checkinButtons.length) return;
      checkinButtons.forEach((checkinButton) => {{
        window.localStorage.setItem(checkinStorageKey(checkinButton), "1");
      }});
      applyCheckinState();
      button.textContent = `已标记 ${{checkinButtons.length}} 个`;
      window.setTimeout(applyReportSearch, 1200);
    }});

    document.querySelector("#auto-checkin-complete")?.addEventListener("click", () => {{
      document.querySelectorAll('tr.has-checkin[data-checkin-mode="自动"] .checkin-toggle').forEach((button) => {{
        if (window.localStorage.getItem(checkinStorageKey(button)) !== "1") {{
          window.localStorage.setItem(checkinStorageKey(button), "1");
        }}
      }});
      applyCheckinState();
      applyReportSearch();
    }});

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
    const reportViewStateKey = "ai-price-monitor:report-view-state";

    function saveReportViewState() {{
      const balanceFilter = document.querySelector("#balance-filter");
      const commonFilter = document.querySelector("#common-filter");
      const autoCheckinFilter = document.querySelector("#auto-checkin-filter");
      const manualCheckinFilter = document.querySelector("#manual-checkin-filter");
      const state = {{
        scrollX: window.scrollX,
        scrollY: window.scrollY,
        search: document.querySelector("#report-search")?.value || "",
        balanceOnly: balanceFilter?.classList.contains("is-active") || false,
        commonOnly: commonFilter?.classList.contains("is-active") || false,
        checkinMode: autoCheckinFilter?.classList.contains("is-active") ? "自动" : manualCheckinFilter?.classList.contains("is-active") ? "手动" : "",
        openCount: document.querySelector("#manual-checkin-open-count")?.value || "10",
        completeCount: document.querySelector("#manual-checkin-complete-count")?.value || "10",
      }};
      window.sessionStorage.setItem(reportViewStateKey, JSON.stringify(state));
    }}

    function restoreReportViewState() {{
      let state;
      try {{ state = JSON.parse(window.sessionStorage.getItem(reportViewStateKey) || "null"); }} catch {{ state = null; }}
      if (!state) return;
      const search = document.querySelector("#report-search");
      if (search) search.value = state.search || "";
      const openCount = document.querySelector("#manual-checkin-open-count");
      const completeCount = document.querySelector("#manual-checkin-complete-count");
      if (openCount && state.openCount) openCount.value = state.openCount;
      if (completeCount && state.completeCount) completeCount.value = state.completeCount;
      const balanceFilter = document.querySelector("#balance-filter");
      if (balanceFilter && state.balanceOnly) balanceFilter.classList.add("is-active");
      const commonFilter = document.querySelector("#common-filter");
      if (commonFilter && state.commonOnly) commonFilter.classList.add("is-active");
      if (state.checkinMode) toggleCheckinFilter(state.checkinMode);
      const searchField = search?.closest(".search-field");
      searchField?.classList.toggle("has-value", Boolean(search?.value));
      applyCheckinState();
      applyReportSearch();
      window.requestAnimationFrame(() => window.scrollTo(state.scrollX || 0, state.scrollY || 0));
      window.sessionStorage.removeItem(reportViewStateKey);
    }}

    window.addEventListener("storage", (event) => {{
      if (event.key === "ai-price-monitor:report-version") {{
        saveReportViewState();
        window.location.reload();
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
    restoreReportViewState();
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













