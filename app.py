from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from monitor import DB_FILE, build_reports, load_sites, normalize_optional_http_url, sort_snapshots, snapshot_from_site, sync_sites_to_db, write_sites


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765


def parse_number(value: Any) -> float | None:
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


def normalize_site(site: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(site.get("name", "")).strip(),
        "url": str(site.get("url", "")).strip(),
        "category": str(site.get("category", "收费站")).strip() or "收费站",
        "usage_status": "常用" if site.get("usage_status") == "常用" else "",
        "balance": parse_number(site.get("balance")),
        "welfare_rate": parse_number(site.get("welfare_rate")),
        "plus_rate": parse_number(site.get("plus_rate")),
        "pro_rate": parse_number(site.get("pro_rate")),
        "signup_bonus": parse_number(site.get("signup_bonus")),
        "daily_checkin_bonus": empty_to_none(site.get("daily_checkin_bonus")),
        "notes": str(site.get("notes", "")).strip(),
        "invite_url": normalize_optional_http_url(site.get("invite_url")),
    }


def validate_unique_site_names(sites: list[dict[str, Any]]) -> None:
    names_by_key: dict[str, str] = {}
    duplicates: list[str] = []
    for site in sites:
        name = site["name"]
        key = name.casefold()
        if key in names_by_key and names_by_key[key] not in duplicates:
            duplicates.append(names_by_key[key])
        else:
            names_by_key[key] = name
    if duplicates:
        raise ValueError(f"站点名称重复：{'、'.join(duplicates)}")


def current_rows() -> list[dict[str, Any]]:
    sites = load_sites()
    snapshots = [snapshot_from_site(site) for site in sites]
    by_name = {site["name"]: site for site in sites}
    rows = []
    for snapshot in sort_snapshots(snapshots):
        site = by_name[snapshot.name]
        rows.append(
            {
                **site,
                "invite_url": normalize_optional_http_url(site.get("invite_url")),
                "lowest_rate": snapshot.lowest_rate,
            }
        )
    return rows


