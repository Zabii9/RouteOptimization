"""
CBL SalesFlo - LoadForm Details Report Scraper  (v11)
Install:  pip install selenium webdriver-manager pandas openpyxl mysql-connector-python
Run:      python cbl_scraper.py
"""

import os
import time
import pandas as pd
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    NoSuchWindowException, WebDriverException
)
from webdriver_manager.chrome import ChromeDriverManager
import mysql.connector

# =====================================================================
#  SCRAPER CONFIGURATION
# =====================================================================
USERNAME  = "bazaar"
PASSWORD  = "Cblbazaar@235"
DATE_FROM = ""          # "May 01, 2026"  or "" for today
DATE_TO   = ""          # "May 14, 2026"  or "" for today
OUTPUT_DIR     = "."
HEADLESS       = False  # keep False — headless breaks innerText/textContent rendering
WAIT_TIMEOUT   = 30
REPORT_TIMEOUT = 180

# =====================================================================
#  DATABASE CONFIGURATION
# =====================================================================
DB_HOST     = "db42280.public.databaseasp.net"
DB_PORT     = 3306
DB_NAME     = "db42280"
DB_USER     = "db42280"
DB_PASSWORD = "admin2233"
DB_TABLE    = "cbl_loadform_details"
# =====================================================================

BASE_URL    = "https://cbl.salesflo.com/OB"
LOGIN_URL   = BASE_URL + "/login/"
REPORTS_URL = BASE_URL + "/reports/?page=SaleReports"

# ------------------------------------------------------------------
# JS_PROBE  — returns "|"-separated diagnostic lines
# Uses textContent (works in both headless and headed)
# ------------------------------------------------------------------
JS_PROBE = (
    "var out = [];"
    "var el = document.getElementById('StickyTable');"
    "out.push('StickyTable_by_id:' + (el ? 'tag=' + el.tagName + '_tds=' + el.querySelectorAll('tbody td').length : 'NULL'));"
    "var el2 = document.querySelector('table.cardTable');"
    "out.push('cardTable:' + (el2 ? 'tds=' + el2.querySelectorAll('tbody td').length : 'NULL'));"
    "var ft = document.getElementById('floatThead');"
    "out.push('floatThead_div:' + (ft ? 'children=' + ft.children.length : 'NULL'));"
    "var fw = document.querySelector('.floatThead-wrapper');"
    "out.push('floatThead-wrapper:' + (fw ? 'found' : 'NULL'));"
    "var sd = document.getElementById('StickyDiv');"
    "out.push('StickyDiv:' + (sd ? 'found' : 'NULL'));"
    "var tbls = document.querySelectorAll('table');"
    "out.push('Total_tables:' + tbls.length);"
    "for (var i = 0; i < tbls.length; i++) {"
    "  var t = tbls[i];"
    "  var firstTd = t.querySelector('tbody td');"
    "  var sample = firstTd ? firstTd.textContent.trim().substring(0,30) : '';"
    "  out.push('tbl[' + i + ']_id=' + t.id + '_tds=' + t.querySelectorAll('tbody td').length + '_sample=' + sample);"
    "}"
    "return out.join('|');"
)

# ------------------------------------------------------------------
# JS_EXTRACT — uses textContent (not innerText) so works everywhere
# ------------------------------------------------------------------
JS_EXTRACT = (
    "var tbl = null;"
    "var bestN = 0;"
    # 1. #floatThead div > table
    "var ft = document.getElementById('floatThead');"
    "if (ft) {"
    "  var t = ft.querySelector('table');"
    "  if (t) { var n = t.querySelectorAll('tbody td').length; if (n > bestN) { bestN = n; tbl = t; } }"
    "}"
    # 2. .floatThead-wrapper .table-responsive table
    "var fw = document.querySelector('.floatThead-wrapper .table-responsive table');"
    "if (fw) { var n = fw.querySelectorAll('tbody td').length; if (n > bestN) { bestN = n; tbl = fw; } }"
    # 3. table.cardTable
    "var ct = document.querySelector('table.cardTable');"
    "if (ct) { var n = ct.querySelectorAll('tbody td').length; if (n > bestN) { bestN = n; tbl = ct; } }"
    # 4. #StickyDiv any table
    "var sd = document.getElementById('StickyDiv');"
    "if (sd) { var sdTbls = sd.querySelectorAll('table'); for (var i=0;i<sdTbls.length;i++) { var n=sdTbls[i].querySelectorAll('tbody td').length; if(n>bestN){bestN=n;tbl=sdTbls[i];} } }"
    # 5. largest table on page
    "var tbls = document.querySelectorAll('table');"
    "for (var i = 0; i < tbls.length; i++) {"
    "  var n = tbls[i].querySelectorAll('tbody td').length;"
    "  if (n > bestN) { bestN = n; tbl = tbls[i]; }"
    "}"
    "if (!tbl || bestN === 0) return null;"
    # headers — try aria-label first, fall back to textContent
    "var headers = [];"
    "var ths = tbl.querySelectorAll('thead th');"
    "for (var i = 0; i < ths.length; i++) {"
    "  var lbl = ths[i].getAttribute('aria-label') || '';"
    "  var txt = (lbl.trim() ? lbl : ths[i].textContent).trim();"
    "  headers.push(txt);"
    "}"
    # rows — textContent instead of innerText
    "var rows = [];"
    "var trs = tbl.querySelectorAll('tbody tr');"
    "for (var i = 0; i < trs.length; i++) {"
    "  var tds = trs[i].querySelectorAll('td');"
    "  if (tds.length === 0) continue;"
    "  var cells = []; var hasData = false;"
    "  for (var j = 0; j < tds.length; j++) {"
    "    var txt = tds[j].textContent.trim();"
    "    cells.push(txt);"
    "    if (txt) hasData = true;"
    "  }"
    "  if (hasData) rows.push(cells);"
    "}"
    "return {headers: headers, rows: rows, tdCount: bestN, tableId: tbl.id, tableClass: tbl.className.substring(0,60)};"
)


