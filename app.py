from __future__ import annotations

from settings import load_environment
load_environment()

import purity_decisions as decisions
import purity_groups as groups
import usage_dashboard as usage
import usage_sites as usage_sites
import usage_icons
import usage_trends
import browser_sync

import json
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import subprocess
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from monitor import REPORTS_DIR, DB_FILE, build_reports, load_sites, normalize_optional_http_url, sort_snapshots, snapshot_from_site, sync_sites_to_db, write_sites


ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("APP_HOST", "127.0.0.1")
PORT = int(os.environ.get("APP_PORT", "8765"))
PUBLIC_ORIGIN = os.environ.get("APP_PUBLIC_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
METAPI_ENV_FILE = Path(os.environ["METAPI_ENV_FILE"]) if os.environ.get("METAPI_ENV_FILE") else None
METAPI_ACCOUNTS_URL = os.environ.get("METAPI_ACCOUNTS_URL", "http://127.0.0.1:4000/api/accounts")
AUTH_REQUIRED = os.environ.get("APP_AUTH_REQUIRED", "0") == "1"
AUTH_USERNAME = os.environ.get("APP_USERNAME", "")
AUTH_PASSWORD_HASH = os.environ.get("APP_PASSWORD_HASH", "")
SESSION_SECRET = os.environ.get("APP_SESSION_SECRET", "")
if AUTH_REQUIRED and not all((AUTH_USERNAME, AUTH_PASSWORD_HASH, SESSION_SECRET)):
    raise RuntimeError("APP_AUTH_REQUIRED needs APP_USERNAME, APP_PASSWORD_HASH and APP_SESSION_SECRET")


def load_auth_settings() -> None:
    global AUTH_USERNAME, AUTH_PASSWORD_HASH, SESSION_SECRET
    if not AUTH_REQUIRED:
        return
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS app_auth_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)")
        settings = dict(conn.execute("SELECT setting_key, setting_value FROM app_auth_settings"))
    AUTH_USERNAME = settings.get("username", AUTH_USERNAME)
    AUTH_PASSWORD_HASH = settings.get("password_hash", AUTH_PASSWORD_HASH)
    SESSION_SECRET = settings.get("session_secret", SESSION_SECRET)


def verify_password(username: str, password: str) -> bool:
    if not hmac.compare_digest(username.encode("utf-8"), AUTH_USERNAME.encode("utf-8")):
        return False
    try:
        algorithm, salt, expected = AUTH_PASSWORD_HASH.split("$", 2)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000).hex()
        return algorithm == "pbkdf2_sha256" and hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000).hex()
    return "pbkdf2_sha256$%s$%s" % (salt.hex(), digest)


load_auth_settings()
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


def normalize_purity_match(value: Any) -> float | None:
    """Store purity as a percentage: 999 -> 99.9, 0.3677 -> 36.77."""
    number = parse_number(value)
    if number is None:
        return None
    if number == 100:
        return 100.0
    if number > 100:
        number /= 10
    elif 0 < number <= 1:
        number *= 100
    return round(number, 4)


def normalize_purity_multiplier(value: Any) -> float | None:
    """倍率快捷输入：010 -> 0.1，0125 -> 0.125。"""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.startswith("0") and text.isdigit() and len(text) > 1:
        return float("0." + text[1:])
    number = parse_number(value)
    if number is not None and number >= 10 and number.is_integer():
        digits = len(str(int(number)))
        return number / (10 ** digits)
    return number


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
        "purity_status": str(site.get("purity_status") or ""),
        "purity_match": normalize_purity_match(site.get("purity_match")),
        "purity_multiplier": normalize_purity_multiplier(site.get("purity_multiplier")),
        "reimbursement_status": str(site.get("reimbursement_status") or ""),
        "reimbursement_notes": str(site.get("reimbursement_notes") or "").strip(),
        "balance": parse_number(site.get("balance")),
        "balance_2": parse_number(site.get("balance_2")),
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
    try:
        with sqlite3.connect(DB_FILE, timeout=20) as conn:
            mr=conn.execute("SELECT site_name, model FROM purity_checks WHERE trim(model) <> '' ORDER BY checked_at DESC, id DESC").fetchall()
        mm={}
        for n,m in mr:
            mm.setdefault(n,[])
            if m not in mm[n]: mm[n].append(m)
        for r in rows: r["purity_models"]=mm.get(r.get("name"),[])
    except sqlite3.Error:
        for r in rows: r["purity_models"]=[]
    return rows