def save_rows(rows: list[dict[str, Any]]) -> None:
    sites = [normalize_site(row) for row in rows if str(row.get("name", "")).strip()]
    validate_unique_site_names(sites)
    write_sites(sites)
    DB_FILE.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        sync_sites_to_db(conn, sites)
    build_reports(sites)


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, body: str) -> None:
    data = body.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def file_response(handler: BaseHTTPRequestHandler, path: Path, content_type: str) -> None:
    if not path.exists() or not path.is_file():
        json_response(handler, {"error": "not found"}, status=404)
        return
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            html_response(self, EDITOR_HTML)
            return
        if path == "/api/sites":
            json_response(self, {"sites": current_rows()})
            return
        if path == "/theme.css":
            file_response(self, ROOT / "theme.css", "text/css; charset=utf-8")
            return
        if path == "/theme.js":
            file_response(self, ROOT / "theme.js", "application/javascript; charset=utf-8")
            return
        if path == "/calculator.html":
            file_response(self, ROOT / "calculator.html", "text/html; charset=utf-8")
            return
        if path == "/reports/latest.html":
            file_response(self, ROOT / "reports" / "latest.html", "text/html; charset=utf-8")
            return
        if path == "/reports/latest.csv":
            file_response(self, ROOT / "reports" / "latest.csv", "text/csv; charset=utf-8")
            return
        json_response(self, {"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            json_response(self, {"error": "invalid json"}, status=400)
            return
        if path == "/api/sites":
            rows = payload.get("sites")
            if not isinstance(rows, list):
                json_response(self, {"error": "sites must be a list"}, status=400)
                return
            try:
                save_rows(rows)
            except ValueError as error:
                json_response(self, {"error": str(error)}, status=400)
                return
            except sqlite3.Error:
                json_response(self, {"error": "数据库保存失败，请重试。"}, status=500)
                return
            json_response(self, {"ok": True, "count": len(rows)})
            return
        json_response(self, {"error": "not found"}, status=404)


EDITOR_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GPT 中转站编辑器</title>
  <style>
    :root {
      --bg: #eef3f7;
      --panel: #ffffff;
      --text: #182230;
      --muted: #667085;
      --line: #d9dee7;
      --soft-line: #edf0f5;
      --accent: #0f9f8f;
      --accent-dark: #087f73;
      --accent-soft: #e3f7f4;
      --blue: #175cd3;
      --blue-soft: #eaf3ff;
      --amber-soft: #fff4d7;
      --danger: #b42318;
      --danger-soft: #fff1f0;
      --shadow: 0 18px 44px rgba(16, 24, 40, 0.10);
    }
    * { box-sizing: border-box; }
    html,
    body {
      height: 100%;
    }
    body {
      margin: 0;
      min-width: 1200px;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background:
        linear-gradient(180deg, #fbfcfe 0, var(--bg) 280px),
        var(--bg);
      color: var(--text);
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      font-size: 14px;
    }
    header {
      position: relative;
      flex: 0 0 auto;
      z-index: 20;
      padding: 20px 28px 16px;
      border-bottom: 1px solid rgba(217, 222, 231, 0.86);
      background:
        radial-gradient(circle at 18% 0%, rgba(15, 159, 143, 0.08), transparent 34%),
        radial-gradient(circle at 82% 0%, rgba(23, 92, 211, 0.08), transparent 30%),
        rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(12px);
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 28px;
      line-height: 1.2;
      font-weight: 820;
      letter-spacing: 0;
    }
    .muted { color: var(--muted); line-height: 1.6; }
    .actions {
      display: flex;
      align-items: center;
      gap: 10px;
      white-space: nowrap;
    }
    button, a.button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 34px;
      padding: 0 13px;
      border: 1px solid #cdd6e1;
      border-radius: 8px;
      background: #fff;
      color: #344054;
      font: inherit;
      line-height: 1;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
      transition: 120ms ease;
    }
    button:hover, a.button:hover {
      border-color: #9fb0c3;
      background: #f8fafc;
      transform: translateY(-1px);
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    button.primary:hover {
      border-color: var(--accent-dark);
      background: var(--accent-dark);
    }
    button.checkin-filter {
      border-color: #f4b740;
      background: #fffaf0;
      color: #8a5200;
    }
    button.checkin-filter:hover,
    button.checkin-filter.is-active {
      border-color: #d99a16;
      background: var(--amber-soft);
      color: #754600;
    }
    button.danger {
      border-color: #fecdca;
      color: var(--danger);
      background: var(--danger-soft);
    }
    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: center;
      margin-top: 16px;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(5, minmax(128px, 1fr));
      gap: 10px;
    }
    .metric {
      padding: 13px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      box-shadow: 0 10px 26px rgba(16, 24, 40, 0.06);
    }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 6px;
    }
    .metric-value {
      font-size: 21px;
      font-weight: 800;
      line-height: 1.1;
      font-variant-numeric: tabular-nums;
    }
    .searchbox {
      width: 320px;
      height: 38px;
      padding: 0 12px;
      border: 1px solid #cdd6e1;
      border-radius: 8px;
      background: #fff;
      color: var(--text);
      font: inherit;
      outline: none;
      transition: 120ms ease;
    }
    .search-wrap {
      position: relative;
      width: 320px;
    }
    .search-wrap .searchbox {
      width: 100%;
      padding-right: 38px;
    }
    .search-clear {
      position: absolute;
      top: 50%;
      right: 6px;
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
      font-size: 20px;
      line-height: 1;
      transform: translateY(-50%);
    }
    .search-clear:hover {
      background: #e7edf4;
      color: #182230;
      transform: translateY(-50%);
    }
    .search-wrap.has-value .search-clear { display: inline-flex; }
    .searchbox:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 159, 143, 0.12);
    }
    main {
      display: flex;
      flex: 1 1 auto;
      min-height: 0;
      padding: 20px 28px 34px;
    }
    .panel {
      flex: 1 1 auto;
      min-width: 0;
      min-height: 0;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
    }
    .table-wrap {
      height: 100%;
      min-height: 0;
      overflow: auto;
      overscroll-behavior: contain;
      scrollbar-gutter: stable;
      touch-action: pan-x pan-y;
      -webkit-overflow-scrolling: touch;
    }
    table {
      width: 100%;
      border-collapse: collapse;
    }
    th, td {
      border-bottom: 1px solid var(--soft-line);
      padding: 9px 8px;
      vertical-align: middle;
      text-align: left;
    }
    th {
      position: sticky;
      top: 0;
      z-index: 5;
      background: #f3f6fa;
      color: #344054;
      font-size: 13px;
      height: 40px;
      box-shadow: inset 0 -1px 0 var(--line);
    }
    tbody tr {
      background: #fff;
      transition: background 120ms ease, box-shadow 120ms ease;
    }
    tbody tr:hover {
      background: #f7fafc;
      outline: 1px solid #d7e1ec;
      outline-offset: -1px;
    }
    tbody tr.tier-low {
      background: linear-gradient(90deg, rgba(227, 247, 244, 0.95), #fff 30%);
    }
    tbody tr.tier-mid {
      background: linear-gradient(90deg, rgba(255, 244, 215, 0.8), #fff 30%);
    }
    tbody tr.has-balance {
      box-shadow: inset 4px 0 0 #175cd3;
    }
    tbody tr.has-balance:hover {
      background: linear-gradient(90deg, rgba(234, 243, 255, 0.78), #f7fafc 30%);
      box-shadow: inset 4px 0 0 #175cd3;
    }
    tbody tr.has-balance .site-input {
      color: #175cd3;
      background: #f6f9ff;
    }
    tbody tr.has-balance input[data-field="balance"] {
      border-color: #b2ccff;
      background: #eaf3ff;
      color: #175cd3;
      font-weight: 800;
    }
    tbody tr.has-checkin {
      box-shadow: inset 4px 0 0 #d99a00;
    }
    tbody tr.has-balance.has-checkin {
      box-shadow: inset 4px 0 0 #175cd3, inset 8px 0 0 #d99a00;
    }
    tbody tr.has-checkin.signed-today {
      box-shadow: inset 4px 0 0 #12b76a;
    }
    tbody tr.has-balance.has-checkin.signed-today {
      box-shadow: inset 4px 0 0 #175cd3, inset 8px 0 0 #12b76a;
    }
    tbody tr.has-checkin input[data-field="daily_checkin_bonus"] {
      border-color: #fedf89;
      background: #fff4d7;
      color: #93370d;
      font-weight: 800;
    }
    tbody tr.signed-today input[data-field="daily_checkin_bonus"] {
      border-color: #abefc6;
      background: #ecfdf3;
      color: #067647;
    }
    input,
    select {
      width: 100%;
      height: 32px;
      padding: 0 8px;
      border: 1px solid #d5dce7;
      border-radius: 6px;
      background: #fff;
      font: inherit;
      color: var(--text);
      outline: none;
      transition: 120ms ease;
    }
    input:focus,
    select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 159, 143, 0.12);
    }
    input.number { min-width: 76px; }
    select.category-input { min-width: 86px; }
    select.usage-input { min-width: 72px; font-weight: 700; }
    tbody tr.is-common-site { background: linear-gradient(90deg, rgba(255, 230, 143, 0.42), rgba(255, 255, 255, 0.94) 45%); outline: 1px solid rgba(217, 154, 0, 0.24); outline-offset: -1px; }
    tbody tr.is-common-site .site-input { color: #7a4b00; font-weight: 800; }
    tbody tr.is-common-site select.usage-input { border-color: #fdb022; background: #fffaeb; color: #93370d; }
    input.url { min-width: 260px; }
    input.notes { min-width: 280px; }
    .rank {
      width: 46px;
      color: var(--muted);
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }
    .readonly {
      color: var(--accent-dark);
      font-weight: 850;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
      padding: 0 10px;
    }
    .site-input {
      min-width: 120px;
      font-weight: 750;
    }
    .status {
      min-width: 150px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--muted);
      font-weight: 700;
      text-align: center;
      box-shadow: 0 6px 16px rgba(16, 24, 40, 0.04);
    }
    .status.ok {
      border-color: #b8ddd8;
      background: var(--accent-soft);
      color: var(--accent-dark);
    }
    .status.error {
      border-color: #fecdca;
      background: var(--danger-soft);
      color: var(--danger);
    }
    .status.dirty {
      border-color: #fedf89;
      background: var(--amber-soft);
      color: #93370d;
    }
    @media (max-width: 1280px) {
      .workspace {
        grid-template-columns: 1fr;
      }
      .searchbox {
        width: 100%;
      }
      .search-wrap { width: 100%; }
    }
  </style>
  <link rel="stylesheet" href="/theme.css">
  <script src="/theme.js"></script>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>GPT 中转站编辑器</h1>
        <div class="muted">保存后同步更新 sites.json、SQLite、HTML 报告和 CSV。</div>
      </div>
      <div class="actions">
        <div data-theme-control></div>
        <span id="status" class="status">加载中...</span>
        <a id="open-report" class="button" href="/reports/latest.html" target="ai_price_monitor_report" rel="noopener">打开报告</a>
        <a id="open-calculator" class="button" href="/calculator.html" target="ai_price_monitor_calculator" rel="noopener">成本计算器</a>
        <button id="checkin-filter" class="checkin-filter" type="button">只看签到站</button>
        <button id="add">新增一行</button>
        <button id="save" class="primary">保存并同步</button>
      </div>
    </div>
    <div class="workspace">
      <div class="metrics">
        <div class="metric"><div class="metric-label">站点数</div><div id="metric-count" class="metric-value">-</div></div>
        <div class="metric"><div class="metric-label">最低倍率</div><div id="metric-best" class="metric-value">-</div></div>
        <div class="metric"><div class="metric-label">有余额站点</div><div id="metric-balance" class="metric-value">-</div></div>
        <div class="metric"><div class="metric-label">余额合计</div><div id="metric-total" class="metric-value">-</div></div>
        <div class="metric"><div class="metric-label">今日待签到</div><div id="metric-checkin" class="metric-value">-</div></div>
      </div>
      <div id="search-wrap" class="search-wrap">
        <input id="search" class="searchbox" placeholder="搜索站点、网址、备注">
        <button id="clear-search" class="search-clear" type="button" aria-label="清空搜索" title="清空搜索">×</button>
      </div>
    </div>
  </header>
  <main>
    <div class="panel">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>站点</th>
              <th>分类</th>
              <th>使用</th>
              <th>最低</th>
              <th>福利/特价</th>
              <th>Plus</th>
              <th>Pro</th>
              <th>注册送</th>
              <th>签到送</th>
              <th>余额</th>
              <th>页面</th>
              <th>邀请链接</th>
              <th>备注</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </div>
  </main>
  <script>
    const fields = ["name", "category", "usage_status", "welfare_rate", "plus_rate", "pro_rate", "signup_bonus", "daily_checkin_bonus", "balance", "url", "invite_url", "notes"];
    const numeric = new Set(["welfare_rate", "plus_rate", "pro_rate", "signup_bonus", "balance"]);
    const tbody = document.querySelector("#tbody");
    const tableWrap = document.querySelector(".table-wrap");
    const statusEl = document.querySelector("#status");
    const searchEl = document.querySelector("#search");
    const searchWrap = document.querySelector("#search-wrap");
    const clearSearch = document.querySelector("#clear-search");
    const metricCount = document.querySelector("#metric-count");
    const metricBest = document.querySelector("#metric-best");
    const metricBalance = document.querySelector("#metric-balance");
    const metricTotal = document.querySelector("#metric-total");
    const metricCheckin = document.querySelector("#metric-checkin");
    const checkinFilter = document.querySelector("#checkin-filter");
    let rows = [];
    let query = "";
    let checkinOnly = false;

    function setStatus(text, cls = "") {
      statusEl.textContent = text;
      statusEl.className = `status ${cls}`;
    }

    function fmt(value) {
      return value === null || value === undefined || value === "" ? "" : String(value);
    }

    function numberValue(value) {
      if (value === "") return null;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }

    function lowest(row) {
      const values = [row.welfare_rate, row.plus_rate, row.pro_rate].filter((value) => value !== null && value !== undefined && value !== "");
      if (!values.length) return "";
      return Math.min(...values.map(Number));
    }

    function sortKeyValue(value) {
      return value === null || value === undefined || value === "" ? 9999 : Number(value);
    }

    function categorySortValue(row) {
      return fmt(row.category) === "公益站" ? 1 : 0;
    }

    function sortRows() {
      rows.sort((a, b) => {
        const keysA = [categorySortValue(a), sortKeyValue(lowest(a)), sortKeyValue(a.plus_rate), sortKeyValue(a.pro_rate), fmt(a.name)];
        const keysB = [categorySortValue(b), sortKeyValue(lowest(b)), sortKeyValue(b.plus_rate), sortKeyValue(b.pro_rate), fmt(b.name)];
        for (let i = 0; i < keysA.length; i += 1) {
          if (keysA[i] < keysB[i]) return -1;
          if (keysA[i] > keysB[i]) return 1;
        }
        return 0;
      });
    }

    function rowMatches(row) {
      if (checkinOnly && !hasCheckin(row)) return false;
      if (!query) return true;
      const haystack = [row.name, row.category, row.usage_status, row.url, row.invite_url, row.notes].map(fmt).join(" ").toLowerCase();
      return haystack.includes(query);
    }

    function todayString() {
      const now = new Date();
      const year = now.getFullYear();
      const month = String(now.getMonth() + 1).padStart(2, "0");
      const day = String(now.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    }

    function checkinStorageKey(row) {
      return `ai-price-monitor:checkin:${todayString()}:${fmt(row.url) || fmt(row.name)}`;
    }

    function hasCheckin(row) {
      return fmt(row.daily_checkin_bonus) !== "";
    }

    function isSignedToday(row) {
      return hasCheckin(row) && window.localStorage.getItem(checkinStorageKey(row)) === "1";
    }

    function updateMetrics() {
      const rates = rows.map(lowest).filter((value) => value !== "").map(Number);
      const best = rates.length ? Math.min(...rates) : "";
      const balanceRows = rows.filter((row) => Number(row.balance || 0) > 0);
      const checkinStations = rows.filter(hasCheckin);
      const checkinRows = checkinStations.filter((row) => !isSignedToday(row));
      const totalBalance = rows.reduce((sum, row) => sum + Number(row.balance || 0), 0);
      metricCount.textContent = String(rows.length);
      metricBest.textContent = best === "" ? "-" : `${best}x`;
      metricBalance.textContent = String(balanceRows.length);
      metricTotal.textContent = Number.isInteger(totalBalance) ? String(totalBalance) : totalBalance.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
      metricCheckin.textContent = String(checkinRows.length);
      checkinFilter.classList.toggle("is-active", checkinOnly);
      checkinFilter.textContent = checkinOnly ? `显示全部 (${rows.length})` : `只看签到站 (${checkinStations.length})`;
    }

    function rowClass(row) {
      const rate = lowest(row);
      const classes = [];
      if (rate !== "" && Number(rate) <= 0.03) classes.push("tier-low");
      else if (rate !== "" && Number(rate) <= 0.1) classes.push("tier-mid");
      if (Number(row.balance || 0) > 0) classes.push("has-balance");
      if (fmt(row.usage_status) === "常用") classes.push("is-common-site");
      if (hasCheckin(row)) classes.push("has-checkin");
      if (isSignedToday(row)) classes.push("signed-today");
      return classes.join(" ");
    }

    function render() {
      tbody.innerHTML = "";
      sortRows();
      updateMetrics();
      rows.forEach((row, index) => {
        if (!rowMatches(row)) return;
        const tr = document.createElement("tr");
        tr.className = rowClass(row);
        tr.dataset.index = String(index);
        tr.innerHTML = `
          <td class="rank">${index + 1}</td>
          <td><input class="site-input" data-field="name" value="${escapeHtml(fmt(row.name))}"></td>
          <td>
            <select class="category-input" data-field="category">
              <option value="收费站"${fmt(row.category || "收费站") === "收费站" ? " selected" : ""}>收费站</option>
              <option value="公益站"${fmt(row.category || "收费站") === "公益站" ? " selected" : ""}>公益站</option>
            </select>
          </td>
          <td>
            <select class="usage-input" data-field="usage_status">
              <option value=""${fmt(row.usage_status) === "" ? " selected" : ""}>-</option>
              <option value="常用"${fmt(row.usage_status) === "常用" ? " selected" : ""}>常用</option>
            </select>
          </td>
          <td class="readonly">${fmt(lowest(row))}${lowest(row) === "" ? "" : "x"}</td>
          <td><input class="number" data-field="welfare_rate" value="${escapeHtml(fmt(row.welfare_rate))}"></td>
          <td><input class="number" data-field="plus_rate" value="${escapeHtml(fmt(row.plus_rate))}"></td>
          <td><input class="number" data-field="pro_rate" value="${escapeHtml(fmt(row.pro_rate))}"></td>
          <td><input class="number" data-field="signup_bonus" value="${escapeHtml(fmt(row.signup_bonus))}"></td>
          <td><input class="checkin-input" data-field="daily_checkin_bonus" value="${escapeHtml(fmt(row.daily_checkin_bonus))}"></td>
          <td><input class="number" data-field="balance" value="${escapeHtml(fmt(row.balance))}"></td>
          <td><input class="url" data-field="url" value="${escapeHtml(fmt(row.url))}"></td>
          <td><input class="url" data-field="invite_url" value="${escapeHtml(fmt(row.invite_url))}"></td>
          <td><input class="notes" data-field="notes" value="${escapeHtml(fmt(row.notes))}"></td>
          <td><button class="danger" data-delete="${index}">删除</button></td>
        `;
        tbody.appendChild(tr);
      });
    }

    function scrollToRow(row) {
      window.requestAnimationFrame(() => {
        const index = rows.indexOf(row);
        const tr = tbody.querySelector(`tr[data-index="${index}"]`);
        if (!tr) return;
        const wrapRect = tableWrap.getBoundingClientRect();
        const rowRect = tr.getBoundingClientRect();
        const nextTop = tableWrap.scrollTop + rowRect.top - wrapRect.top - (tableWrap.clientHeight / 2) + (tr.offsetHeight / 2);
        tableWrap.scrollTo({top: Math.max(0, nextTop), behavior: "smooth"});
        const nameInput = tr.querySelector('input[data-field="name"]');
        if (nameInput) nameInput.focus();
      });
    }

    function redirectPageWheel(event) {
      if (!tableWrap || event.defaultPrevented || event.ctrlKey) return;
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      const maxTop = tableWrap.scrollHeight - tableWrap.clientHeight;
      if (maxTop <= 0) return;
      const startTop = tableWrap.scrollTop;
      const nextTop = Math.max(0, Math.min(maxTop, startTop + event.deltaY));
      if (nextTop === startTop) return;

      if (!event.target.closest(".table-wrap")) {
        event.preventDefault();
        tableWrap.scrollTop = nextTop;
        return;
      }

      window.requestAnimationFrame(() => {
        if (Math.abs(tableWrap.scrollTop - startTop) < 1) {
          tableWrap.scrollTop = nextTop;
        }
      });
    }

    document.addEventListener("wheel", redirectPageWheel, {passive: false});

    function escapeHtml(text) {
      return String(text)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function updateRowFromField(event) {
      const input = event.target.closest("input[data-field], select[data-field]");
      if (!input) return;
      const tr = input.closest("tr");
      const index = Number(tr.dataset.index);
      const field = input.dataset.field;
      rows[index][field] = numeric.has(field) ? numberValue(input.value) : input.value;
      tr.querySelector(".readonly").textContent = fmt(lowest(rows[index])) + (lowest(rows[index]) === "" ? "" : "x");
      tr.className = rowClass(rows[index]);
      updateMetrics();
      setStatus("有未保存修改", "dirty");
    }

    tbody.addEventListener("input", updateRowFromField);
    tbody.addEventListener("change", updateRowFromField);

    tbody.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-delete]");
      if (!button) return;
      const index = Number(button.dataset.delete);
      const siteName = fmt(rows[index]?.name) || "该站点";
      if (!window.confirm(`确定删除“${siteName}”吗？删除后需要点击“保存并同步”才会正式生效。`)) return;
      rows.splice(index, 1);
      render();
      setStatus("有未保存修改", "dirty");
    });

    document.querySelector("#add").addEventListener("click", () => {
      const row = {name: "", category: "收费站", usage_status: "", url: "", balance: null, welfare_rate: null, plus_rate: null, pro_rate: null, signup_bonus: null, daily_checkin_bonus: null, notes: "", invite_url: ""};
      rows.push(row);
      query = "";
      checkinOnly = false;
      searchEl.value = "";
      render();
      scrollToRow(row);
      setStatus("有未保存修改", "dirty");
    });

    function applySearch() {
      query = searchEl.value.trim().toLowerCase();
      searchWrap.classList.toggle("has-value", Boolean(searchEl.value));
      render();
    }

    searchEl.addEventListener("input", applySearch);
    clearSearch.addEventListener("click", () => {
      searchEl.value = "";
      applySearch();
      searchEl.focus();
    });

    checkinFilter.addEventListener("click", () => {
      checkinOnly = !checkinOnly;
      render();
    });

    window.addEventListener("storage", (event) => {
      if (event.key && event.key.startsWith("ai-price-monitor:checkin:")) {
        render();
      }
    });

    document.querySelector("#open-report").addEventListener("click", (event) => {
      event.preventDefault();
      const reportWindow = window.open("/reports/latest.html", "ai_price_monitor_report");
      if (reportWindow) reportWindow.focus();
    });

    document.querySelector("#open-calculator").addEventListener("click", (event) => {
      event.preventDefault();
      const calculatorWindow = window.open("/calculator.html", "ai_price_monitor_calculator");
      if (calculatorWindow) calculatorWindow.focus();
    });

    document.querySelector("#save").addEventListener("click", async () => {
      setStatus("保存中...");
      try {
        const response = await fetch("/api/sites", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({sites: rows}),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "保存失败");
        setStatus(`已保存 ${payload.count} 个站点`, "ok");
        await load();
      } catch (error) {
        setStatus(error.message, "error");
      }
    });

    async function load() {
      const response = await fetch("/api/sites");
      const payload = await response.json();
      rows = payload.sites;
      render();
      setStatus(`已加载 ${rows.length} 个站点`, "ok");
    }

    load();
  </script>
</body>
</html>
"""


def main() -> int:
    url = f"http://{HOST}:{PORT}/"
    build_reports(load_sites())
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Editor: {url}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

