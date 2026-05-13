# ── Config ─────────────────────────────────────────────────────────────────────
# BASE_URL = "https://cbl.salesflo.com/OB/login/"
# USERNAME = "bazaar"   # <- Salesflo login
# PASSWORD = "Cblbazaar@235"   # <- Salesflo login
"""
CBL Salesflo — Load Form Details Bot
=====================================
Navigation:
  Login → Reports → Sale Reports → Load Form Summary Metrics
  Settings: QTY=Carton, Type=Load Form Details, Show Stores Details=✔, Date Era=Current

Report columns (confirmed from live data):
  S/No | Distributor | Order Booker | Deliveryman | Load Form # | Load Form Status |
  Invoice # | Store Code | Store Name | Locality Name | Sub Locality Name |
  Channel Type Name | Channel Name | Sub Channel Name | PJP # |
  SKU Code | SKU Name | Issued | Return | Sales | Discount | Return Amount | Net Sales

Requirements:
    pip install playwright aiomysql python-dotenv
    playwright install chromium

Usage:
    python cbl_loadform_details.py                            # smart date (yesterday or catch-up)
    python cbl_loadform_details.py --start 2026-05-01 --end 2026-05-12
    python cbl_loadform_details.py --start 2026-05-01 --end 2026-05-12 --force-refresh
"""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, date as date_type
from typing import Optional

import aiomysql
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Load .env ──────────────────────────────────────────────────────────────────
load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
_fh = logging.FileHandler("cbl_loadform_details.log", encoding="utf-8")
_sh = logging.StreamHandler(sys.stdout)
if hasattr(_sh.stream, "reconfigure"):
    _sh.stream.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[_fh, _sh],
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

BASE_URL = "https://cbl.salesflo.com/OB/login/"

SALESFLO_USERNAME = os.getenv("SALESFLO_USERNAME", "bazaar")
SALESFLO_PASSWORD = os.getenv("SALESFLO_PASSWORD", "Cblbazaar@235")

DB_HOST = os.getenv("DB_HOST", "db42280.public.databaseasp.net")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "db42280")
DB_PASS = os.getenv("DB_PASS", "admin2233")
DB_NAME = os.getenv("DB_NAME", "db42280")

TABLE_DATA   = "loadform_details"
TABLE_RUNLOG = "cbl_bot_run_log"

REPORT_WAIT_SECONDS    = int(os.getenv("REPORT_WAIT_SECONDS", "300"))
REPORT_PAGE_TIMEOUT_MS = int(os.getenv("REPORT_PAGE_TIMEOUT_MS", "120000"))

# ── Column mapping ─────────────────────────────────────────────────────────────

HEADER_MAP: dict[str, str] = {
    "s/no": "s_no", "s.no": "s_no", "s#": "s_no", "sno": "s_no", "s.no#": "s_no",
    "distributor":       "distributor",
    "order booker":      "order_booker",
    "deliveryman":       "deliveryman",
    "load form #":       "load_form_no",
    "load form no":      "load_form_no",
    "load form number":  "load_form_no",
    "load form status":  "load_form_status",
    "invoice #":         "invoice_no",
    "invoice no":        "invoice_no",
    "invoice number":    "invoice_no",
    "store code":        "store_code",
    "store name":        "store_name",
    "locality name":     "locality_name",
    "sub locality name": "sub_locality_name",
    "channel type name": "channel_type_name",
    "channel type":      "channel_type_name",
    "channel name":      "channel_name",
    "sub channel name":  "sub_channel_name",
    "pjp #":             "pjp_no",
    "pjp no":            "pjp_no",
    "pjp":               "pjp_no",
    "sku code":          "sku_code",
    "sku name":          "sku_name",
    "issued":            "issued_cartons",
    "return":            "return_cartons",
    "sales":             "sales_cartons",
    "discount":          "discount",
    "return amount":     "return_amount",
    "net sales":         "net_sales",
}

KNOWN_HEADERS = [
    "S/No", "Distributor", "Order Booker", "Deliveryman",
    "Load Form #", "Load Form Status", "Invoice #",
    "Store Code", "Store Name", "Locality Name", "Sub Locality Name",
    "Channel Type Name", "Channel Name", "Sub Channel Name",
    "PJP #", "SKU Code", "SKU Name",
    "Issued", "Return", "Sales", "Discount", "Return Amount", "Net Sales",
]

DECIMAL_COLS = {
    "issued_cartons", "return_cartons", "sales_cartons",
    "discount", "return_amount", "net_sales",
}

DB_COLUMNS = [
    "start_date", "end_date", "created_at",
    "s_no", "distributor", "order_booker", "deliveryman",
    "load_form_no", "load_form_status", "invoice_no",
    "store_code", "store_name", "locality_name", "sub_locality_name",
    "channel_type_name", "channel_name", "sub_channel_name",
    "pjp_no", "sku_code", "sku_name",
    "issued_cartons", "return_cartons", "sales_cartons",
    "discount", "return_amount", "net_sales",
    "extra_data",
]