def init_purity_checks() -> None:
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS purity_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            match_percent REAL,
            multiplier REAL,
            checked_at TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT ''
        )""")
        conn.commit()


def purity_payload() -> dict[str, Any]:
    init_purity_checks()
    sites = []
    for site in load_sites():
        normalized = normalize_site(site)
        sites.append({
            "name": normalized["name"],
            "balance": normalized.get("balance"),
            "purity_status": normalized.get("purity_status", ""),
            "purity_match": normalized.get("purity_match"),
            "purity_multiplier": normalized.get("purity_multiplier"),
            "reimbursement_status": normalized.get("reimbursement_status", ""),
            "reimbursement_notes": normalized.get("reimbursement_notes", ""),
        })
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        history = [dict(zip(("id", "site_name", "match_percent", "multiplier", "checked_at", "notes", "model"), row)) for row in conn.execute("SELECT id, site_name, match_percent, multiplier, checked_at, notes, model FROM purity_checks ORDER BY checked_at DESC, id DESC")]
    return {"sites": sites, "history": groups.annotate(history, DB_FILE)}


def save_reimbursement(payload: dict[str, Any]) -> None:
    site_name = str(payload.get("site_name", "")).strip()
    if not site_name: raise ValueError("请选择站点")
    status = str(payload.get("reimbursement_status", "")).strip()
    notes = str(payload.get("reimbursement_notes", "")).strip()
    sites = load_sites()
    target = next((site for site in sites if site.get("name") == site_name), None)
    if target is None: raise ValueError("站点不存在")
    target["reimbursement_status"] = status
    target["reimbursement_notes"] = notes
    write_sites(sites)
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        sync_sites_to_db(conn, [normalize_site(site) for site in sites])
        conn.commit()
    build_reports(sites)

def save_purity_check(payload: dict[str, Any]) -> None:
    site_name = str(payload.get("site_name", "")).strip()
    if not site_name:
        raise ValueError("请选择站点")
    match = normalize_purity_match(payload.get("match_percent"))
    multiplier = normalize_purity_multiplier(payload.get("multiplier"))
    if multiplier is None:
        existing_sites = load_sites()
        existing = next((site for site in existing_sites if site.get("name") == site_name), None)
        multiplier = normalize_purity_multiplier(existing.get("purity_multiplier")) if existing else None
    if match is None or match < 0 or match > 100:
        raise ValueError("匹配度应为 0 到 100，支持输入 999 表示 99.9%")
    if multiplier is None or multiplier < 0:
        raise ValueError("请填写有效的验纯倍率")
    checked_at = str(payload.get("checked_at", "")).strip().replace("T", " ") or time.strftime("%Y-%m-%d %H:%M")
    notes = str(payload.get("notes", "")).strip()
    model = str(payload.get("model", "")).strip() or "GPT-6.1-Sol"
    sites = load_sites()
    target = next((site for site in sites if site.get("name") == site_name), None)
    if target is None:
        raise ValueError("站点不存在")
    conclusion = None
    requested_status = str(payload.get("conclusion_status", "auto")).strip()
    if requested_status:
        selected_status = decisions.auto_status(match) if requested_status == "auto" else requested_status
        conclusion = decisions.validate({
            "site_name": site_name, "model": model,
            "scope": decisions.multiplier_scope(multiplier),
            "status": selected_status, "reason": notes,
            "checked_at": checked_at,
        })
        decisions.initialize(DB_FILE)
    groups.initialize(DB_FILE)
    target["purity_match"] = match
    target["purity_multiplier"] = multiplier
    target["purity_status"] = "GPT-6.1-Sol 较纯" if match >= 94 else (target.get("purity_status") or "待复验")
    write_sites(sites)
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        init_sites_table = None
        conn.execute("""CREATE TABLE IF NOT EXISTS purity_checks (id INTEGER PRIMARY KEY AUTOINCREMENT, site_name TEXT NOT NULL, match_percent REAL, multiplier REAL, checked_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '')""")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(purity_checks)")}
        if "model" not in cols: conn.execute("ALTER TABLE purity_checks ADD COLUMN model TEXT NOT NULL DEFAULT ''")
        conn.execute("INSERT INTO purity_checks(site_name,match_percent,multiplier,checked_at,notes,model) VALUES (?,?,?,?,?,?)", (site_name, match, multiplier, checked_at, notes, model))
        groups.ensure_group(conn, site_name, model, multiplier)
        if conclusion is not None:
            decisions.insert_validated(conn, conclusion)
        sync_sites_to_db(conn, [normalize_site(site) for site in sites])
        conn.commit()
    build_reports(sites)


def save_purity_group_change(payload: dict[str, Any]) -> dict[str, Any]:
    sites = load_sites()
    site = next((x for x in sites if x.get("name") == payload.get("site_name")), None)
    if site is None: raise ValueError("站点不存在")
    body = dict(payload)
    if body.get("action") == "reprice":
        body["new_multiplier"] = normalize_purity_multiplier(body.get("new_multiplier"))
    result = groups.change(body, DB_FILE)
    active = [x for x in groups.data(DB_FILE)["groups"] if x["site_name"] == site["name"] and x["latest_check"]]
    if active:
        latest = max(active, key=lambda x: (x["latest_check"]["checked_at"].replace("T", " "), x["latest_check"]["id"]))
        if latest["id"] == result["group_id"]:
            site["purity_multiplier"] = latest["current_multiplier"]
            site["purity_match"] = latest["latest_check"]["match_percent"]
            write_sites(sites)
    build_reports(sites)
    return result


def save_rows(rows: list[dict[str, Any]]) -> None:
    sites = [normalize_site(row) for row in rows if str(row.get("name", "")).strip()]
    validate_unique_site_names(sites)
    write_sites(sites)
    DB_FILE.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        sync_sites_to_db(conn, sites)
    build_reports(sites)


def get_checkin_keys(check_date: str) -> list[str]:
    from datetime import datetime
    datetime.strptime(check_date, "%Y-%m-%d")
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS checkins (check_date TEXT NOT NULL, site_key TEXT NOT NULL, checked_at TEXT NOT NULL, PRIMARY KEY (check_date, site_key))")
        return [row[0] for row in conn.execute("SELECT site_key FROM checkins WHERE check_date = ?", (check_date,))]


def set_checkin_status(check_date: str, site_key: str, checked: bool) -> None:
    from datetime import datetime
    datetime.strptime(check_date, "%Y-%m-%d")
    if not site_key or len(site_key) > 2048 or type(checked) is not bool:
        raise ValueError("签到参数无效")
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS checkins (check_date TEXT NOT NULL, site_key TEXT NOT NULL, checked_at TEXT NOT NULL, PRIMARY KEY (check_date, site_key))")
        if checked:
            conn.execute("INSERT OR REPLACE INTO checkins (check_date, site_key, checked_at) VALUES (?, ?, ?)", (check_date, site_key, time.strftime("%Y-%m-%dT%H:%M:%S")))
        else:
            conn.execute("DELETE FROM checkins WHERE check_date = ? AND site_key = ?", (check_date, site_key))


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


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
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
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def build_migration_export() -> dict[str, Any]:
    """Build a portable business-data export without credentials or derived history."""
    sites = [normalize_site(site) for site in load_sites()]
    checkins: list[dict[str, str]] = []
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "checkins" in tables:
            checkins = [
                {"check_date": row[0], "site_key": row[1], "checked_at": row[2]}
                for row in conn.execute(
                    "SELECT check_date, site_key, checked_at FROM checkins ORDER BY check_date, site_key"
                )
            ]
    return {
        "format": "linx-price-monitor-migration",
        "version": 1,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "data": {
            "sites": sites,
            "checkins": checkins,
        },
        "excluded": [
            "account username", "password hash", "session secret", "login cookies",
            "Metapi credentials", "historical snapshots and generated reports",
        ],
    }


def migration_token(expiry: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), f"migration:{expiry}".encode(), hashlib.sha256).hexdigest()


MIGRATION_TOKENS: dict[str, float] = {}


def valid_migration_token(expiry: str, signature: str) -> bool:
    try:
        if int(expiry) < int(time.time()) or not signature:
            return False
    except (TypeError, ValueError):
        return False
    if hmac.compare_digest(migration_token(expiry), signature):
        return True
    return MIGRATION_TOKENS.get(signature, 0) >= time.time()


def import_migration_payload(payload: Any) -> int:
    if not isinstance(payload, dict) or payload.get("format") != "linx-price-monitor-migration":
        raise ValueError("迁移文件格式不正确")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        raise ValueError("迁移文件缺少站点数据")
    sites = data["sites"]
    save_rows(sites)
    checkins = data.get("checkins") if isinstance(data.get("checkins"), list) else []
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS checkins (check_date TEXT NOT NULL, site_key TEXT NOT NULL, checked_at TEXT NOT NULL, PRIMARY KEY (check_date, site_key))")
        conn.execute("DELETE FROM checkins")
        conn.executemany(
            "INSERT OR REPLACE INTO checkins (check_date, site_key, checked_at) VALUES (?, ?, ?)",
            [(str(row.get("check_date", "")), str(row.get("site_key", "")), str(row.get("checked_at", ""))) for row in checkins if isinstance(row, dict)],
        )
    return len(sites)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def authenticated(self) -> bool:
        if not AUTH_REQUIRED:
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            value = cookie["price_monitor_session"].value
            expires, signature = value.split(".", 1)
            if int(expires) < int(time.time()):
                return False
            expected = hmac.new(SESSION_SECRET.encode(), f"{AUTH_USERNAME}:{expires}".encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected)
        except (KeyError, ValueError, TypeError):
            return False

    def require_auth(self, api: bool = False) -> bool:
        if self.authenticated():
            return True
        if api:
            json_response(self, {"error": "请先登录"}, status=401)
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        return False

    def do_OPTIONS(self) -> None:
        if urlparse(self.path).path == "/api/import-from-cloud":
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", PUBLIC_ORIGIN)
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()
            return
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if browser_sync.handle(self, path, json_response):
            return
        if path in ("/login", "/login.html"):
            if self.authenticated():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
            else:
                html_response(self, LOGIN_HTML)
            return
        # Public personal producer artwork; application icons keep existing routes.
        personal_brand_assets = {
            "/assets/linx-v3-lockup_color.svg": "image/svg+xml",
            "/assets/linx-v3-lockup_white.svg": "image/svg+xml",
        }
        if path in personal_brand_assets:
            file_response(self, ROOT / "assets" / path.rsplit("/", 1)[1], personal_brand_assets[path])
            return
        query = parse_qs(urlparse(self.path).query)
        if path == "/api/export" and valid_migration_token(query.get("exp", [""])[0], query.get("sig", [""])[0]):
            self.send_migration_export()
            return
        if not self.require_auth(api=path.startswith("/api/")):
            return
        if path == "/browser-sync-ui.js":
            file_response(self, ROOT / "browser-sync-ui.js", "application/javascript; charset=utf-8")
            return
        if path == "/usage-trends.js":
            file_response(self, ROOT / "usage-trends.js", "application/javascript; charset=utf-8")
            return
        if path == "/cache.html":
            file_response(self, ROOT / "cache.html", "text/html; charset=utf-8")
            return
        if path.startswith("/api/cache/"):
            try:
                if path == "/api/cache/icon":
                    icon = usage_icons.get(query.get("site_id", ["lulu"])[0])
                    if not icon:
                        json_response(self, {"error": "站点暂未提供可用图标"}, status=404)
                        return
                    body, mime = icon
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "private, max-age=86400")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                elif path == "/api/cache/sites": result = {"sites": usage_sites.sites()}
                elif path == "/api/cache/status": result = usage_sites.client(query.get("site_id", ["lulu"])[0]).status()
                elif path == "/api/cache/metadata": result = usage_sites.client(query.get("site_id", ["lulu"])[0]).metadata()
                elif path == "/api/cache/trend": result = usage_trends.trend(query)
                elif path == "/api/cache/snapshot": result = usage_sites.client(query.get("site_id", ["lulu"])[0]).snapshot(query)
                else:
                    json_response(self, {"error": "接口不存在"}, status=404)
                    return
                json_response(self, result)
            except usage.UsageError as error:
                json_response(self, {"error": str(error), "code": error.code}, status=error.status)
            except Exception:
                json_response(self, {"error": "用量服务暂时无法读取，请稍后刷新。"}, status=500)
            return
        if path == "/":
            html_response(self, EDITOR_HTML)
            return
        if path == "/api/detector/auth":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path == "/purity.html":
            file_response(self, ROOT / "purity.html", "text/html; charset=utf-8")
            return
        if path == "/purity-groups.js":
            file_response(self, ROOT / "purity-groups.js", "text/javascript; charset=utf-8")
            return
        if path == "/api/purity/groups":
            json_response(self, groups.data(DB_FILE))
            return
        if path == "/purity-decisions.js":
            file_response(self, ROOT / "purity-decisions.js", "text/javascript; charset=utf-8")
            return
        if path == "/api/purity/conclusions":
            json_response(self, decisions.data())
            return
        if path == "/api/purity":
            json_response(self, purity_payload())
            return
        if path == "/api/sites":
            json_response(self, {"sites": current_rows()})
            return
        if path == "/api/export":
            self.send_migration_export()
            return
        if path == "/api/checkins":
            check_date = parse_qs(urlparse(self.path).query).get("date", [""])[0]
            try:
                keys = get_checkin_keys(check_date)
            except ValueError:
                json_response(self, {"error": "日期格式无效"}, status=400)
                return
            json_response(self, {"signed_keys": keys})
            return
        if path == "/api/export-link":
            expiry = str(int(time.time()) + 180)
            signature = migration_token(expiry)
            MIGRATION_TOKENS[signature] = time.time() + 180
            base = PUBLIC_ORIGIN + "/api/export?" + urlencode({"exp": expiry, "sig": signature})
            json_response(self, {"url": base, "expires_in": 180})
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
        if path == "/manifest.webmanifest":
            file_response(self, ROOT / "manifest.webmanifest", "application/manifest+json; charset=utf-8")
            return
        if path == "/assets/linx-mark.svg":
            file_response(self, ROOT / "assets" / "linx-mark.svg", "image/svg+xml")
            return
        if path == "/assets/linx-mark-512.png":
            file_response(self, ROOT / "assets" / "linx-mark-512.png", "image/png")
            return
        if path == "/calculator.html":
            file_response(self, ROOT / "calculator.html", "text/html; charset=utf-8")
            return
        if path == "/reports/latest.html":
            file_response(self, REPORTS_DIR / "latest.html", "text/html; charset=utf-8")
            return
        if path == "/reports/latest.csv":
            file_response(self, REPORTS_DIR / "latest.csv", "text/csv; charset=utf-8")
            return
        json_response(self, {"error": "not found"}, status=404)

    def send_migration_export(self) -> None:
        try:
            payload = json.dumps(build_migration_export(), ensure_ascii=False, indent=2).encode("utf-8")
        except sqlite3.Error:
            json_response(self, {"error": "导出数据失败，请稍后重试"}, status=500)
            return
        filename = "linx-price-monitor-data-%s.json" % time.strftime("%Y%m%d-%H%M%S")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        global AUTH_USERNAME, AUTH_PASSWORD_HASH, SESSION_SECRET
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            json_response(self, {"error": "invalid json"}, status=400)
            return
        if browser_sync.handle(self, path, json_response, payload):
            return
        if path == "/api/change-credentials":
            username = str(payload.get("username", ""))
            password = str(payload.get("password", ""))
            new_username = str(payload.get("new_username", "")).strip() or username
            new_password = str(payload.get("new_password", ""))
            if not verify_password(username, password):
                json_response(self, {"error": "当前账号名或密码不正确"}, status=401)
                return
            if not new_username or len(new_username) > 64 or any(char.isspace() for char in new_username):
                json_response(self, {"error": "新账号名需为 1–64 个字符，且不能包含空格"}, status=400)
                return
            if new_username == AUTH_USERNAME and not new_password:
                json_response(self, {"error": "请至少填写新账号名或新密码"}, status=400)
                return
            if new_password and len(new_password) < 8:
                json_response(self, {"error": "新密码至少需要 8 个字符"}, status=400)
                return
            updated_hash = hash_password(new_password) if new_password else AUTH_PASSWORD_HASH
            updated_secret = secrets.token_urlsafe(32)
            try:
                with sqlite3.connect(DB_FILE, timeout=20) as conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS app_auth_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)")
                    conn.executemany("INSERT INTO app_auth_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value", [("username", new_username), ("password_hash", updated_hash), ("session_secret", updated_secret)])
            except sqlite3.Error:
                json_response(self, {"error": "账号设置保存失败，请稍后重试"}, status=500)
                return
            AUTH_USERNAME, AUTH_PASSWORD_HASH, SESSION_SECRET = new_username, updated_hash, updated_secret
            expires = str(int(time.time()) + 7 * 24 * 3600)
            signature = hmac.new(SESSION_SECRET.encode(), (AUTH_USERNAME + ":" + expires).encode(), hashlib.sha256).hexdigest()
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
            cookie = "price_monitor_session=%s.%s; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax%s" % (expires, signature, secure)
            json_response(self, {"ok": True, "username": AUTH_USERNAME}, headers={"Set-Cookie": cookie})
            return
        if path == "/api/login":
            username = str(payload.get("username", ""))
            password = str(payload.get("password", ""))
            if not verify_password(username, password):
                json_response(self, {"error": "账号或密码不正确"}, status=401)
                return
            expires = str(int(time.time()) + 7 * 24 * 3600)
            signature = hmac.new(SESSION_SECRET.encode(), f"{AUTH_USERNAME}:{expires}".encode(), hashlib.sha256).hexdigest()
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
            json_response(self, {"ok": True}, headers={"Set-Cookie": f"price_monitor_session={expires}.{signature}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax{secure}"})
            return
        if path == "/api/import-from-cloud":
            origin = self.headers.get("Origin", "")
            if origin not in (PUBLIC_ORIGIN, "http://127.0.0.1:8765", "http://localhost:8765"):
                json_response(self, {"error": "仅允许 LinX 网页发起同步"}, status=403, headers={"Access-Control-Allow-Origin": PUBLIC_ORIGIN})
                return
            source_url = str(payload.get("source_url", "")).strip()
            if source_url:
                parsed_source = urlparse(source_url)
                if parsed_source.scheme != urlparse(PUBLIC_ORIGIN).scheme or parsed_source.netloc != urlparse(PUBLIC_ORIGIN).netloc or len(source_url) > 1024:
                    json_response(self, {"error": "云端同步地址无效"}, status=400, headers={"Access-Control-Allow-Origin": origin})
                    return
                try:
                    request = Request(source_url, headers={"Accept": "application/json", "User-Agent": "LinX-local-sync/1"})
                    with urlopen(request, timeout=30) as response:
                        downloaded = response.read(2_000_001)
                    if len(downloaded) > 2_000_000:
                        raise ValueError("迁移文件过大")
                    payload = json.loads(downloaded.decode("utf-8"))
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    json_response(self, {"error": f"无法从云端读取迁移数据：{error}"}, status=502, headers={"Access-Control-Allow-Origin": origin})
                    return
            elif len(raw.encode("utf-8")) > 2_000_000:
                json_response(self, {"error": "迁移文件过大"}, status=413, headers={"Access-Control-Allow-Origin": origin})
                return
            try:
                count = import_migration_payload(payload)
            except (ValueError, TypeError, sqlite3.Error) as error:
                json_response(self, {"error": str(error) or "导入失败"}, status=400, headers={"Access-Control-Allow-Origin": origin})
                return
            json_response(self, {"ok": True, "count": count}, headers={"Access-Control-Allow-Origin": origin})
            return
        if path == "/api/logout":
            json_response(self, {"ok": True}, headers={"Set-Cookie": "price_monitor_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
            return
        if not self.require_auth(api=path.startswith("/api/")):
            return
        if path in ("/api/cache/connect", "/api/cache/disconnect", "/api/cache/sites", "/api/cache/remove-site"):
            origin = self.headers.get("Origin", "")
            if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
                json_response(self, {"error": "请从本站页面连接账号。"}, status=403)
                return
            if not isinstance(payload, dict):
                json_response(self, {"error": "请求格式无效"}, status=400)
                return
            try:
                if path == "/api/cache/sites": result = usage_sites.add(payload)
                elif path == "/api/cache/remove-site": result = usage_sites.remove(payload.get("site_id"))
                else:
                    client = usage_sites.client(payload.get("site_id", "lulu"))
                    result = client.connect(payload) if path.endswith("/connect") else client.disconnect()
                json_response(self, result)
            except usage.UsageError as error:
                json_response(self, {"error": str(error), "code": error.code}, status=error.status)
            except Exception:
                json_response(self, {"error": "账号连接未完成，请稍后重试。"}, status=500)
            return
        if path == "/api/checkins":
            try:
                set_checkin_status(str(payload.get("date", "")), str(payload.get("site_key", "")), payload.get("checked"))
            except (ValueError, sqlite3.Error):
                json_response(self, {"error": "签到状态保存失败"}, status=400)
                return
            json_response(self, {"ok": True})
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
            except Exception as error:  # noqa: BLE001
                json_response(self, {"error": f"保存失败：{error}"}, status=500)
                return
            except Exception as error:
                json_response(self, {"error": f"保存失败：{error}"}, status=500)
                return
            json_response(self, {"ok": True, "count": len(rows)})
            return
        if path == "/api/purity":
            try:
                save_purity_check(payload)
            except (ValueError, sqlite3.Error) as error:
                json_response(self, {"error": str(error)}, status=400)
                return
            json_response(self, {"ok": True})
            return
        if path == "/api/purity/groups":
            try:
                result = save_purity_group_change(payload)
            except groups.Conflict as error:
                json_response(self, {"error": str(error)}, status=409)
                return
            except (ValueError, sqlite3.Error) as error:
                json_response(self, {"error": str(error)}, status=400)
                return
            json_response(self, result)
            return
        if path == "/api/purity/conclusions":
            try:
                if payload.get("site_name") not in {x.get("name") for x in load_sites()}:raise ValueError("站点不存在")
                decisions.save(payload)
                build_reports(load_sites())
            except (ValueError, sqlite3.Error) as error:
                json_response(self, {"error": str(error)}, status=400)
                return
            json_response(self, {"ok": True})
            return
        if path == "/api/purity/reimbursement":
            try:
                save_reimbursement(payload)
            except (ValueError, sqlite3.Error) as error:
                json_response(self, {"error": str(error)}, status=400)
                return
            json_response(self, {"ok": True})
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


LOGIN_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#f6f8fa"><link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='18' fill='%23152b3c'/%3E%3Cpath d='M10 13h7v31h16v7H10V13Z' fill='%23fff'/%3E%3Cpath d='M37 42 43 35l6 4 7-15' fill='none' stroke='%2379c7b9' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'/%3E%3Ccircle cx='56' cy='24' r='2.5' fill='%2379c7b9'/%3E%3C/svg%3E"><title>LinX · 价格监控</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:clamp(12px,2.5vw,36px);background:radial-gradient(ellipse at 12% 12%,#e8f4f0 0,transparent 34%),radial-gradient(ellipse at 90% 88%,#e9eef8 0,transparent 38%),#f7f8fa;color:#1d2635;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}.shell{width:min(94%,1840px);min-height:min(900px,calc(100vh - 72px));margin:0 auto;display:grid;grid-template-columns:1.08fr .92fr;overflow:hidden;border:1px solid #e7eaf0;border-radius:30px;background:#fff;box-shadow:0 28px 90px #23334a12}.hero{position:relative;display:flex;flex-direction:column;justify-content:space-between;padding:68px 76px;background:linear-gradient(145deg,#fbfcfd,#f1f5f7);overflow:hidden}.hero:after{content:"";position:absolute;width:470px;height:470px;right:-210px;bottom:-210px;border:1px solid #dce7e7;border-radius:50%;box-shadow:0 0 0 34px #ffffff60,0 0 0 68px #ffffff40}.brand{position:relative;z-index:1;display:flex;align-items:center;gap:12px;color:#1c2938;font-weight:700}.brand-icon{display:grid;width:46px;height:46px;place-items:center;border-radius:15px;background:#152b3c;color:#fff;font-size:14px;letter-spacing:.06em;overflow:hidden;box-shadow:0 8px 18px #152b3c20}.brand-icon svg{width:100%;height:100%;display:block}.hero-copy{position:relative;z-index:1;max-width:470px;margin:30px 0 60px}.eyebrow{margin:0 0 18px;color:#427f78;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}.hero h1{margin:0;font-size:clamp(40px,5vw,58px);line-height:1.13;font-weight:650;letter-spacing:-.055em;color:#182737}.hero-copy p{max-width:420px;margin:20px 0 0;color:#687687;font-size:16px;line-height:1.85}.hero-foot{position:relative;z-index:1;color:#8a96a4;font-size:12px}.panel{display:flex;align-items:center;justify-content:center;padding:48px 64px}.panel-inner{width:min(100%,400px)}.panel-top{margin-bottom:34px}.panel-top .eyebrow{margin-bottom:10px}.panel h2{margin:0;color:#1c2938;font-size:30px;line-height:1.25;font-weight:650;letter-spacing:-.04em}.lead{margin:10px 0 0;color:#7b8795;font-size:14px}.field{margin-top:19px}.field label{display:block;margin:0 0 8px;color:#465363;font-size:13px;font-weight:600}.field input{width:100%;height:50px;padding:0 14px;border:1px solid #d9dfe7;border-radius:12px;background:#fff;color:#1d2635;font:inherit;outline:none;transition:border-color .18s,box-shadow .18s}.field input::placeholder{color:#a6afbb}.field input:focus{border-color:#6daea4;box-shadow:0 0 0 4px #6daea41c}.password-wrap{position:relative}.password-wrap input{padding-right:68px}.reveal{position:absolute;top:0;right:10px;height:50px;padding:0 7px;border:0;background:transparent;color:#648078;font-family:inherit;font-size:12px;font-weight:600;cursor:pointer}.primary{width:100%;height:50px;margin-top:25px;border:0;border-radius:12px;background:#1f746c;color:#fff;font-family:inherit;font-size:15px;font-weight:650;cursor:pointer;transition:background .18s,transform .18s}.primary:hover{background:#185f59;transform:translateY(-1px)}.primary:disabled{opacity:.62;cursor:wait;transform:none}.links{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:17px}.text-button{padding:4px 0;border:0;background:none;color:#527f79;font-family:inherit;font-size:13px;font-weight:600;white-space:nowrap;cursor:pointer}.text-button:hover{color:#1d625b;text-decoration:underline}.hint{margin:16px 0 0;color:#8b96a3;font-size:12px;line-height:1.7}#error,#change-error{min-height:22px;margin-top:10px;color:#b42318;font-size:13px}#change-success{min-height:20px;margin-top:10px;color:#247560;font-size:13px}#change-panel[hidden],#login-panel[hidden]{display:none!important}.back{display:inline-flex;align-items:center;gap:6px;margin:0 0 24px;padding:0;border:0;background:none;color:#6d7a88;font-family:inherit;font-size:13px;cursor:pointer}.back:hover{color:#1d2938}.secure{margin-top:28px;padding-top:18px;border-top:1px solid #edf0f3;color:#929ca8;font-size:12px}.secure span{color:#4e9b88}.panel-heading{margin-bottom:22px}@media(max-width:760px){body{padding:14px;place-items:center}.shell{max-width:500px;min-height:0;grid-template-columns:1fr;border-radius:24px}.hero{min-height:220px;padding:25px 26px 20px}.hero-copy{margin:25px 0 22px}.hero h1{font-size:34px}.hero-copy p{margin-top:10px;font-size:14px}.hero-foot{display:none}.hero:after{width:270px;height:270px;right:-150px;bottom:-190px}.panel{padding:32px 26px 30px}.panel-top{margin-bottom:25px}}
@media(max-height:720px) and (min-width:761px){body{place-items:start center}.shell{min-height:calc(100vh - 40px)}.hero{padding:38px 50px}.panel{padding:30px 44px}.panel-top{margin-bottom:20px}.field{margin-top:13px}}

.hero-foot .linx-producer{display:inline-flex;align-items:center;gap:10px;opacity:.8}.hero-foot .linx-producer-art{display:block;width:104px;line-height:0}.hero-foot .linx-producer img{display:block;width:100%;height:auto}.hero-foot .linx-producer .producer-night{display:none}.hero-foot .linx-producer small{font-size:11px;color:#6c7d89;letter-spacing:.12em}@media(max-width:760px){.hero-foot{display:block;margin-top:3px}.hero-foot .linx-producer-art{width:72px}.hero-foot .linx-producer small{font-size:10px}}
</style></head><body><main class="shell"><section class="hero"><div class="brand"><span class="brand-icon" aria-label="LinX mark"><svg viewBox="0 0 64 64" aria-hidden="true"><path d="M10 13h7v31h16v7H10V13Z" fill="#fff"/><path d="M37 42 43 35l6 4 7-15" fill="none" stroke="#79c7b9" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="56" cy="24" r="2.5" fill="#79c7b9"/></svg></span><span>LinX Price</span></div><div class="hero-copy"><p class="eyebrow">Your monitoring space</p><h1>价格与余额，<br>清晰掌握。</h1><p>为常用 AI 服务整理价格、余额和历史记录。登录同一账号，即可在不同电脑间继续使用。</p></div><div class="hero-foot"><span class="linx-producer producer-login" aria-label="LinX 出品"><span class="linx-producer-art"><img class="producer-day" src="/assets/linx-v3-lockup_color.svg" alt="LinX" width="1240" height="380"><img class="producer-night" src="/assets/linx-v3-lockup_white.svg" alt="" aria-hidden="true" width="1240" height="380"></span><small>出品</small></span></div></section><section class="panel"><div class="panel-inner"><div id="login-panel"><div class="panel-top"><p class="eyebrow">Welcome back</p><h2>登录你的空间</h2><p class="lead">继续查看同步的站点与报告。</p></div><form id="login"><div class="field"><label for="username">账号名</label><input id="username" name="username" autocomplete="username" placeholder="输入账号名" required></div><div class="field"><label for="password">密码</label><div class="password-wrap"><input id="password" name="password" type="password" autocomplete="current-password" placeholder="输入密码" required><button class="reveal" type="button" data-reveal="password">显示</button></div></div><button class="primary" id="submit" type="submit">登录</button><div id="error" role="alert" aria-live="polite"></div></form><div class="links"><span class="hint" style="margin:0">数据安全保存在你的云服务器。</span><button class="text-button" type="button" id="show-change">修改账号 / 密码</button></div><div class="secure"><span>●</span> 安全连接 · 仅你授权的账号可访问</div></div><div id="change-panel" hidden><button class="back" type="button" id="back-login">← 返回登录</button><div class="panel-heading"><p class="eyebrow">Account security</p><h2>修改账号设置</h2><p class="lead">请先验证当前账号和密码。</p></div><form id="change-credentials"><div class="field"><label for="current-username">当前账号名</label><input id="current-username" autocomplete="username" required></div><div class="field"><label for="current-password">当前密码</label><input id="current-password" type="password" autocomplete="current-password" required></div><div class="field"><label for="new-username">新账号名 <span style="color:#8994a2;font-weight:400">（可留空）</span></label><input id="new-username" autocomplete="username" placeholder="留空则保持不变"></div><div class="field"><label for="new-password">新密码 <span style="color:#8994a2;font-weight:400">（可留空）</span></label><div class="password-wrap"><input id="new-password" type="password" autocomplete="new-password" placeholder="至少 8 个字符"><button class="reveal" type="button" data-reveal="new-password">显示</button></div></div><div class="field"><label for="confirm-password">确认新密码</label><input id="confirm-password" type="password" autocomplete="new-password" placeholder="再次输入新密码"></div><button class="primary" id="change-submit" type="submit">保存账号设置</button><div id="change-error" role="alert" aria-live="polite"></div><div id="change-success" role="status" aria-live="polite"></div></form><p class="hint">修改后，其他设备需要使用新账号信息重新登录。密码至少 8 个字符。</p></div></div></section></main><script>
const loginPanel=document.querySelector('#login-panel'),changePanel=document.querySelector('#change-panel');document.querySelector('#show-change').addEventListener('click',()=>{loginPanel.hidden=true;changePanel.hidden=false;document.querySelector('#current-username').focus()});document.querySelector('#back-login').addEventListener('click',()=>{changePanel.hidden=true;loginPanel.hidden=false});document.querySelectorAll('[data-reveal]').forEach(button=>button.addEventListener('click',()=>{const input=document.getElementById(button.dataset.reveal),visible=input.type==='password';input.type=visible?'text':'password';button.textContent=visible?'隐藏':'显示'}));document.querySelector('#login').addEventListener('submit',async event=>{event.preventDefault();const button=document.querySelector('#submit'),error=document.querySelector('#error');button.disabled=true;button.textContent='正在登录…';error.textContent='';try{const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.querySelector('#username').value,password:document.querySelector('#password').value})});const result=await response.json();if(!response.ok)throw Error(result.error||'登录失败，请检查账号和密码');location.replace('/')}catch(problem){error.textContent=problem.message||'暂时无法连接，请稍后重试'}finally{button.disabled=false;button.textContent='登录'}});document.querySelector('#change-credentials').addEventListener('submit',async event=>{event.preventDefault();const button=document.querySelector('#change-submit'),error=document.querySelector('#change-error'),success=document.querySelector('#change-success'),newPassword=document.querySelector('#new-password').value;error.textContent='';success.textContent='';if(newPassword&&newPassword!==document.querySelector('#confirm-password').value){error.textContent='两次输入的新密码不一致';return}button.disabled=true;button.textContent='正在保存…';try{const response=await fetch('/api/change-credentials',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.querySelector('#current-username').value,password:document.querySelector('#current-password').value,new_username:document.querySelector('#new-username').value,new_password:newPassword})});const result=await response.json();if(!response.ok)throw Error(result.error||'修改失败');success.textContent='账号设置已更新，正在进入工作空间…';location.replace('/')}catch(problem){error.textContent=problem.message||'修改失败，请稍后重试'}finally{button.disabled=false;button.textContent='保存账号设置'}});
</script></body></html>
"""
LOGOUT_WIDGET = ""

