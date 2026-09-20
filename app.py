from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from monitor import DB_FILE, build_reports, load_sites, normalize_optional_http_url, sort_snapshots, snapshot_from_site, sync_sites_to_db, write_sites


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765
METAPI_ENV_FILE = Path(os.environ["METAPI_ENV_FILE"]) if os.environ.get("METAPI_ENV_FILE") else None
METAPI_ACCOUNTS_URL = os.environ.get("METAPI_ACCOUNTS_URL", "http://127.0.0.1:4000/api/accounts")
METAPI_SITE_ALIASES = {
    "bearlabai": "bearlab",
    "七倍算力公益站": "七倍算力",
}
METAPI_GENERIC_SITE_LABELS = (
    "api",
    "中转站",
    "公益站",
    "开放平台",
    "模型平台",
    "模型",
    "平台",
    "网站",
    "站",
)


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


def normalized_checkin_mode(site: dict[str, Any], daily_checkin_bonus: Any) -> str:
    if daily_checkin_bonus is None:
        return "无签到"
    return "手动"


RECHARGE_RATIO_PATTERN = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)$")
RECHARGE_RATIO_IN_NOTES_PATTERN = re.compile(
    r"充值(?:比)?\s*([0-9]+(?:\.[0-9]+)?)\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)"
)


def format_ratio_value(value: float) -> str:
    return f"{value:.12f}".rstrip("0").rstrip(".")


def canonical_recharge_ratio(value: Any) -> str:
    match = RECHARGE_RATIO_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError("充值倍率请填写为 数字:数字，例如 1:50。")
    left, right = float(match.group(1)), float(match.group(2))
    if left <= 0 or right <= 0:
        raise ValueError("充值倍率两侧数字都必须大于 0。")
    return f"{format_ratio_value(left)}:{format_ratio_value(right)}"


def recharge_ratio_for_site(site: dict[str, Any]) -> str:
    if str(site.get("category", "收费站")).strip() != "收费站":
        return ""
    value = str(site.get("recharge_ratio") or "").strip()
    if value:
        return canonical_recharge_ratio(value)

    match = RECHARGE_RATIO_IN_NOTES_PATTERN.search(str(site.get("notes") or ""))
    if not match:
        return "1:1"
    left, right = float(match.group(1)), float(match.group(2))
    if left > 0 and right > 0:
        return f"{format_ratio_value(left)}:{format_ratio_value(right)}"
    return "1:1"


def recharge_multiplier(site: dict[str, Any]) -> float:
    ratio = recharge_ratio_for_site(site)
    if not ratio:
        return 1.0
    left, right = ratio.split(":", 1)
    return float(left) / float(right)