# ══════════════════════════════════════════════════════════════════════════════
# MYSQL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def get_db():
    return await aiomysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        db=DB_NAME, charset="utf8mb4",
        autocommit=True,
    )


async def ensure_tables(conn) -> None:
    async with conn.cursor() as cur:
        await cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{TABLE_DATA}` (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                start_date          DATE,
                end_date            DATE,
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
                s_no                VARCHAR(20),
                distributor         VARCHAR(255),
                order_booker        VARCHAR(255),
                deliveryman         VARCHAR(255),
                load_form_no        VARCHAR(100),
                load_form_status    VARCHAR(100),
                invoice_no          VARCHAR(100),
                store_code          VARCHAR(50),
                store_name          VARCHAR(255),
                locality_name       VARCHAR(255),
                sub_locality_name   VARCHAR(255),
                channel_type_name   VARCHAR(100),
                channel_name        VARCHAR(255),
                sub_channel_name    VARCHAR(255),
                pjp_no              VARCHAR(255),
                sku_code            VARCHAR(50),
                sku_name            VARCHAR(500),
                issued_cartons      DECIMAL(18,4),
                return_cartons      DECIMAL(18,4),
                sales_cartons       DECIMAL(18,4),
                discount            DECIMAL(18,4),
                return_amount       DECIMAL(18,4),
                net_sales           DECIMAL(18,4),
                extra_data          TEXT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        await cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{TABLE_RUNLOG}` (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                run_date      DATE NOT NULL,
                status        ENUM('success','failed','no_data') NOT NULL,
                rows_saved    INT DEFAULT 0,
                rows_deleted  INT DEFAULT 0,
                period_start  DATE NULL,
                period_end    DATE NULL,
                action_type   VARCHAR(50) DEFAULT 'run',
                message       TEXT,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    log.info("MySQL tables ready: %s, %s", TABLE_DATA, TABLE_RUNLOG)


async def get_last_saved_date(conn) -> Optional[date_type]:
    async with conn.cursor() as cur:
        await cur.execute(f"SELECT MAX(end_date) FROM `{TABLE_DATA}`")
        row = await cur.fetchone()
        return row[0] if row and row[0] else None


async def log_run(
    conn, run_date, status: str, rows_saved: int = 0, message: str = "", *,
    rows_deleted: int = 0, period_start=None, period_end=None, action_type: str = "run",
):
    async with conn.cursor() as cur:
        await cur.execute(
            f"""INSERT INTO `{TABLE_RUNLOG}`
                (run_date, status, rows_saved, rows_deleted,
                 period_start, period_end, action_type, message)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run_date, status, rows_saved, rows_deleted,
             period_start, period_end, action_type, message),
        )


async def delete_rows_for_period(conn, start_date: date_type, end_date: date_type) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            f"DELETE FROM `{TABLE_DATA}` WHERE start_date=%s AND end_date=%s",
            (start_date, end_date),
        )
        return int(cur.rowcount or 0)


def _safe_decimal(v) -> Optional[float]:
    try:
        cleaned = re.sub(r"[^\d.\-]", "", str(v))
        return float(cleaned) if cleaned and cleaned not in ("", "-", ".") else None
    except Exception:
        return None


def _norm_row(raw_row: dict) -> tuple[dict, dict]:
    mapped: dict = {}
    extras: dict = {}
    for raw_key, val in raw_row.items():
        snake = HEADER_MAP.get(raw_key.strip().lower())
        if snake:
            mapped[snake] = str(val).strip() if val is not None else ""
        else:
            auto = re.sub(r"[^a-z0-9]+", "_", raw_key.strip().lower()).strip("_")
            extras[auto] = str(val).strip() if val is not None else ""
    return mapped, extras


async def save_rows(
    conn, rows: list[dict], start_date: date_type, end_date: date_type
) -> int:
    if not rows:
        return 0
    col_sql = ", ".join(f"`{c}`" for c in DB_COLUMNS)
    val_sql = ", ".join(["%s"] * len(DB_COLUMNS))
    insert  = f"INSERT INTO `{TABLE_DATA}` ({col_sql}) VALUES ({val_sql})"
    data = []
    for raw_row in rows:
        mapped, extras = _norm_row(raw_row)
        def g(col):
            v = mapped.get(col, "")
            return _safe_decimal(v) if col in DECIMAL_COLS else (v or None)
        data.append((
            start_date, end_date, datetime.now(),
            g("s_no"), g("distributor"), g("order_booker"), g("deliveryman"),
            g("load_form_no"), g("load_form_status"), g("invoice_no"),
            g("store_code"), g("store_name"), g("locality_name"), g("sub_locality_name"),
            g("channel_type_name"), g("channel_name"), g("sub_channel_name"),
            g("pjp_no"), g("sku_code"), g("sku_name"),
            g("issued_cartons"), g("return_cartons"), g("sales_cartons"),
            g("discount"), g("return_amount"), g("net_sales"),
            json.dumps(extras) if extras else None,
        ))
    async with conn.cursor() as cur:
        await cur.executemany(insert, data)
    log.info("Inserted %d rows into '%s'.", len(data), TABLE_DATA)
    return len(data)


# ══════════════════════════════════════════════════════════════════════════════
# BROWSER HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _all_frames(page):
    return [page] + list(page.frames)


async def _find_first(page, selectors: list[str], timeout_s: float = 15.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for root in _all_frames(page):
            for sel in selectors:
                try:
                    loc = root.locator(sel).first
                    if await loc.count() > 0:
                        return loc
                except Exception:
                    continue
        await asyncio.sleep(0.4)
    return None


async def _fast_fill(target, value: str):
    try:
        await target.fill(value, timeout=3000)
        return
    except Exception:
        pass
    await target.evaluate(
        """(el, val) => {
            el.removeAttribute('readonly'); el.readOnly = false; el.disabled = false;
            el.focus(); el.value = val;
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        value,
    )


async def _js_click_text(page, texts: list[str]) -> Optional[str]:
    """
    Click the first element whose text matches any of `texts`.
    Searches main page + all iframes. Returns outerHTML snippet or None.
    """
    js = """
        (texts) => {
            const els = Array.from(document.querySelectorAll(
                'a, li, span, div, td, button, input, ul'
            ));
            for (const t of texts) {
                const tl = t.toLowerCase();
                const match = els.find(el => {
                    const txt = (el.innerText || el.textContent || '').trim().toLowerCase();
                    return txt === tl || txt.includes(tl);
                });
                if (match) { match.click(); return match.outerHTML.slice(0, 200); }
            }
            return null;
        }
    """
    for frame in _all_frames(page):
        try:
            result = await frame.evaluate(js, texts)
            if result:
                return result
        except Exception:
            continue
    return None


async def _wait_for_text_in_dom(page, texts: list[str], timeout_s: float = 30.0) -> bool:
    """Return True as soon as any of `texts` appears anywhere in the DOM."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for frame in _all_frames(page):
            try:
                found = await frame.evaluate("""
                    (texts) => texts.some(t =>
                        document.body?.innerText?.toLowerCase().includes(t.toLowerCase())
                    )
                """, texts)
                if found:
                    return True
            except Exception:
                continue
        await asyncio.sleep(0.5)
    return False


# ══════════════════════════════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════════════════════════════

async def login(page, username: str, password: str) -> None:
    log.info("Navigating to login: %s", BASE_URL)

    # ── Load page (3 attempts, 90s each, domcontentloaded then commit) ────────
    loaded = False
    for attempt in range(1, 4):
        for wait_until in ("domcontentloaded", "commit"):
            try:
                await page.goto(BASE_URL, wait_until=wait_until, timeout=90_000)
                loaded = True
                log.info("  Page loaded (attempt %d, wait=%s).", attempt, wait_until)
                break
            except PlaywrightTimeout:
                log.warning("  Timeout (attempt %d, wait=%s). Retrying...", attempt, wait_until)
                await asyncio.sleep(3)
            except Exception as exc:
                log.warning("  Nav error (attempt %d): %s", attempt, exc)
                await asyncio.sleep(3)
        if loaded:
            break

    if not loaded:
        raise RuntimeError("Could not load login page after 3 attempts.")

    await asyncio.sleep(4)   # let JS render the form

    # ── Find inputs (60s window, all frames) ──────────────────────────────────
    USER_SEL = [
        'input[name="username"]', 'input#username',
        'input[name="email"]',    'input[type="text"]',
        'input[name*="user" i]',  'input[id*="user" i]',
    ]
    PASS_SEL = [
        'input[name="password"]', 'input#password',
        'input[type="password"]', 'input[name*="pass" i]',
    ]

    user_inp = await _find_first(page, USER_SEL, timeout_s=60.0)
    pass_inp = await _find_first(page, PASS_SEL, timeout_s=60.0)

    if not user_inp or not pass_inp:
        # Check if already logged in
        if await _wait_for_text_in_dom(page, ["Reports", "Dashboard"], timeout_s=5):
            log.info("Already authenticated — skipping login.")
            return
        # Hard reload and retry
        log.warning("Inputs not found after 60s — reloading...")
        try:
            await page.reload(wait_until="domcontentloaded", timeout=90_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(5)
        user_inp = await _find_first(page, USER_SEL, timeout_s=30.0)
        pass_inp = await _find_first(page, PASS_SEL, timeout_s=30.0)

    if not user_inp or not pass_inp:
        raise RuntimeError(
            f"Login form inputs not found. URL={page.url}"
        )

    # ── Fill credentials ───────────────────────────────────────────────────────
    await _fast_fill(user_inp, username)
    await asyncio.sleep(0.3)
    await _fast_fill(pass_inp, password)
    await asyncio.sleep(0.3)

    # ── Submit ─────────────────────────────────────────────────────────────────
    clicked = False
    for sel in [
        'input[type="image"][src*="btnLogin"]',
        'input[type="image"]',
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Login")',
        'button:has-text("Sign In")',
        'input[value*="Login" i]',
        'input[value*="Sign" i]',
    ]:
        try:
            for root in _all_frames(page):
                loc = root.locator(sel).first
                if await loc.count() > 0:
                    await loc.click(timeout=5000)
                    clicked = True
                    break
        except Exception:
            continue
        if clicked:
            break

    if not clicked:
        try:
            await pass_inp.press("Enter")
            clicked = True
        except Exception:
            pass

    if not clicked:
        await page.evaluate("""
            () => { const f = document.querySelector('form');
                    if (f) { if (f.requestSubmit) f.requestSubmit(); else f.submit(); } }
        """)

    # ── Wait for dashboard (up to 90s) ─────────────────────────────────────────
    log.info("  Waiting for dashboard after login...")
    dashboard = await _wait_for_text_in_dom(
        page, ["Reports", "Dashboard", "Sale Reports"], timeout_s=90.0
    )
    if not dashboard:
        try:
            await page.wait_for_load_state("networkidle", timeout=30_000)
        except PlaywrightTimeout:
            pass

    await asyncio.sleep(3)
    log.info("Logged in successfully. Current URL: %s", page.url)


# ══════════════════════════════════════════════════════════════════════════════
# NAVIGATION
# ══════════════════════════════════════════════════════════════════════════════

async def navigate_to_loadform(page) -> None:
    log.info("Navigating: Reports → Sale Reports → Load Form Summary Metrics")

    # After login the page may still be redirecting — wait for it to settle
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        pass
    await asyncio.sleep(3)

    # Confirm we are on the dashboard (not still on login page)
    if "login" in page.url.lower():
        log.warning("Still on login page after navigation wait — waiting more...")
        await _wait_for_text_in_dom(page, ["Reports", "Dashboard"], timeout_s=30.0)
        await asyncio.sleep(2)

    # ── Step 1: Reports top-level menu ───────────────────────────────────────
    log.info("Step 1: Clicking Reports menu...")
    for attempt in range(25):
        result = await _js_click_text(page, ["Reports"])
        if result:
            log.info("  Reports clicked: %s", result[:80])
            break
        if attempt % 5 == 0:
            log.info("  Reports not found (attempt %d/25), waiting 2s...", attempt + 1)
        await asyncio.sleep(2)
    else:
        raise RuntimeError("Could not click Reports menu after 50s.")

    # Wait for submenu to animate open
    await asyncio.sleep(3)
    sale_visible = await _wait_for_text_in_dom(
        page, ["Sale Reports", "Sales Reports"], timeout_s=10.0
    )
    if not sale_visible:
        log.warning("  Submenu may not have opened — trying Reports click again...")
        await _js_click_text(page, ["Reports"])
        await asyncio.sleep(3)

    # ── Step 2: Sale Reports submenu ─────────────────────────────────────────
    log.info("Step 2: Clicking Sale Reports...")
    sale_texts = ["Sale Reports", "Sales Reports"]
    for attempt in range(25):
        # Re-click Reports every 5 failed attempts to reopen submenu
        if attempt > 0 and attempt % 5 == 0:
            log.info("  Re-clicking Reports to reopen submenu...")
            await _js_click_text(page, ["Reports"])
            await asyncio.sleep(2)

        result = await _js_click_text(page, sale_texts)
        if result:
            log.info("  Sale Reports clicked: %s", result[:80])
            break
        await asyncio.sleep(2)
    else:
        raise RuntimeError("Could not click Sale Reports after 50s.")

    await asyncio.sleep(5)

    # ── Step 3: Load Form Summary Metrics tile ────────────────────────────────
    log.info("Step 3: Clicking Load Form Summary Metrics tile...")
    tile_texts = [
        "Load Form Summary Metrics",
        "Load Form Summary",
        "LoadForm Summary Metrics",
    ]
    for attempt in range(20):
        result = await _js_click_text(page, tile_texts)
        if result:
            log.info("  Tile clicked: %s", result[:120])
            break
        log.info("  Tile not visible (attempt %d/20)...", attempt + 1)
        await asyncio.sleep(2)
    else:
        raise RuntimeError("Could not find 'Load Form Summary Metrics' tile.")

    await asyncio.sleep(5)

    # ── Step 4: Confirm panel / View Report button ────────────────────────────
    log.info("Step 4: Confirming report panel...")
    panel_texts = ["Load Form Summary Metrics", "Load Form Summary", "Load Form Details"]
    for _ in range(40):
        try:
            if await page.evaluate("""
                (texts) => Array.from(document.querySelectorAll(
                    '.tab-content,.panel,.report-panel,div,h3,h4,span,a,td'
                )).some(el => {
                    const t = (el.innerText || el.textContent || '').trim();
                    return texts.some(pt => t.includes(pt)) && el.offsetParent !== null;
                })
            """, panel_texts):
                log.info("  Report panel confirmed.")
                break
        except Exception:
            pass
        vr = await _find_first(page, [
            "input[value='View Report']",
            "button:has-text('View Report')",
        ], timeout_s=0.5)
        if vr:
            log.info("  View Report button found — panel ready.")
            break
        await asyncio.sleep(1)
    else:
        raise RuntimeError("Load Form report panel never appeared.")

    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeout:
        pass
    log.info("Load Form report panel ready.")


# ══════════════════════════════════════════════════════════════════════════════
# SET REPORT OPTIONS
# ══════════════════════════════════════════════════════════════════════════════

async def set_report_options(page, start_date: date_type, end_date: date_type) -> None:
    log.info("Setting report options...")

    async def _js_set(js: str, label: str):
        result = await page.evaluate(js)
        log.info("  %s → %s", label, result)

    await _js_set("""
        () => {
            for (const inp of document.querySelectorAll('input[type=checkbox],input[type=radio]')) {
                const lbl = (inp.labels?.[0]?.innerText || inp.parentElement?.innerText || '').toLowerCase();
                if (lbl.includes('carton')) { if (!inp.checked) inp.click(); return 'ok'; }
            }
            return 'not found';
        }
    """, "QTY Type → Carton")

    await _js_set("""
        () => {
            for (const inp of document.querySelectorAll('input[type=radio]')) {
                const lbl = (inp.labels?.[0]?.innerText || inp.parentElement?.innerText || '').toLowerCase();
                if (lbl.includes('load form details')) { if (!inp.checked) inp.click(); return 'ok'; }
            }
            return 'not found';
        }
    """, "Type Of Report → Load Form Details")

    await _js_set("""
        () => {
            for (const inp of document.querySelectorAll('input[type=checkbox]')) {
                const lbl = (inp.labels?.[0]?.innerText || inp.parentElement?.innerText || '').toLowerCase();
                if (lbl.includes('show stores details') || lbl.includes('store details')) {
                    if (!inp.checked) inp.click(); return 'ok';
                }
            }
            return 'not found';
        }
    """, "Show Stores Details → checked")

    await _js_set("""
        () => {
            for (const inp of document.querySelectorAll('input[type=radio]')) {
                const lbl = (inp.labels?.[0]?.innerText || inp.parentElement?.innerText || '').toLowerCase();
                if (lbl.includes('current') && !lbl.includes('history')) {
                    if (!inp.checked) inp.click(); return 'ok';
                }
            }
            return 'not found';
        }
    """, "Date Era → Current")

    await asyncio.sleep(1)

    # Delivery dates (format "May 12, 2026")
    start_str = start_date.strftime("%B %-d, %Y")
    end_str   = end_date.strftime("%B %-d, %Y")

    async def _fill_date(selectors, value, label):
        target = await _find_first(page, selectors, timeout_s=15.0)
        if not target:
            log.warning("  %s field not found.", label)
            return
        await _fast_fill(target, value)
        await target.press("Tab")
        log.info("  %s: %s", label, value)

    await _fill_date([
        'input[id*="FromDate" i]', 'input[id*="from_date" i]',
        'input[id*="DeliveryFrom" i]', 'input[id*="dt1" i]',
        'input[placeholder*="From" i]', 'input[name*="from" i]',
        'input[id*="startdate" i]',
    ], start_str, "Delivery From Date")

    await _fill_date([
        'input[id*="ToDate" i]', 'input[id*="to_date" i]',
        'input[id*="DeliveryTo" i]', 'input[id*="dt2" i]',
        'input[placeholder*="To" i]', 'input[name*="to" i]',
        'input[id*="enddate" i]',
    ], end_str, "Delivery To Date")

    await asyncio.sleep(1)
    log.info("Report options set.")


# ══════════════════════════════════════════════════════════════════════════════
# CLICK VIEW REPORT → GENERATE
# ══════════════════════════════════════════════════════════════════════════════

async def click_view_and_generate(page):
    log.info("Clicking View Report...")
    existing_pages = list(page.context.pages)
    panel_texts = ["Load Form Summary Metrics", "Load Form Summary", "Load Form Details"]

    # Try to click View Report that is inside the Load Form panel
    clicked = False
    for attempt in range(1, 4):
        clicked = await page.evaluate("""
            (pTexts) => {
                for (const el of document.querySelectorAll(
                        'input[type=button],input[type=submit],button,a')) {
                    const val = (el.value || el.innerText || el.textContent || '').trim();
                    if (val !== 'View Report') continue;
                    let anc = el.parentElement, d = 0;
                    while (anc && d < 20) {
                        if (pTexts.some(pt =>
                                (anc.innerText || anc.textContent || '').includes(pt))) {
                            el.click(); return true;
                        }
                        anc = anc.parentElement; d++;
                    }
                }
                return false;
            }
        """, panel_texts)
        if clicked:
            log.info("  'View Report' clicked (attempt %d).", attempt)
            break
        log.warning("  View Report not inside panel (attempt %d). Retrying...", attempt)
        await asyncio.sleep(2)

    if not clicked:
        vr = await _find_first(page, ["input[value='View Report']"], timeout_s=5)
        if vr:
            await vr.click(timeout=8000)
            clicked = True
            log.info("  'View Report' clicked via fallback selector.")

    if not clicked:
        raise RuntimeError("Could not click 'View Report'.")

    # Handle optional Generate modal
    modal_visible = False
    try:
        await page.wait_for_selector("text=Please Select Report Method", timeout=8000)
        modal_visible = True
    except PlaywrightTimeout:
        pass

    report_page = page
    if modal_visible:
        log.info("  Generate modal detected — clicking Generate.")
        gen = page.locator(
            "button:has-text('Generate'), a:has-text('Generate'), input[value='Generate']"
        ).first
        try:
            async with page.context.expect_page(timeout=20000) as pi:
                await gen.click(timeout=5000)
            report_page = await pi.value
            log.info("  New tab opened after Generate.")
        except PlaywrightTimeout:
            await gen.click(timeout=5000)
            await asyncio.sleep(3)
    else:
        log.info("  No modal — watching for new tab...")
        for _ in range(30):
            new_pages = [p for p in page.context.pages if p not in existing_pages]
            if new_pages:
                report_page = new_pages[-1]
                log.info("  New tab detected.")
                break
            await asyncio.sleep(0.5)

    try:
        await report_page.wait_for_load_state("domcontentloaded",
                                               timeout=REPORT_PAGE_TIMEOUT_MS)
    except PlaywrightTimeout:
        log.warning("  domcontentloaded timed out — continuing.")

    return report_page


# ══════════════════════════════════════════════════════════════════════════════
# PARSE TABLE
# ══════════════════════════════════════════════════════════════════════════════

async def _read_table_payload(root) -> dict:
    try:
        return await root.evaluate(r"""
            () => {
                const clean = (v) => (v||'').replace(/\s+/g,' ').trim();
                const pageText = clean(document.body?.innerText||'').toLowerCase();
                const noRecords = [
                    'sorry! no record found','sorry! no records found',
                    'no record found','no records found',
                ].some(p => pageText.includes(p));

                const tables = Array.from(document.querySelectorAll(
                    '#StickyTable, .ui-jqgrid-btable, table'
                ));
                if (!tables.length) return {rows:[],header:[],noRecords};

                const payloads = tables.map(table => {
                    const rows = Array.from(table.querySelectorAll('tr'))
                        .map(tr => Array.from(tr.querySelectorAll('th,td'))
                            .map(c => clean(c.innerText||c.textContent)))
                        .filter(r => r.length > 0);
                    const meaningful = rows.filter(r => {
                        const j = r.join(' ').toLowerCase();
                        return j && j !== 'print' && !j.startsWith('print ') &&
                               !j.includes('please select report method');
                    });
                    let header = Array.from(table.querySelectorAll('thead th,thead td'))
                        .map(c => clean(c.innerText||c.textContent)).filter(Boolean);
                    if (!header.length) {
                        const g = table.closest('.ui-jqgrid-view,.ui-jqgrid');
                        if (g) header = Array.from(g.querySelectorAll('.ui-jqgrid-htable th'))
                            .map(c => clean(c.innerText||c.textContent)).filter(Boolean);
                    }
                    const score = (meaningful.length*100) +
                                  meaningful.reduce((m,r)=>Math.max(m,r.length),0) +
                                  (header.length*5);
                    return {rows: meaningful, header, score, noRecords};
                });
                payloads.sort((a,b)=>b.score-a.score);
                return payloads[0] || {rows:[],header:[],noRecords};
            }
        """)
    except Exception:
        return {"rows": [], "header": [], "noRecords": False}


async def parse_report_table(report_page, end_date: date_type) -> list[dict]:
    log.info("Waiting for report table to populate...")

    payload      = {"rows": [], "header": [], "noRecords": False}
    last_sig     = None
    stable_ticks = 0
    no_records   = False

    def _sig(p):
        rows = p.get("rows", [])
        hdr  = p.get("header", [])
        return (
            len(rows), len(hdr),
            tuple(tuple(str(c) for c in r[:4]) for r in rows[:2]),
            tuple(tuple(str(c) for c in r[:4]) for r in rows[-2:]),
        )

    for elapsed in range(REPORT_WAIT_SECONDS):
        best = payload
        for root in _all_frames(report_page):
            p = await _read_table_payload(root)
            if p.get("noRecords"):
                no_records = True
            if (len(p.get("rows", [])) + len(p.get("header", []))) >= \
               (len(best.get("rows", [])) + len(best.get("header", []))):
                best = p

        if best.get("rows") or best.get("header"):
            payload = best
            sig = _sig(payload)
            stable_ticks = (stable_ticks + 1) if sig == last_sig else 0
            last_sig = sig
            if stable_ticks >= 3:
                log.info("  Table stable after %ds (%d rows).", elapsed, len(payload["rows"]))
                break

        if elapsed % 15 == 0 and elapsed > 0:
            log.info("  Still waiting... (%ds)", elapsed)
        await asyncio.sleep(1)

    if no_records and not payload.get("rows"):
        log.info("Report returned no records.")
        return []

    table_rows = payload.get("rows", [])
    ext_header = payload.get("header", [])

    if not table_rows and not ext_header:
        raise RuntimeError(f"Report table not found after {REPORT_WAIT_SECONDS}s.")

    # Find header row
    def _score_header(cells):
        return sum(1 for c in cells if HEADER_MAP.get(str(c).strip().lower()))

    header_idx = None
    for idx, row in enumerate(table_rows):
        if _score_header(row) >= 4:
            header_idx = idx
            break

    if header_idx is not None:
        header    = [str(c).strip() for c in table_rows[header_idx]]
        data_rows = table_rows[header_idx + 1:]
    elif ext_header and _score_header(ext_header) >= 4:
        header    = [str(c).strip() for c in ext_header]
        data_rows = table_rows
    else:
        num_cols = max((len(r) for r in table_rows), default=0)
        if num_cols == len(KNOWN_HEADERS):
            header = list(KNOWN_HEADERS)
            log.info("  Using hardcoded schema (%d cols).", len(header))
        else:
            header = [f"col_{i}" for i in range(num_cols)]
            log.warning("  Auto col_N header (%d cols).", num_cols)
        data_rows = table_rows

    def _is_total(cells):
        return "total" in " ".join(str(c) for c in cells[:3]).lower()

    clean_data = [
        r for r in data_rows
        if not (all(str(v).strip() == "" for v in r) or _is_total(r))
    ]

    if not clean_data:
        log.warning("All rows filtered — returning empty.")
        return []

    rows = [
        {col: (cells[i] if i < len(cells) else "")
         for i, col in enumerate(header)}
        for cells in clean_data
    ]
    log.info("Parsed %d rows. Sample: %s", len(rows), list(rows[0].items())[:5])
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# FETCH LOGIC
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_date_range(
    page, conn, start_date: date_type, end_date: date_type
) -> tuple[str, int]:
    try:
        await navigate_to_loadform(page)
        await set_report_options(page, start_date, end_date)
        report_page = await click_view_and_generate(page)
        try:
            rows = await parse_report_table(report_page, end_date)
        finally:
            if report_page is not page:
                try:
                    await report_page.close()
                except Exception:
                    pass

        if not rows:
            await log_run(conn, end_date, "no_data", 0,
                          "Generated report returned 0 rows.",
                          period_start=start_date, period_end=end_date)
            return "no_data", 0

        saved = await save_rows(conn, rows, start_date, end_date)
        await log_run(conn, end_date, "success", saved,
                      f"Fetched {start_date} → {end_date}",
                      period_start=start_date, period_end=end_date)
        log.info("SUCCESS %s → %s | %d rows saved.", start_date, end_date, saved)
        return "success", saved

    except Exception as exc:
        msg = str(exc)
        log.error("FAILED %s → %s: %s", start_date, end_date, msg)
        await log_run(conn, end_date, "failed", 0, msg,
                      period_start=start_date, period_end=end_date)
        return "failed", 0


async def forced_refresh(
    page, conn, start_date: date_type, end_date: date_type
) -> tuple[str, int]:
    try:
        await navigate_to_loadform(page)
        await set_report_options(page, start_date, end_date)
        report_page = await click_view_and_generate(page)
        try:
            rows = await parse_report_table(report_page, end_date)
        finally:
            if report_page is not page:
                try:
                    await report_page.close()
                except Exception:
                    pass

        if not rows:
            await log_run(conn, end_date, "no_data", 0,
                          "Forced refresh: 0 rows returned.",
                          period_start=start_date, period_end=end_date,
                          action_type="forced_refresh")
            return "no_data", 0

        deleted = await delete_rows_for_period(conn, start_date, end_date)
        saved   = await save_rows(conn, rows, start_date, end_date)
        await log_run(conn, end_date, "success", saved,
                      f"Forced refresh {start_date}→{end_date} | deleted={deleted}",
                      rows_deleted=deleted,
                      period_start=start_date, period_end=end_date,
                      action_type="forced_refresh")
        log.info("FORCED REFRESH %s → %s | deleted=%d saved=%d",
                 start_date, end_date, deleted, saved)
        return "success", saved

    except Exception as exc:
        msg = str(exc)
        log.error("FORCED REFRESH FAILED %s → %s: %s", start_date, end_date, msg)
        await log_run(conn, end_date, "failed", 0, msg,
                      period_start=start_date, period_end=end_date,
                      action_type="forced_refresh")
        return "failed", 0


# ══════════════════════════════════════════════════════════════════════════════
# BROWSER LAUNCH
# ══════════════════════════════════════════════════════════════════════════════

async def _launch_browser(pw):
    try:
        return await pw.chromium.launch(headless=False)
    except Exception as exc:
        err = str(exc)
        if "Executable doesn't exist" not in err and "Please run" not in err:
            raise
        log.warning("Chromium not found — attempting one-time install...")
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError("Playwright install failed.") from exc
        return await pw.chromium.launch(headless=True)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def run_bot(
    start_date: Optional[date_type] = None,
    end_date:   Optional[date_type] = None,
    force_refresh: bool = False,
):
    yesterday = datetime.now().date() - timedelta(days=1)

    log.info("=== CBL Load Form Details Bot START ===")
    log.info("DB: host=%s db=%s | Salesflo user_set=%s",
             DB_HOST, DB_NAME, "yes" if SALESFLO_USERNAME else "no")

    conn = await get_db()
    await ensure_tables(conn)

    # Determine date range
    if start_date and end_date:
        fetch_start = start_date
        fetch_end   = end_date
        log.info("User-supplied range: %s → %s", fetch_start, fetch_end)
    else:
        fetch_end  = yesterday
        last_saved = await get_last_saved_date(conn)
        if last_saved is None:
            fetch_start = yesterday - timedelta(days=7)
            log.info("No existing data. Backfilling %s → %s.", fetch_start, fetch_end)
        else:
            fetch_start = last_saved + timedelta(days=1)
            log.info("Last saved: %s. Fetching %s → %s.", last_saved, fetch_start, fetch_end)

    if fetch_start > fetch_end:
        log.info("Already up-to-date. Nothing to fetch.")
        conn.close()
        return

    async with async_playwright() as pw:
        browser = await _launch_browser(pw)
        context = await browser.new_context(accept_downloads=True)
        page    = await context.new_page()
        try:
            await login(page, SALESFLO_USERNAME, SALESFLO_PASSWORD)

            if force_refresh:
                status, saved = await forced_refresh(page, conn, fetch_start, fetch_end)
            else:
                status, saved = await fetch_date_range(page, conn, fetch_start, fetch_end)

        except Exception as exc:
            log.error("Bot run failed: %s", exc, exc_info=True)
            status, saved = "failed", 0
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    async with conn.cursor() as cur:
        await cur.execute(f"SELECT COUNT(*) FROM `{TABLE_DATA}`")
        total = (await cur.fetchone())[0]
    log.info("Total rows in '%s': %d", TABLE_DATA, total)

    try:
        conn.close()
    except Exception:
        pass

    if status == "success":
        log.info("Bot complete. Rows saved this run: %d", saved)
    elif status == "no_data":
        log.warning("Bot finished — no data returned.")
    else:
        log.error("Bot finished with failures.")

    log.info("=== CBL Load Form Details Bot DONE ===")


def main():
    parser = argparse.ArgumentParser(description="CBL Load Form Details scraper")
    parser.add_argument(
        "--start", metavar="YYYY-MM-DD",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
    )
    parser.add_argument(
        "--end", metavar="YYYY-MM-DD",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
    )
    parser.add_argument(
        "--force-refresh", action="store_true",
        help="Delete existing rows for the period and re-insert fresh data",
    )
    args = parser.parse_args()

    start = args.start
    end   = args.end
    if start and not end:
        end = start
    if end and not start:
        start = end

    asyncio.run(run_bot(start_date=start, end_date=end, force_refresh=args.force_refresh))


if __name__ == "__main__":
    main()