def build_driver():
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    svc    = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=svc, options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return driver


# =====================================================================
#  DATABASE HELPERS
# =====================================================================

def col_to_sql_name(col):
    name = col.strip().lower()
    name = name.replace(" ", "_").replace("/", "_").replace("#", "num").replace("-", "_")
    name = "".join(c for c in name if c.isalnum() or c == "_")
    if name and name[0].isdigit():
        name = "col_" + name
    return name or "col"


def ensure_table(conn, df):
    cursor = conn.cursor()
    col_defs = []
    for col in df.columns:
        sql_col = col_to_sql_name(col)
        if sql_col == "scraped_at":
            continue
        col_defs.append("`" + sql_col + "` TEXT")
    col_defs.append("`scraped_at` DATETIME DEFAULT CURRENT_TIMESTAMP")

    create_sql = (
        "CREATE TABLE IF NOT EXISTS `" + DB_TABLE + "` ("
        "  `id` INT AUTO_INCREMENT PRIMARY KEY, "
        + ", ".join(col_defs)
        + ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
    )
    cursor.execute(create_sql)
    conn.commit()

    cursor.execute("DESCRIBE `" + DB_TABLE + "`;")
    existing = {row[0].lower() for row in cursor.fetchall()}
    for col in df.columns:
        sql_col = col_to_sql_name(col)
        if sql_col == "scraped_at":
            continue
        if sql_col not in existing:
            cursor.execute(
                "ALTER TABLE `" + DB_TABLE + "` ADD COLUMN `" + sql_col + "` TEXT;"
            )
            conn.commit()
            print("     DB: added column `" + sql_col + "`")
    cursor.close()


def insert_rows(conn, df):
    cursor = conn.cursor()
    sql_cols   = [col_to_sql_name(c) for c in df.columns]
    col_list   = ", ".join("`" + c + "`" for c in sql_cols)
    val_tmpl   = ", ".join(["%s"] * len(sql_cols))
    insert_sql = (
        "INSERT INTO `" + DB_TABLE + "` (" + col_list + ") VALUES (" + val_tmpl + ");"
    )
    batch = []
    batch_sz = 500
    inserted = 0

    for _, row in df.iterrows():
        vals = []
        for col in df.columns:
            v = row[col]
            vals.append(str(v) if v not in (None, "") else None)
        batch.append(tuple(vals))
        if len(batch) >= batch_sz:
            cursor.executemany(insert_sql, batch)
            conn.commit()
            inserted += len(batch)
            print("     DB: inserted %d rows so far ..." % inserted)
            batch = []

    if batch:
        cursor.executemany(insert_sql, batch)
        conn.commit()
        inserted += len(batch)

    cursor.close()
    print("     DB: total inserted = %d rows" % inserted)
    return inserted


def save_to_db(df):
    print("\n[DB]  Connecting to MySQL ...")
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connection_timeout=30,
            charset="utf8mb4",
        )
        print("     connected to", DB_HOST)
        print("     ensuring table `" + DB_TABLE + "` ...")
        ensure_table(conn, df)
        print("     inserting %d rows ..." % len(df))
        n = insert_rows(conn, df)
        conn.close()
        print("  DB save complete: %d rows -> `%s`" % (n, DB_TABLE))
        return True
    except mysql.connector.Error as e:
        print("  DB ERROR:", e)
        return False