def normalize_site(site: dict[str, Any]) -> dict[str, Any]:
    daily_checkin_bonus = empty_to_none(site.get("daily_checkin_bonus"))
    return {
        "name": str(site.get("name", "")).strip(),
        "url": str(site.get("url", "")).strip(),
        "category": str(site.get("category", "收费站")).strip() or "收费站",
        "usage_status": "常用" if site.get("usage_status") == "常用" else "",
        "balance": parse_number(site.get("balance")),
        "recharge_ratio": recharge_ratio_for_site(site),
        "welfare_rate": parse_number(site.get("welfare_rate")),
        "plus_rate": parse_number(site.get("plus_rate")),
        "pro_rate": parse_number(site.get("pro_rate")),
        "signup_bonus": parse_number(site.get("signup_bonus")),
        "daily_checkin_bonus": daily_checkin_bonus,
        "checkin_mode": normalized_checkin_mode(site, daily_checkin_bonus),
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


class MetapiSyncError(RuntimeError):
    pass


def normalized_site_name(value: Any) -> str:
    normalized = "".join(char for char in str(value or "").casefold() if char.isalnum())
    return METAPI_SITE_ALIASES.get(normalized, normalized)


def metapi_site_name_keys(value: Any) -> set[str]:
    """Return safe name variants for matching site labels from the two systems."""
    normalized = normalized_site_name(value)
    keys = {normalized} if normalized else set()
    simplified = normalized
    for label in METAPI_GENERIC_SITE_LABELS:
        simplified = simplified.replace(label, "")
    if simplified:
        keys.add(METAPI_SITE_ALIASES.get(simplified, simplified))
    return keys


def matched_metapi_balance(site_name: Any, balances: dict[str, float], used_keys: set[str]) -> tuple[str, float] | None:
    site_keys = metapi_site_name_keys(site_name)
    available = [(name, metapi_site_name_keys(name)) for name in balances if name not in used_keys]

    for account_name, account_keys in available:
        if site_keys.intersection(account_keys):
            return account_name, balances[account_name]
    # Do not use arbitrary prefix/suffix matching here. Names such as Ctoken and
    # MicToken share a substring but are different services and must not cross-update.
    return None


def read_metapi_auth_token() -> str:
    if METAPI_ENV_FILE is None or not METAPI_ENV_FILE.exists():
        raise MetapiSyncError("未找到 Metapi 本地配置，请先启动并完成 Metapi 部署。")
    for line in METAPI_ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("AUTH_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
            if token:
                return token
    raise MetapiSyncError("Metapi 配置中缺少 AUTH_TOKEN。")


def fetch_metapi_account_balances() -> tuple[dict[str, float], int]:
    request = Request(
        METAPI_ACCOUNTS_URL,
        headers={"Authorization": f"Bearer {read_metapi_auth_token()}"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise MetapiSyncError(f"Metapi 返回 HTTP {error.code}，请检查管理员令牌和服务状态。") from error
    except URLError as error:
        raise MetapiSyncError("无法连接 Metapi，请先确认 Metapi 已启动。") from error
    except (OSError, json.JSONDecodeError) as error:
        raise MetapiSyncError("读取 Metapi 余额失败，请稍后重试。") from error

    accounts = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(accounts, list):
        raise MetapiSyncError("Metapi 账户数据格式异常。")

    balances: dict[str, float] = {}
    active_accounts = 0
    for account in accounts:
        if not isinstance(account, dict) or account.get("status") != "active":
            continue
        site = account.get("site")
        site_name = site.get("name") if isinstance(site, dict) else ""
        key = normalized_site_name(site_name)
        balance = parse_number(account.get("balance"))
        if not key or balance is None:
            continue
        balances[key] = balances.get(key, 0.0) + balance
        active_accounts += 1
    return balances, active_accounts


def sync_metapi_balances() -> dict[str, Any]:
    balances, account_count = fetch_metapi_account_balances()
    sites = load_sites()
    updated_names: list[str] = []
    used_balance_keys: set[str] = set()
    # Metapi's connection list is the source of truth. A report row only needs a
    # check-in allowance to be eligible; its manual/automatic display label does
    # not decide whether a connected Metapi account can refresh its balance.
    sync_candidates = [
        site for site in sites
        if empty_to_none(site.get("daily_checkin_bonus")) is not None
    ]

    for site in sync_candidates:
        matched = matched_metapi_balance(site.get("name"), balances, used_balance_keys)
        if matched is None:
            continue
        balance_key, balance = matched
        used_balance_keys.add(balance_key)
        # Metapi balances are shown and stored as currency-like values.
        site["balance"] = round(balance * recharge_multiplier(site), 2)
        updated_names.append(str(site.get("name", "")))

    if updated_names:
        save_rows(sites)

    return {
        "updated": len(updated_names),
        "eligible": len(updated_names),
        "accounts": account_count,
        "sites": updated_names,
        "unmatched_accounts": sorted(set(balances).difference(used_balance_keys)),
    }


def edge_executable() -> str | None:
    candidates = [
        Path(os.environ[variable]) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")
        if os.environ.get(variable)
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def open_site_pages(urls: Any) -> int:
    if not isinstance(urls, list):
        raise ValueError("urls must be a list")

    allowed_urls = {
        str(site.get("url", "")).strip()
        for site in load_sites()
        if normalize_optional_http_url(site.get("url"))
    }
    selected: list[str] = []
    for value in urls[:50]:
        url = str(value).strip()
        if url in allowed_urls and url not in selected:
            selected.append(url)
    if not selected:
        raise ValueError("没有可打开的站点页面。")

    browser = edge_executable()
    if not browser:
        raise OSError("未找到 Microsoft Edge。")
    # Starting Edge once with dozens of URLs makes Chromium schedule all
    # navigations at the same time.  On a cold start (or when an existing Edge
    # process is still busy) several tabs can be left with a blank renderer
    # until the user manually refreshes them.  Open each URL as an explicit
    # new tab and give the browser a small amount of time to attach the tab to
    # its renderer before queueing the next navigation.
    opened = 0
    for index, url in enumerate(selected):
        subprocess.Popen(
            [browser, "--new-tab", url],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        opened += 1
        # The first launch may need to create the browser process/profile;
        # subsequent launches only enqueue a tab in the already running one.
        if index < len(selected) - 1:
            time.sleep(0.35 if index == 0 else 0.12)
    return opened


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
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
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
        if path == "/tokens.css":
            file_response(self, ROOT / "tokens.css", "text/css; charset=utf-8")
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
        if path == "/api/metapi/balances":
            try:
                result = sync_metapi_balances()
            except MetapiSyncError as error:
                json_response(self, {"error": str(error)}, status=503)
                return
            except (OSError, sqlite3.Error, ValueError):
                json_response(self, {"error": "同步余额失败，请稍后重试。"}, status=500)
                return
            json_response(self, {"ok": True, **result})
            return
        if path == "/api/open-site-pages":
            try:
                opened = open_site_pages(payload.get("urls"))
            except (OSError, ValueError) as error:
                json_response(self, {"error": str(error)}, status=400)
                return
            json_response(self, {"ok": True, "opened": opened})
            return
        json_response(self, {"error": "not found"}, status=404)


EDITOR_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI 模型中转站编辑器</title>
  <style>
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
        radial-gradient(circle at 10% -10%, rgba(15, 159, 143, 0.10), transparent 32%),
        radial-gradient(circle at 90% -12%, rgba(23, 92, 211, 0.09), transparent 30%),
        linear-gradient(180deg, #fbfcfe 0, var(--bg) 280px);
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
      letter-spacing: -0.02em;
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
      background: var(--warn-soft);
      color: #754600;
      box-shadow: 0 0 0 2px rgba(217,154,22,.28), inset 0 0 0 1px rgba(255,255,255,.5);
      font-weight: 900;
    }
    button.metapi-balance-sync {
      border-color: #0f9f8f;
      background: #e3f7f4;
      color: #087f73;
    }
    button.metapi-balance-sync:hover {
      border-color: #087f73;
      background: #c7efe9;
      color: #05665d;
    }
    button.metapi-balance-sync:disabled {
      border-color: #d0d5dd;
      background: #f2f4f7;
      color: #98a2b3;
      cursor: wait;
      transform: none;
    }
    button.danger {
      border-color: #fecdca;
      color: var(--danger);
      background: var(--danger-soft);
    }
    .workspace {
      display: block;
      margin-top: 16px;
    }
    .search-tools { position:relative; display:grid; width:100%; grid-template-columns: 560px minmax(0,1fr); gap:18px; align-items:center; margin-top:12px; padding:0 8px; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(5, minmax(128px, 1fr));
      gap: 10px;
    }
    .metric {
      position: relative;
      padding: 13px 14px;
      border: 1px solid var(--line);
      border-radius: var(--radius-m);
      background: var(--panel);
      box-shadow: var(--shadow-card);
      transition: transform 140ms ease, box-shadow 140ms ease, border-color 140ms ease;
    }
    .metric:hover {
      transform: translateY(-2px);
      border-color: rgba(15, 159, 143, 0.45);
      box-shadow: 0 14px 30px rgba(16, 24, 40, 0.10);
    }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 6px;
    }
    .metric-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      margin-bottom: 7px;
      border-radius: 9px;
      background: var(--accent-soft);
      color: var(--accent-dark);
      font-size: 15px;
    }
    .metric:nth-child(2) .metric-icon { background: var(--blue-soft); color: var(--blue); }
    .metric:nth-child(3) .metric-icon { background: var(--blue-soft); color: var(--blue); }
    .metric:nth-child(4) .metric-icon { background: var(--gold-soft); color: var(--alert); }
    .metric:nth-child(5) .metric-icon { background: var(--warn-soft); color: var(--warn); }
    .metrics-legend {
      width: max-content;
      position:absolute;
      right:8px;
      top:50%;
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px 14px;
      margin-top: 0;
      justify-content: flex-end;
      transform:translateY(-50%);
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .metrics-legend-title { color: var(--text); font-weight: 800; }
    .metrics-legend span:not(.metrics-legend-title) { display: inline-flex; align-items: center; gap: 4px; }
    .metrics-legend .legend-symbol { font-size: 14px; }
    .metric-value {
      font-size: 21px;
      font-weight: 800;
      line-height: 1.1;
      font-variant-numeric: tabular-nums;
    }
    .searchbox {
      width: 100%;
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
      width: 100%;
      margin-top: 12px;
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
      border-radius: var(--radius-l);
      background: var(--panel);
      box-shadow: var(--shadow-panel);
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
    .recharge-ratio-inputs { display: inline-flex; align-items: center; gap: 3px; }
    .recharge-ratio-inputs input { width: 54px; min-width: 54px; text-align: center; }
    .recharge-ratio-separator { color: var(--muted); font-weight: 800; }
    select.category-input { min-width: 86px; }
    select.usage-input { min-width: 72px; font-weight: 700; }
    tbody tr.is-common-site { background: linear-gradient(90deg, rgba(255, 221, 102, 0.62), rgba(255, 250, 230, 0.96) 52%); outline: 2px solid rgba(217, 154, 0, 0.52); outline-offset: -2px; }
    tbody tr.is-common-site .site-input { color: #704500; font-weight: 850; border-color: #e7a23b; background: #fff9e8; }
    tbody tr.is-common-site select.usage-input { border-color: #fdb022; background: #fffaeb; color: #93370d; }
    .panel tbody tr.has-checkin td:nth-child(2)::before,
    .panel tbody tr.has-balance td:nth-child(2)::after,
    .panel tbody tr.is-common-site td:nth-child(2)::after {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 18px;
      margin-right: 5px;
      padding: 1px 5px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 850;
      vertical-align: 1px;
    }
    tbody tr.has-debt {
      background: #fff1f0 !important;
      box-shadow: inset 5px 0 0 #d92d20 !important;
    }
    tbody tr.has-debt input[data-field="balance"] {
      color: #b42318 !important;
      border-color: #f04438 !important;
      background: #fff5f4 !important;
      font-weight: 850;
    }
    html[data-theme="dark"] tbody tr.has-debt {
      background: #3a2024 !important;
      box-shadow: inset 5px 0 0 #f97066 !important;
    }
    html[data-theme="dark"] tbody tr.has-debt input[data-field="balance"] {
      color: #fda29b !important;
      border-color: #f97066 !important;
      background: #4a252a !important;
    }
    .editor-status-badges { display:flex; flex-wrap:wrap; gap:4px; margin-top:3px; }
    .editor-status-badge { display:inline-flex; align-items:center; min-height:17px; padding:1px 5px; border:1px solid; border-radius:999px; font-size:10px; font-weight:850; line-height:1; }
    .editor-checkin-toggle { min-width:72px; min-height:30px; padding:4px 9px; border:2px solid #f79009; border-radius:7px; background:linear-gradient(180deg,#ffb547,#f79009); color:#351c00; font-size:12px; font-weight:900; letter-spacing:.2px; white-space:nowrap; cursor:pointer; box-shadow:0 2px 6px rgba(247,144,9,.35); text-shadow:0 1px rgba(255,255,255,.35); transition:transform 120ms ease,box-shadow 120ms ease,filter 120ms ease; }
    .editor-checkin-toggle:hover { filter:brightness(1.08); transform:translateY(-1px); box-shadow:0 4px 10px rgba(247,144,9,.45); }
    .editor-checkin-toggle:active { transform:translateY(0); }
    .editor-checkin-toggle.is-signed { border-color:#12b76a; background:linear-gradient(180deg,#32d583,#12b76a); color:#032d1b; box-shadow:0 2px 6px rgba(18,183,106,.35); }
    .editor-badge-common { border-color:#e7a23b; background:#fff4d6; color:#8a5200; }
    .editor-badge-balance { border-color:#b2ccff; background:#eaf3ff; color:#175cd3; }
    .editor-badge-checkin { border-color:#fedf89; background:#fff4d7; color:#93370d; }
    .editor-badge-debt { border-color:#fecdca; background:#fff1f0; color:#b42318; }
    html[data-theme="dark"] .editor-badge-debt { border-color:#f97066; background:#4a252a; color:#fda29b; }
    .signed-today .editor-badge-checkin { border-color:#abefc6; background:#ecfdf3; color:#067647; }
    .panel tbody tr.has-checkin td:nth-child(2)::before {
      content: "⏳ 待签到";
      border: 1px solid #fedf89;
      background: #fff4d7;
      color: #93370d;
    }
    .panel tbody tr.has-balance.is-common-site td:nth-child(2)::after { content: "★ 常用 · 💰 有余额"; }
    .panel tbody tr.has-checkin.signed-today td:nth-child(2)::before {
      content: "✓ 已签到";
      border-color: #abefc6;
      background: #ecfdf3;
      color: #067647;
    }
    .panel tbody tr.has-balance td:nth-child(2)::after {
      content: "💰 有余额";
      border: 1px solid #b2ccff;
      background: #eaf3ff;
      color: #175cd3;
    }
    .panel tbody tr.is-common-site td:nth-child(2)::after {
      content: "★ 常用";
      border: 1px solid #fdb022;
      background: #fffaeb;
      color: #93370d;
    }
    .panel tbody tr.has-checkin td:nth-child(2)::before,
    .panel tbody tr.has-balance td:nth-child(2)::after,
    .panel tbody tr.is-common-site td:nth-child(2)::after { display:none; }
    input.url { min-width: 260px; }
    input.url.just-opened {
      border-color: #12b76a !important;
      background: #ecfdf3 !important;
      box-shadow: 0 0 0 3px rgba(18, 183, 106, 0.2), 0 0 18px rgba(18, 183, 106, 0.24) !important;
      transition: border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
    }
    tbody tr.new-row-editing {
      background: #bfdbfe !important;
      box-shadow: inset 0 3px 0 #2563eb, inset 0 -3px 0 #2563eb, 0 0 0 2px rgba(37, 99, 235, 0.22) !important;
      animation: new-row-glow 1.8s ease-in-out infinite;
    }
    tbody tr.new-row-editing input,
    tbody tr.new-row-editing select {
      background: #eff6ff !important;
      border-color: #60a5fa !important;
    }
    tbody tr.new-row-editing td:first-child {
      border-left: 6px solid #2563eb !important;
    }
    .new-row-label {
      display: block;
      width: max-content;
      margin: 3px auto 0;
      padding: 3px 7px;
      border-radius: 999px;
      background: #1d4ed8;
      color: #fff;
      font-size: 12px;
      font-weight: 850;
      box-shadow: 0 2px 7px rgba(37, 99, 235, 0.38);
      animation: new-label-glow 1.2s ease-in-out infinite;
    }
    html[data-theme="dark"] tbody tr.new-row-editing {
      background: #164e78 !important;
      box-shadow: inset 0 3px 0 #60a5fa, inset 0 -3px 0 #60a5fa, 0 0 0 2px rgba(96, 165, 250, 0.28) !important;
    }
    html[data-theme="dark"] tbody tr.new-row-editing td:first-child { border-left: 6px solid #60a5fa !important; }
    html[data-theme="dark"] .new-row-label { background: #60a5fa; color: #082f49; }
    html[data-theme="dark"] tbody tr.new-row-editing input,
    html[data-theme="dark"] tbody tr.new-row-editing select { background: #123b63 !important; border-color: #60a5fa !important; }
    @keyframes new-row-glow {
      0%, 100% { box-shadow: inset 0 3px 0 #2563eb, inset 0 -3px 0 #2563eb, 0 0 0 2px rgba(37, 99, 235, 0.2), 0 0 10px rgba(37, 99, 235, 0.16); }
      50% { box-shadow: inset 0 3px 0 #1d4ed8, inset 0 -3px 0 #1d4ed8, 0 0 0 3px rgba(37, 99, 235, 0.38), 0 0 24px rgba(37, 99, 235, 0.42); }
    }
    @keyframes new-row-glow-dark {
      0%, 100% { box-shadow: inset 0 3px 0 #60a5fa, inset 0 -3px 0 #60a5fa, 0 0 0 2px rgba(96, 165, 250, 0.24), 0 0 12px rgba(96, 165, 250, 0.2); }
      50% { box-shadow: inset 0 3px 0 #93c5fd, inset 0 -3px 0 #93c5fd, 0 0 0 3px rgba(96, 165, 250, 0.46), 0 0 28px rgba(96, 165, 250, 0.5); }
    }
    html[data-theme="dark"] tbody tr.new-row-editing { animation-name: new-row-glow-dark; }
    @keyframes new-label-glow {
      0%, 100% { transform: scale(1); box-shadow: 0 2px 7px rgba(37, 99, 235, 0.35), 0 0 0 rgba(37, 99, 235, 0); }
      50% { transform: scale(1.08); box-shadow: 0 2px 10px rgba(37, 99, 235, 0.55), 0 0 14px rgba(37, 99, 235, 0.65); }
    }
    @media (prefers-reduced-motion: reduce) {
      tbody tr.new-row-editing { animation: none !important; }
    }
    html[data-theme="dark"] input.url.just-opened {
      border-color: #6ce9a6 !important;
      background: #123b31 !important;
      color: #d1fadf !important;
      box-shadow: 0 0 0 3px rgba(108, 233, 166, 0.24), 0 0 20px rgba(108, 233, 166, 0.28) !important;
    }
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
      background: var(--warn-soft);
      color: #93370d;
    }
    .toast {
      position: fixed;
      z-index: 1000;
      right: 24px;
      bottom: 24px;
      max-width: min(400px, calc(100vw - 32px));
      padding: 12px 15px;
      border: 1px solid #b8ddd8;
      border-radius: 8px;
      background: #f0fbf9;
      color: #05665d;
      font-size: 14px;
      font-weight: 750;
      box-shadow: 0 14px 32px rgba(16, 24, 40, 0.16);
      opacity: 0;
      pointer-events: none;
      transform: translateY(10px);
      transition: opacity 160ms ease, transform 160ms ease;
    }
    .toast.is-visible { opacity: 1; transform: translateY(0); }
    .toast.error { border-color: #fecdca; background: #fff4f2; color: var(--danger); }
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
  <link rel="stylesheet" href="/tokens.css">
  <link rel="stylesheet" href="/theme.css">
  <script src="/theme.js"></script>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>AI 模型中转站编辑器</h1>
        <div class="muted">保存后同步更新 sites.json、SQLite、HTML 报告和 CSV。</div>
      </div>
      <div class="actions">
        <div data-theme-control></div>
        <span id="status" class="status">加载中...</span>
        <a id="open-report" class="button" href="/reports/latest.html" target="ai_price_monitor_report" rel="noopener">打开报告</a>
        <a id="open-calculator" class="button" href="/calculator.html" target="ai_price_monitor_calculator" rel="noopener">成本计算器</a>
        <button id="metapi-balance-sync" class="metapi-balance-sync" type="button" title="从 Metapi 连接管理同步余额">同步 Metapi 余额</button>
        <button id="manual-checkin-filter" class="checkin-filter manual-checkin-filter" type="button" title="只显示手动签到站点">手动签到</button>
        <button id="add" title="在表格末尾新增一个站点">新增一行</button>
        <button id="save" class="primary" title="保存编辑内容并重新生成报告">保存并同步</button>
      </div>
    </div>
    <div class="workspace">
      <div class="metrics">
        <div class="metric"><div class="metric-icon" title="站点总数量">▦</div><div class="metric-label">站点数</div><div id="metric-count" class="metric-value">-</div></div>
        <div class="metric"><div class="metric-icon" title="所有站点中的最低倍率">↘</div><div class="metric-label">最低倍率</div><div id="metric-best" class="metric-value">-</div></div>
        <div class="metric"><div class="metric-icon" title="余额大于 0 的站点数量">💰</div><div class="metric-label">有余额站点</div><div id="metric-balance" class="metric-value">-</div></div>
        <div class="metric"><div class="metric-icon" title="所有站点余额合计">¥</div><div class="metric-label">余额合计</div><div id="metric-total" class="metric-value">-</div></div>
        <div class="metric"><div class="metric-icon" title="今天还没有完成签到的站点">⏳</div><div class="metric-label">今日待签到</div><div id="metric-checkin" class="metric-value">-</div></div>
      </div>
      <div class="search-tools">
        <div id="search-wrap" class="search-wrap">
          <input id="search" class="searchbox" placeholder="搜索站点、网址、备注">
          <button id="clear-search" class="search-clear" type="button" aria-label="清空搜索" title="清空搜索">×</button>
        </div>
        <div class="metrics-legend" aria-label="状态图标说明">
          <span class="metrics-legend-title">图标说明：</span>
          <span><span class="legend-symbol">★</span>常用站</span>
          <span><span class="legend-symbol">💰</span>有余额</span>
          <span><span class="legend-symbol">⏳</span>待签到</span>
          <span><span class="legend-symbol">✓</span>今日已签到</span>
        </div>
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
              <th>签到类型</th>
              <th>余额</th>
              <th>充值倍率</th>
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
  <div id="toast" class="toast" role="status" aria-live="polite"></div>
  <script>
    const fields = ["name", "category", "usage_status", "welfare_rate", "plus_rate", "pro_rate", "signup_bonus", "daily_checkin_bonus", "checkin_mode", "balance", "recharge_ratio", "url", "invite_url", "notes"];
    const numeric = new Set(["welfare_rate", "plus_rate", "pro_rate", "signup_bonus", "balance"]);
    const tbody = document.querySelector("#tbody");
    const tableWrap = document.querySelector(".table-wrap");
    const statusEl = document.querySelector("#status");
    const toastEl = document.querySelector("#toast");
    const searchEl = document.querySelector("#search");
    const searchWrap = document.querySelector("#search-wrap");
    const clearSearch = document.querySelector("#clear-search");
    const metricCount = document.querySelector("#metric-count");
    const metricBest = document.querySelector("#metric-best");
    const metricBalance = document.querySelector("#metric-balance");
    const metricTotal = document.querySelector("#metric-total");
    const metricCheckin = document.querySelector("#metric-checkin");
    const metapiBalanceSync = document.querySelector("#metapi-balance-sync");
    const manualCheckinFilter = document.querySelector("#manual-checkin-filter");
    let rows = [];
    let query = "";
    let checkinFilterMode = "";
    let toastTimer;
    let newRowEditing = null;
    const editedAmountFields = new Map();

    function setStatus(text, cls = "") {
      statusEl.textContent = text;
      statusEl.className = `status ${cls}`;
    }

    function showToast(text, cls = "") {
      window.clearTimeout(toastTimer);
      toastEl.textContent = text;
      toastEl.className = `toast ${cls} is-visible`;
      toastTimer = window.setTimeout(() => {
        toastEl.className = `toast ${cls}`;
      }, 4200);
    }

    function notifyReportUpdated() {
      window.localStorage.setItem("ai-price-monitor:report-version", String(Date.now()));
    }

    function fmt(value) {
      return value === null || value === undefined || value === "" ? "" : String(value);
    }

    function numberValue(value) {
      if (value === "") return null;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }

    function isPaidSite(row) {
      return fmt(row.category || "收费站") === "收费站";
    }

    function rechargeRatioParts(row) {
      const match = /^([0-9]+(?:\.[0-9]+)?)\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)$/.exec(fmt(row.recharge_ratio || "1:1"));
      return match ? [match[1], match[2]] : ["1", "1"];
    }

    function rechargeMultiplier(row) {
      const [left, right] = rechargeRatioParts(row).map(Number);
      return Number.isFinite(left) && Number.isFinite(right) && left > 0 && right > 0 ? left / right : 1;
    }

    function foldEditedAmounts() {
      editedAmountFields.forEach((fields, row) => {
        if (!isPaidSite(row)) return;
        if (fields.has("balance") && row.balance !== null && row.balance !== "") {
          row.balance = Number((Number(row.balance) * rechargeMultiplier(row)).toFixed(3));
        }
        if (fields.has("signup_bonus") && row.signup_bonus !== null && row.signup_bonus !== "") {
          row.signup_bonus = Number((Number(row.signup_bonus) * rechargeMultiplier(row)).toFixed(2));
        }
        const numericCheckinBonus = fields.has("daily_checkin_bonus") ? numberValue(row.daily_checkin_bonus) : null;
        if (numericCheckinBonus !== null) {
          row.daily_checkin_bonus = Number((numericCheckinBonus * rechargeMultiplier(row)).toFixed(2));
        }
        if (fields.has("welfare_rate") && row.welfare_rate !== null && row.welfare_rate !== "") {
          row.welfare_rate = Number((Number(row.welfare_rate) * rechargeMultiplier(row)).toFixed(6));
        }
        if (fields.has("plus_rate") && row.plus_rate !== null && row.plus_rate !== "") {
          row.plus_rate = Number((Number(row.plus_rate) * rechargeMultiplier(row)).toFixed(6));
        }
        if (fields.has("pro_rate") && row.pro_rate !== null && row.pro_rate !== "") {
          row.pro_rate = Number((Number(row.pro_rate) * rechargeMultiplier(row)).toFixed(6));
        }
      });
      editedAmountFields.clear();
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
      if (checkinFilterMode && (!hasCheckin(row) || checkinMode(row) !== checkinFilterMode)) return false;
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

    function checkinMode(row) {
      if (!hasCheckin(row)) return "无签到";
      return fmt(row.checkin_mode) === "手动" ? "手动" : "自动";
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
      const manualCheckinStations = checkinStations.filter((row) => checkinMode(row) === "手动");
      const totalBalance = rows.reduce((sum, row) => sum + Number(row.balance || 0), 0);
      metricCount.textContent = String(rows.length);
      metricBest.textContent = best === "" ? "-" : `${best}x`;
      metricBalance.textContent = String(balanceRows.length);
      metricTotal.textContent = Number.isInteger(totalBalance) ? String(totalBalance) : totalBalance.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
      metricCheckin.textContent = String(checkinRows.length);
      manualCheckinFilter.classList.toggle("is-active", checkinFilterMode === "手动");
      manualCheckinFilter.setAttribute("aria-pressed", checkinFilterMode === "手动" ? "true" : "false");
      manualCheckinFilter.textContent = `${checkinFilterMode === "手动" ? "当前：" : ""}手动签到 (${manualCheckinStations.length})`;
    }

    function rowClass(row) {
      const rate = lowest(row);
      const classes = [];
      if (rate !== "" && Number(rate) <= 0.03) classes.push("tier-low");
      else if (rate !== "" && Number(rate) <= 0.1) classes.push("tier-mid");
      if (Number(row.balance || 0) > 0) classes.push("has-balance");
      if (Number(row.balance || 0) < 0) classes.push("has-debt");
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
      if (row === newRowEditing) tr.classList.add("new-row-editing");
        tr.dataset.index = String(index);
        const editorBadges = [];
        if (fmt(row.usage_status) === "常用") editorBadges.push('<span class="editor-status-badge editor-badge-common" title="标记为常用站">★ 常用</span>');
        if (Number(row.balance || 0) > 0) editorBadges.push('<span class="editor-status-badge editor-badge-balance" title="当前余额大于 0">💰 有余额</span>');
        if (Number(row.balance || 0) < 0) editorBadges.push('<span class="editor-status-badge editor-badge-debt" title="当前余额为负数，表示欠费">⚠ 欠费</span>');
        if (hasCheckin(row)) editorBadges.push(`<span class="editor-status-badge editor-badge-checkin" title="每日签到状态">${isSignedToday(row) ? "✓ 已签到" : "⏳ 待签到"}</span>`);
        tr.innerHTML = `
          <td class="rank">${index + 1}${row === newRowEditing ? '<span class="new-row-label">新增编辑中</span>' : ''}</td>
          <td><input class="site-input" data-field="name" value="${escapeHtml(fmt(row.name))}"><div class="editor-status-badges">${editorBadges.join("")}</div></td>
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
          <td>
            <select class="category-input" data-field="checkin_mode"${hasCheckin(row) ? "" : " disabled"}>
              <option value="无签到"${checkinMode(row) === "无签到" ? " selected" : ""}>无签到</option>
              <option value="手动"${checkinMode(row) === "手动" ? " selected" : ""}>手动签到</option>
            </select>
          </td>
          <td><input class="number" data-field="balance" value="${escapeHtml(fmt(row.balance))}"></td>
          <td>
            <div class="recharge-ratio-inputs">
              <input class="number" type="number" min="0.000001" step="any" data-field="recharge_ratio_left" value="${escapeHtml(isPaidSite(row) ? rechargeRatioParts(row)[0] : "")}"${isPaidSite(row) ? "" : " disabled"}>
              <span class="recharge-ratio-separator">:</span>
              <input class="number" type="number" min="0.000001" step="any" data-field="recharge_ratio_right" value="${escapeHtml(isPaidSite(row) ? rechargeRatioParts(row)[1] : "")}"${isPaidSite(row) ? "" : " disabled"}>
            </div>
          </td>
          <td><input class="url" data-field="url" title="按住 Ctrl + Shift 后左键点击，可打开并切换到此网页" value="${escapeHtml(fmt(row.url))}"></td>
          <td><input class="url" data-field="invite_url" title="按住 Ctrl + Shift 后左键点击，可打开并切换到此邀请链接" value="${escapeHtml(fmt(row.invite_url))}"></td>
          <td><input class="notes" data-field="notes" value="${escapeHtml(fmt(row.notes))}"></td>
          <td>
            ${hasCheckin(row) ? `<button class="editor-checkin-toggle${isSignedToday(row) ? " is-signed" : ""}" type="button" data-checkin-toggle="${index}" aria-pressed="${isSignedToday(row) ? "true" : "false"}">${isSignedToday(row) ? "已签到" : "标记已签"}</button>` : ""}
            <button class="danger" data-delete="${index}">删除</button>
          </td>
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
      if (field === "recharge_ratio_left" || field === "recharge_ratio_right") {
        const [left, right] = rechargeRatioParts(rows[index]);
        rows[index].recharge_ratio = field === "recharge_ratio_left"
          ? `${input.value}:${right}`
          : `${left}:${input.value}`;
      } else {
        rows[index][field] = numeric.has(field) ? numberValue(input.value) : input.value;
      }
      if (field === "balance" || field === "signup_bonus" || field === "daily_checkin_bonus" || field === "welfare_rate" || field === "plus_rate" || field === "pro_rate") {
        if (!editedAmountFields.has(rows[index])) editedAmountFields.set(rows[index], new Set());
        editedAmountFields.get(rows[index]).add(field);
      }
      if (field === "category") {
        rows[index].recharge_ratio = isPaidSite(rows[index]) ? (fmt(rows[index].recharge_ratio) || "1:1") : "";
        const [leftInput, rightInput] = tr.querySelectorAll('input[data-field="recharge_ratio_left"], input[data-field="recharge_ratio_right"]');
        const [left, right] = rechargeRatioParts(rows[index]);
        if (leftInput && rightInput) {
          leftInput.value = isPaidSite(rows[index]) ? left : "";
          rightInput.value = isPaidSite(rows[index]) ? right : "";
          leftInput.disabled = !isPaidSite(rows[index]);
          rightInput.disabled = !isPaidSite(rows[index]);
        }
      }
      if (field === "daily_checkin_bonus") {
        // 一旦填写签到奖励，就按人工签到处理。
        rows[index].checkin_mode = hasCheckin(rows[index]) ? "手动" : "无签到";
        const checkinModeSelect = tr.querySelector('select[data-field="checkin_mode"]');
        if (checkinModeSelect) {
          checkinModeSelect.value = checkinMode(rows[index]);
          checkinModeSelect.disabled = !hasCheckin(rows[index]);
        }
      }
      tr.querySelector(".readonly").textContent = fmt(lowest(rows[index])) + (lowest(rows[index]) === "" ? "" : "x");
      tr.className = rowClass(rows[index]);
      updateMetrics();
      setStatus("有未保存修改", "dirty");
    }

    tbody.addEventListener("input", updateRowFromField);
    tbody.addEventListener("change", updateRowFromField);

    tbody.addEventListener("click", (event) => {
      const input = event.target.closest('input[data-field="url"], input[data-field="invite_url"]');
      if (!input || !event.ctrlKey || !event.shiftKey || event.button !== 0) return;
      const url = input.value.trim();
      try {
        const parsed = new URL(url);
        if (!/^https?:$/.test(parsed.protocol)) return;
      } catch {
        return;
      }
      event.preventDefault();
      // 用原生链接的用户手势打开，Edge 对这种方式的前台切换支持最好。
      const launchLink = document.createElement("a");
      launchLink.href = url;
      launchLink.target = "_blank";
      launchLink.rel = "noopener noreferrer";
      launchLink.style.position = "fixed";
      launchLink.style.left = "-10000px";
      document.body.appendChild(launchLink);
      launchLink.click();
      launchLink.remove();
      input.classList.remove("just-opened");
      window.requestAnimationFrame(() => input.classList.add("just-opened"));
      window.setTimeout(() => input.classList.remove("just-opened"), 2600);
      showToast(`已打开${input.dataset.field === "invite_url" ? "邀请链接" : "页面"}，请查看新标签页`);
      setStatus("链接已打开", "ok");
    });

    tbody.addEventListener("click", (event) => {
      const checkinButton = event.target.closest("button[data-checkin-toggle]");
      if (checkinButton) {
        const row = rows[Number(checkinButton.dataset.checkinToggle)];
        if (!row || !hasCheckin(row)) return;
        window.localStorage.setItem(checkinStorageKey(row), "1");
        render();
        setStatus(`已标记“${fmt(row.name) || "该站点"}”今日已签到`, "ok");
        showToast("已标记今日签到");
        return;
      }
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
      const row = {name: "", category: "收费站", usage_status: "", url: "", balance: null, recharge_ratio: "1:1", welfare_rate: null, plus_rate: null, pro_rate: null, signup_bonus: null, daily_checkin_bonus: null, checkin_mode: "无签到", notes: "", invite_url: ""};
      rows.push(row);
      newRowEditing = row;
      query = "";
      checkinFilterMode = "";
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

    function toggleCheckinFilter(mode) {
      checkinFilterMode = checkinFilterMode === mode ? "" : mode;
      render();
    }

    manualCheckinFilter.addEventListener("click", () => toggleCheckinFilter("手动"));

    metapiBalanceSync.addEventListener("click", async () => {
      const originalText = metapiBalanceSync.textContent;
      metapiBalanceSync.disabled = true;
      metapiBalanceSync.textContent = "同步中...";
      setStatus("正在读取 Metapi 余额...");
      try {
        const response = await fetch("/api/metapi/balances", { method: "POST" });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "同步失败");
        await load();
        const unmatched = Array.isArray(payload.unmatched_accounts) ? payload.unmatched_accounts.filter(Boolean) : [];
        const warnings = [];
        if (unmatched.length) warnings.push(`Metapi 未匹配：${unmatched.join("、")}`);
        const message = `Metapi 余额已同步：更新 ${payload.updated} / ${payload.accounts ?? payload.updated} 个连接${warnings.length ? `；${warnings.join("；")}` : ""}`;
        setStatus(message, "ok");
        showToast(message);
      } catch (error) {
        const message = error.message || "同步 Metapi 余额失败";
        setStatus(message, "error");
        showToast(message, "error");
      } finally {
        metapiBalanceSync.textContent = originalText;
        metapiBalanceSync.disabled = false;
      }
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
        foldEditedAmounts();
        const response = await fetch("/api/sites", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({sites: rows}),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "保存失败");
        setStatus(`已保存 ${payload.count} 个站点`, "ok");
        newRowEditing = null;
        await load();
        notifyReportUpdated();
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
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Editor: {url}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