EDITOR_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#152b3c">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/assets/linx-mark.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/assets/linx-mark.svg">
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
    a.export-button { border-color: #0f9f8f; background: #e3f7f4; color: #087f73; }
    a.export-button:hover { border-color: #087f73; background: #c7efe9; color: #05665d; }
    a.local-sync-button { border-color: #6b8fd4; background: #eef4ff; color: #315ca8; }
    a.local-sync-button:hover { border-color: #315ca8; background: #dfeaff; color: #24498c; }
    button.logout-button { border-color: #c9362b; background: #c9362b; color: #fff; }
    button.logout-button:hover { border-color: #a82c24; background: #a82c24; color: #fff; }
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
    .legend-filter{width:auto;min-width:132px;height:30px;padding:0 8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);font:inherit;font-size:12px;font-weight:800}.legend-filter-button{height:30px;padding:0 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);font:inherit;font-size:12px;font-weight:800;cursor:pointer}.legend-filter-button.is-active{background:var(--accent);color:#fff;border-color:var(--accent)}
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
    /* 默认只展示核心字段，更多字段可用按钮展开。 */
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
  <link rel="stylesheet" href="/theme.css?v=20261105-producer-v3">
  <script src="/theme.js?v=20261105-unified"></script>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>AI 模型中转站编辑器</h1>
        <div class="muted">保存后同步更新 sites.json、SQLite、HTML 报告和 CSV。</div>
        <span id="status" class="status">加载中...</span><span class="linx-producer producer-inline" aria-label="LinX 出品"><span class="linx-producer-art"><img class="producer-day" src="/assets/linx-v3-lockup_color.svg" alt="LinX" width="1240" height="380"><img class="producer-night" src="/assets/linx-v3-lockup_white.svg" alt="" aria-hidden="true" width="1240" height="380"></span><small>出品</small></span>
      </div>
      <div class="actions editor-actions">
        <div class="action-group action-pages" role="group" aria-label="打开页面">
          <a id="open-report" class="button page-entry page-entry-report" href="/reports/latest.html" target="ai_price_monitor_report" rel="noopener"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 3h12v14H4zM7 13v-3m3 3V7m3 6V9"/></svg><span>打开报告</span><span class="page-entry-arrow" aria-hidden="true">↗</span></a>
          <a class="button purity-page-button page-entry page-entry-purity" href="/purity.html" target="ai_price_monitor_purity" rel="noopener" title="在新页面打开验纯分析"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 3h4m-3 0v5l-5 8h12l-5-8V3M7 12h6"/></svg><span>验纯分析</span><span class="page-entry-arrow" aria-hidden="true">↗</span></a>
          <a class="button page-entry page-entry-purity" href="/cache.html" target="ai_price_monitor_cache" rel="noopener" title="在新页面查看真实请求与缓存率"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 16h14M4 13l4-5 4 3 4-7"/></svg><span>用量与缓存</span><span class="page-entry-arrow" aria-hidden="true">↗</span></a>
        </div>
        <div class="action-group action-tools" role="group" aria-label="常用工具">
          <a id="open-calculator" class="button" href="/calculator.html" target="ai_price_monitor_calculator" rel="noopener">成本计算器</a>
          <button id="manual-checkin-filter" class="checkin-filter manual-checkin-filter" type="button" title="只显示手动签到站点">手动签到</button>
        </div>
        <div class="action-group action-data" role="group" aria-label="数据操作">
          <a id="export-data" class="button export-button" href="/api/export" download="linx-price-monitor-data.json" title="仅导出站点和签到数据，不包含账号密码">导出数据</a>
          <button id="sync-local-data" class="local-sync-button" type="button" title="将云端主体数据同步到本机编辑器">同步到本机</button>
          <button id="metapi-balance-sync" class="metapi-balance-sync" type="button" title="从 Metapi 连接管理同步余额">同步 Metapi 余额</button>
        </div>
        <div class="action-group action-edit" role="group" aria-label="编辑与保存">
          <button id="add" title="在表格末尾新增一个站点">新增一行</button>
          <button id="save" class="primary" title="保存编辑内容并重新生成报告">保存并同步</button>
        </div>
        <div class="action-group action-settings" role="group" aria-label="显示与账户">
          <div data-theme-control></div>
          <button id="logout-button" class="logout-button" type="button" onclick="fetch('/api/logout',{method:'POST'}).finally(()=>location.replace('/login'))">退出登录</button>
        </div>
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
        <div class="metrics-legend" aria-label="筛选和状态图标说明">
          <select id="purity-filter" class="legend-filter"><option value="">验纯站点：全部</option><option value="__pure__">已验纯</option></select>
          <button id="reimbursement-filter" class="legend-filter-button" type="button" aria-pressed="false">可报销站</button>
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
              <th>余额1</th>
              <th>余额2</th>
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
  <script src="/purity-decisions.js" defer></script><script>
    const fields = ["name", "category", "usage_status", "purity_status", "purity_match", "purity_multiplier", "reimbursement_status", "reimbursement_notes", "welfare_rate", "plus_rate", "pro_rate", "signup_bonus", "daily_checkin_bonus", "checkin_mode", "balance", "balance_2", "recharge_ratio", "url", "invite_url", "notes"];
    const numeric = new Set(["welfare_rate", "plus_rate", "pro_rate", "signup_bonus", "balance", "balance_2"]);
    function purityMatchInput(value) {
      const text = String(value || "").trim().replace(/%$/, "");
      if (!text) return null;
      const number = Number(text);
      if (!Number.isFinite(number)) return null;
      if (number === 100) return 100;
      if (number > 100) return Number((number / 10).toFixed(4));
      if (number > 0 && number <= 1) return Number((number * 100).toFixed(4));
      return number;
    }
    function purityMultiplierInput(value) {
      const text = String(value || "").trim();
      if (!text) return null;
      if (/^0\d+$/.test(text)) return Number("0." + text.slice(1));
      const number = Number(text);
      if (!Number.isFinite(number)) return null;
      if (Number.isInteger(number) && number >= 10) return number / (10 ** String(number).length);
      return number;
    }
    function purityMatchDisplay(value) { const number = purityMatchInput(value); return number === null ? "" : String(number) + "%"; }
    function purityMultiplierDisplay(value) { const number = purityMultiplierInput(value); return number === null ? "" : String(number); }
    const tbody = document.querySelector("#tbody");
    const tableWrap = document.querySelector(".table-wrap");
    window.name = "ai_price_monitor_editor";
    if (typeof BroadcastChannel === "function") {
      const editorNavigation = new BroadcastChannel("ai-price-monitor:editor-navigation");
      editorNavigation.onmessage = function(event) {
        if (event.data && event.data.type === "focus-editor") {
          window.focus();
          editorNavigation.postMessage({type: "editor-focused", token: event.data.token});
        }
      };
    }
    const metricsLegend = document.querySelector(".metrics-legend");
    const purityFilter = document.querySelector("#purity-filter");
    const reimbursementFilter = document.querySelector("#reimbursement-filter");
    let purityFilterMode = "";
    let reimbursementFilterMode = false;
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
    let signedCheckinKeys = new Set();
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
      const purityModels = Array.isArray(row.purity_models) ? row.purity_models : [];
      if (purityFilterMode === "__pure__" && !purityModels.length && row.purity_match == null) return false;
      if (purityFilterMode && purityFilterMode !== "__pure__" && !purityModels.includes(purityFilterMode)) return false;
      if (reimbursementFilterMode && fmt(row.reimbursement_status) !== "可报销") return false;
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

    function checkinSiteKey(row) {
      return fmt(row.url) || fmt(row.name);
    }

    function hasCheckin(row) {
      return fmt(row.daily_checkin_bonus) !== "";
    }

    function checkinMode(row) {
      if (!hasCheckin(row)) return "无签到";
      return fmt(row.checkin_mode) === "手动" ? "手动" : "自动";
    }

    function isSignedToday(row) {
      return hasCheckin(row) && signedCheckinKeys.has(checkinSiteKey(row));
    }

    function updateMetrics() {
      const rates = rows.map(lowest).filter((value) => value !== "").map(Number);
      const best = rates.length ? Math.min(...rates) : "";
      const balanceRows = rows.filter((row) => Number(row.balance || 0) + Number(row.balance_2 || 0) > 0);
      const checkinStations = rows.filter(hasCheckin);
      const checkinRows = checkinStations.filter((row) => !isSignedToday(row));
      const manualCheckinStations = checkinStations.filter((row) => checkinMode(row) === "手动");
      const totalBalance = rows.reduce((sum, row) => sum + Number(row.balance || 0) + Number(row.balance_2 || 0), 0);
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
      if (Number(row.balance || 0) + Number(row.balance_2 || 0) > 0) classes.push("has-balance");
      if (Number(row.balance || 0) + Number(row.balance_2 || 0) < 0) classes.push("has-debt");
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
          <td><input class="number" data-field="balance_2" value="${escapeHtml(fmt(row.balance_2))}"></td>
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
        const purityMatchEl = tr.querySelector('input[data-field="purity_match"]');
        const purityMultiplierEl = tr.querySelector('input[data-field="purity_multiplier"]');
        if (purityMatchEl) purityMatchEl.value = purityMatchDisplay(row.purity_match);
        if (purityMultiplierEl) purityMultiplierEl.value = purityMultiplierDisplay(row.purity_multiplier);
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
      } else if (field === "purity_match") {
        rows[index][field] = purityMatchInput(input.value);
      } else if (field === "purity_multiplier") {
        rows[index][field] = purityMultiplierInput(input.value);
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

    tbody.addEventListener("click", async (event) => {
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

    tbody.addEventListener("click", async (event) => {
      const checkinButton = event.target.closest("button[data-checkin-toggle]");
      if (checkinButton) {
        const row = rows[Number(checkinButton.dataset.checkinToggle)];
        if (!row || !hasCheckin(row)) return;
        const siteKey = checkinSiteKey(row);
        const wasSigned = signedCheckinKeys.has(siteKey);
        checkinButton.disabled = true;
        try {
          const response = await fetch("/api/checkins", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({date: todayString(), site_key: siteKey, checked: !wasSigned})});
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || "签到状态保存失败");
          if (wasSigned) { signedCheckinKeys.delete(siteKey); } else { signedCheckinKeys.add(siteKey); }
        } catch (error) {
          checkinButton.disabled = false;
          setStatus(error.message || "签到状态保存失败", "error");
          return;
        }
        render();
        if (wasSigned) {
          setStatus("已恢复“" + (fmt(row.name) || "该站点") + "”为待签到", "ok");
          showToast("已取消今日签到标记");
        } else {
          setStatus("已标记“" + (fmt(row.name) || "该站点") + "”今日已签到", "ok");
          showToast("已标记今日签到");
        }
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
      const row = {name: "", category: "收费站", usage_status: "", url: "", balance: null, balance_2: null, recharge_ratio: "1:1", welfare_rate: null, plus_rate: null, pro_rate: null, signup_bonus: null, daily_checkin_bonus: null, checkin_mode: "无签到", notes: "", invite_url: ""};
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
      const reportWindow = window.open("/reports/latest.html", "ai_price_monitor_report");
      if (!reportWindow) return;
      event.preventDefault();
      reportWindow.focus();
    });

    document.querySelector("#open-calculator").addEventListener("click", (event) => {
      const calculatorWindow = window.open("/calculator.html", "ai_price_monitor_calculator");
      if (!calculatorWindow) return;
      event.preventDefault();
      calculatorWindow.focus();
    });

    document.querySelector("#sync-local-data").addEventListener("click", async () => {
      const button = document.querySelector("#sync-local-data");
      const original = button.textContent;
      button.disabled = true;
      button.textContent = "正在同步…";
      try {
        const linkResponse = await fetch("/api/export-link");
        const link = await linkResponse.json();
        if (!linkResponse.ok || !link.url) throw new Error(link.error || "无法创建云端迁移链接");
        const localResponse = await fetch("http://127.0.0.1:8765/api/import-from-cloud", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({source_url: link.url}),
        });
        const result = await localResponse.json();
        if (!localResponse.ok) throw new Error(result.error || "本机同步失败");
        setStatus("本机已同步 " + result.count + " 个站点", "ok");
        showToast("本机已同步 " + result.count + " 个站点，请刷新本地编辑器", "ok");
      } catch (error) {
        const message = error.message || "本机同步失败";
        setStatus(message, "error");
        showToast("本机同步失败：请先启动本地编辑器，再重试", "error");
      } finally {
        button.disabled = false;
        button.textContent = original;
      }
    });

    function handleSaveShortcut(event) {
      if ((event.ctrlKey || event.metaKey) && (event.key === "s" || event.key === "S" || event.code === "KeyS")) {
        event.preventDefault();
        event.stopPropagation();
        const saveButton = document.querySelector("#save");
        if (saveButton && !saveButton.disabled) saveButton.click();
        return false;
      }
    }
    window.addEventListener("keydown", handleSaveShortcut, true);
    window.addEventListener("keypress", handleSaveShortcut, true);
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
        const message = error instanceof TypeError
          ? "无法连接编辑器服务，请双击桌面的「价格监控编辑器」重新启动后再试。"
          : error.message;
        setStatus(message, "error");
      }
    });

    function updatePurityFilterOptions(){if(!purityFilter)return;const models=[...new Set(rows.flatMap(r=>Array.isArray(r.purity_models)?r.purity_models:[]))].sort();purityFilter.innerHTML='<option value="">验纯站点：全部</option><option value="__pure__">已验纯</option>'+models.map(m=>'<option value="'+escapeHtml(m)+'">'+escapeHtml(m)+'</option>').join('');}
    if(purityFilter)purityFilter.addEventListener("change",()=>{purityFilterMode=purityFilter.value;render()});
    if(reimbursementFilter)reimbursementFilter.addEventListener("click",()=>{reimbursementFilterMode=!reimbursementFilterMode;reimbursementFilter.classList.toggle("is-active",reimbursementFilterMode);render()});

    async function load() {
      setStatus("正在加载站点…");
      try {
        const response = await fetch("/api/sites");
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "加载站点失败");
        rows = Array.isArray(payload.sites) ? payload.sites : [];
        updatePurityFilterOptions();
        signedCheckinKeys = new Set();
        render();
        setStatus("已加载 " + rows.length + " 个站点", "ok");
      } catch (error) {
        setStatus(error.message || "站点加载失败，请刷新重试", "error");
        showToast(error.message || "站点加载失败", "error");
        return;
      }
      try {
        const response = await fetch("/api/checkins?date=" + encodeURIComponent(todayString()));
        const payload = await response.json();
        if (response.ok) signedCheckinKeys = new Set(payload.signed_keys || []);
      } catch (_) {}
      try {
        const legacyKeys = rows.filter((row) => hasCheckin(row) && window.localStorage.getItem(checkinStorageKey(row)) === "1").map(checkinSiteKey).filter((key) => !signedCheckinKeys.has(key));
        if (legacyKeys.length) {
          const results = await Promise.allSettled(legacyKeys.map((siteKey) => fetch("/api/checkins", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({date: todayString(), site_key: siteKey, checked: true})})));
          results.forEach((result, index) => { if (result.status === "fulfilled" && result.value.ok) signedCheckinKeys.add(legacyKeys[index]); });
        }
      } catch (_) {}
      render();
    }

    load();

    window.setInterval(async () => {
      try {
        const response = await fetch("/api/checkins?date=" + encodeURIComponent(todayString()));
        const payload = await response.json();
        if (!response.ok) return;
        const next = new Set(payload.signed_keys || []);
        if (next.size !== signedCheckinKeys.size || [...next].some((key) => !signedCheckinKeys.has(key))) {
          signedCheckinKeys = next;
          render();
        }
      } catch (_) {}
    }, 15000);
  </script>
</body>
</html>
"""


def main() -> int:
    build_reports(load_sites())
    url = f"http://{HOST}:{PORT}/"
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Editor: {url}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