# =====================================================================
#  SCRAPER
# =====================================================================

class CBLScraper:

    def __init__(self):
        self.driver = build_driver()

    def _wait_el(self, by, val, timeout=WAIT_TIMEOUT):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, val))
        )

    def _click_el(self, by, val, timeout=WAIT_TIMEOUT):
        el = WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, val))
        )
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.3)
        self.driver.execute_script("arguments[0].click();", el)
        return el

    def _js_set(self, el, value):
        self.driver.execute_script(
            "arguments[0].removeAttribute('readonly'); arguments[0].value='';", el
        )
        el.send_keys(value)
        self.driver.execute_script(
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el
        )

    def _set_cb(self, el, want):
        if el.is_selected() != want:
            self.driver.execute_script("arguments[0].click();", el)

    def _shot(self, name):
        try:
            p = os.path.join(OUTPUT_DIR, name)
            self.driver.save_screenshot(p)
            print("  screenshot:", os.path.abspath(p))
        except Exception:
            pass

    # ── 1. Login ──────────────────────────────────────────────────

    def login(self):
        print("\n[1]  Login ...")
        self.driver.get(LOGIN_URL)
        time.sleep(2)
        self._js_set(self._wait_el(By.NAME, "username"), USERNAME)
        time.sleep(0.3)
        self._js_set(self.driver.find_element(By.NAME, "password"), PASSWORD)
        time.sleep(0.3)
        self.driver.find_element(By.CSS_SELECTOR, "input[type='image']").click()
        WebDriverWait(self.driver, WAIT_TIMEOUT).until(
            lambda d: "login" not in d.current_url.lower()
        )
        time.sleep(1.5)
        print("     OK:", self.driver.current_url)

    # ── 2. Reports page ───────────────────────────────────────────

    def go_to_reports(self):
        print("\n[2]  Reports page ...")
        self.driver.get(REPORTS_URL)
        time.sleep(3)
        print("     OK:", self.driver.current_url)

    # ── 3. Sale Reports sidebar ───────────────────────────────────

    def click_sale_reports(self):
        print("\n[3]  Sale Reports ...")
        for by, val in [
            (By.XPATH, "//a[normalize-space(text())='Sale Reports']"),
            (By.XPATH, "//li[normalize-space(text())='Sale Reports']"),
            (By.XPATH, "//*[normalize-space(text())='Sale Reports']"),
        ]:
            try:
                el = WebDriverWait(self.driver, 6).until(EC.element_to_be_clickable((by, val)))
                self.driver.execute_script("arguments[0].click();", el)
                print("     OK")
                time.sleep(2)
                return
            except (TimeoutException, NoSuchElementException):
                pass
        if "SaleReports" in self.driver.current_url:
            print("     already there")
            return
        raise RuntimeError("Sale Reports not found")

    # ── 4. LoadForm Summary ───────────────────────────────────────

    def click_loadform_summary(self):
        print("\n[4]  LoadForm Summary ...")
        time.sleep(1)
        for by, val in [
            (By.XPATH, "//*[normalize-space(text())='LoadForm Summary']"),
            (By.XPATH, "//*[normalize-space(text())='Load Form Summary']"),
            (By.XPATH, "//*[contains(normalize-space(text()),'LoadForm Summary')]"),
            (By.XPATH, "//*[contains(normalize-space(text()),'Load Form Summary')]"),
            (By.XPATH, "//a[contains(translate(@href,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'loadform')]"),
        ]:
            try:
                el = WebDriverWait(self.driver, 6).until(EC.element_to_be_clickable((by, val)))
                self.driver.execute_script("arguments[0].click();", el)
                print("     OK")
                time.sleep(3)
                return
            except (TimeoutException, NoSuchElementException):
                pass
        raise RuntimeError("LoadForm Summary not found")

    # ── 5. Filters ────────────────────────────────────────────────

    def fill_filters(self):
        print("\n[5]  Filters ...")
        time.sleep(1.5)

        for lbl, want in [("Carton", True), ("Box", False), ("Unit", False)]:
            try:
                cb = self.driver.find_element(
                    By.XPATH,
                    "//label[normalize-space(text())='" + lbl + "']"
                    "/preceding-sibling::input[@type='checkbox'][1]|"
                    "//label[normalize-space(text())='" + lbl + "']/input[@type='checkbox']|"
                    "//input[@type='checkbox'][@value='" + lbl + "']"
                )
                self._set_cb(cb, want)
            except NoSuchElementException:
                pass

        self._pick_radio()
        time.sleep(1.5)
        self._check_stores()

        if DATE_FROM:
            try:
                self._js_set(self.driver.find_element(By.XPATH, "//input[@id='dt1' or @name='dt1']"), DATE_FROM)
            except NoSuchElementException:
                pass
        if DATE_TO:
            try:
                self._js_set(self.driver.find_element(By.XPATH, "//input[@id='dt2' or @name='dt2']"), DATE_TO)
            except NoSuchElementException:
                pass

        time.sleep(0.5)
        self._shot("before_view_report.png")

        self._click_el(
            By.XPATH,
            "//button[contains(normalize-space(.),'View Report')]|"
            "//input[contains(@value,'View Report')]|"
            "//a[contains(normalize-space(.),'View Report')]"
        )
        time.sleep(3)

        handles_before = set(self.driver.window_handles)
        try:
            self._click_el(
                By.XPATH,
                "//button[normalize-space(text())='Generate']|"
                "//a[normalize-space(text())='Generate']|"
                "//input[@value='Generate']",
                timeout=10
            )
        except TimeoutException:
            self._click_el(
                By.XPATH,
                "//*[contains(normalize-space(text()),'Generate')][self::button or self::a or self::input]",
                timeout=8
            )

        WebDriverWait(self.driver, 20).until(
            lambda d: len(d.window_handles) > len(handles_before)
        )
        new_tab = (set(self.driver.window_handles) - handles_before).pop()
        self.driver.switch_to.window(new_tab)
        print("     new tab:", self.driver.current_url)

        WebDriverWait(self.driver, 60).until(
            lambda d: "showreport" in d.current_url.lower()
        )
        print("     showreport loaded:", self.driver.current_url)

    def _pick_radio(self):
        try:
            container = self.driver.find_element(
                By.XPATH,
                "//td[contains(normalize-space(.),'Type Of Report')]/following-sibling::td[1]"
            )
            radios = container.find_elements(By.XPATH, ".//input[@type='radio']")
            if len(radios) >= 3:
                self.driver.execute_script("arguments[0].click();", radios[2])
                time.sleep(0.4)
                self.driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", radios[2]
                )
                print("     radio: Load Form Details")
                return
        except NoSuchElementException:
            pass
        self.driver.execute_script(
            "for(var r of document.querySelectorAll('tr')){"
            "if(r.innerText.indexOf('Type Of Report')!==-1){"
            "var rds=r.querySelectorAll('input[type=radio]');"
            "if(rds.length>=3){rds[rds.length-1].click();"
            "rds[rds.length-1].dispatchEvent(new Event('change',{bubbles:true}));break;}}}"
        )
        print("     radio: JS fallback")

    def _check_stores(self):
        try:
            WebDriverWait(self.driver, 10).until(
                EC.visibility_of_element_located((By.ID, "DynamicCheckBoxTr_ShowStoresDetails"))
            )
        except TimeoutException:
            pass
        for by, val in [
            (By.ID,   "ShowStoresDetails_1"),
            (By.NAME, "ShowStoresDetails"),
            (By.CSS_SELECTOR, "input.ShowStoresDetails"),
            (By.XPATH, "//tr[@id='DynamicCheckBoxTr_ShowStoresDetails']//input[@type='checkbox']"),
        ]:
            try:
                cb = self.driver.find_element(by, val)
                self.driver.execute_script("arguments[0].closest('tr').style.display='table-row';", cb)
                self._set_cb(cb, True)
                if cb.is_selected():
                    print("     Show Stores Details: checked")
                    return
                self.driver.execute_script(
                    "arguments[0].checked=true;"
                    "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", cb
                )
                if cb.is_selected():
                    print("     Show Stores Details: checked via JS")
                    return
            except (NoSuchElementException, Exception):
                pass
        print("     Show Stores Details: not found")

    def _check_sku_weight_type(self):
        try:
            WebDriverWait(self.driver, 10).until(
                EC.visibility_of_element_located((By.ID, "DynamicCheckBoxTr_ShowSKUWeightType"))
            )
        except TimeoutException:
            pass
        for by, val in [
            (By.ID,   "ShowSKUWeightType_1"),
            (By.NAME, "ShowSKUWeightType"),
            (By.CSS_SELECTOR, "input.ShowSKUWeightType"),
            (By.XPATH, "//tr[@id='DynamicCheckBoxTr_ShowSKUWeightType']//input[@type='checkbox']"),
            (By.XPATH, "//label[contains(normalize-space(text()),'SKU Weight Type')]"
                        "/preceding-sibling::input[@type='checkbox'][1]"),
            (By.XPATH, "//label[contains(normalize-space(text()),'SKU Weight Type')]"
                       "/input[@type='checkbox']"),
            (By.XPATH, "//td[contains(normalize-space(text()),'SKU Weight Type')]"
                       "//following::input[@type='checkbox'][1]"),
        ]:
            try:
                cb = self.driver.find_element(by, val)
                self.driver.execute_script(
                    "arguments[0].closest('tr').style.display='table-row';", cb
                )
                self._set_cb(cb, True)
                if cb.is_selected():
                    print("     Show SKU Weight Type: checked")
                    return
                self.driver.execute_script(
                    "arguments[0].checked=true;"
                    "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", cb
                )
                if cb.is_selected():
                    print("     Show SKU Weight Type: checked via JS")
                    return
            except (NoSuchElementException, Exception):
                pass
        print("     Show SKU Weight Type: not found")

    # ── 6. Scrape ─────────────────────────────────────────────────

    def scrape_report(self):
        print("\n[6]  Scraping:", self.driver.current_url)

        # Wait for any tbody td
        print("     waiting for tbody data ...")
        try:
            WebDriverWait(self.driver, REPORT_TIMEOUT).until(
                lambda d: d.execute_script(
                    "return document.querySelectorAll('table tbody td').length > 0;"
                )
            )
            print("     data rows present")
        except TimeoutException:
            print("     ERROR: no data appeared")
            self._shot("debug_no_data.png")
            return pd.DataFrame()

        # Extra wait for full render
        time.sleep(3)

        # Probe DOM
        print("     probing DOM ...")
        try:
            probe = self.driver.execute_script(JS_PROBE)
            if probe:
                for line in str(probe).split("|"):
                    print("       " + line.strip())
        except Exception as e:
            print("     probe error:", str(e)[:200])

        # Extract all data in one JS call
        print("     extracting data ...")
        try:
            result = self.driver.execute_script(JS_EXTRACT)
        except (NoSuchWindowException, WebDriverException) as e:
            print("     window closed during extract:", str(e)[:120])
            return pd.DataFrame()

        if not result:
            print("     ERROR: JS returned None — check probe output above")
            self._shot("debug_js_null.png")
            return pd.DataFrame()

        headers = result.get("headers", [])
        rows    = result.get("rows",    [])
        print("     table id='%s' class='%s'" % (result.get("tableId"), result.get("tableClass")))
        print("     tds=%s  rows=%d  cols=%d" % (result.get("tdCount"), len(rows), len(headers)))
        print("     columns:", headers)

        if not rows:
            print("     ERROR: 0 data rows extracted")
            self._shot("debug_empty_rows.png")
            return pd.DataFrame()

        if not headers:
            max_cols = max((len(r) for r in rows), default=0)
            headers  = ["Col" + str(i) for i in range(max_cols)]

        n      = len(headers)
        padded = [(r + [""] * n)[:n] for r in rows]
        df     = pd.DataFrame(padded, columns=headers)

        # Metadata column for DB
        df["scraped_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print("     FINAL: %d rows x %d cols" % (len(df), len(df.columns)))
        return df

    # ── Save files ────────────────────────────────────────────────

    def save_files(self, df):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        xlsx    = os.path.join(OUTPUT_DIR, "cbl_loadform_" + ts + ".xlsx")
        csv     = os.path.join(OUTPUT_DIR, "cbl_loadform_" + ts + ".csv")
        df_file = df.drop(columns=["scraped_at"], errors="ignore")
        df_file.to_excel(xlsx, index=False)
        df_file.to_csv(csv,   index=False)
        print("  Excel:", os.path.abspath(xlsx))
        print("  CSV:  ", os.path.abspath(csv))

    # ── Run ───────────────────────────────────────────────────────

    def run(self):
        try:
            self.login()
            self.go_to_reports()
            self.click_sale_reports()
            self.click_loadform_summary()
            self.fill_filters()
            df = self.scrape_report()

            if df.empty:
                print("\nWARN: no data scraped")
                self._shot("debug_empty.png")
            else:
                self.save_files(df)
                ok = save_to_db(df)
                if not ok:
                    print("  WARN: DB save failed — data still saved to files above")

                print("\nPreview (first 5 rows):")
                print(df.drop(columns=["scraped_at"], errors="ignore").head(5).to_string(index=False))

        except Exception as e:
            print("\nERROR:", e)
            self._shot("debug_error.png")
            raise
        finally:
            print("\nClosing browser ...")
            self.driver.quit()


if __name__ == "__main__":
    CBLScraper().run()