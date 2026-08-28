"""
Ellis County Foreclosure Notices scraper.

Portal: https://ellisccktxpublicsearch.us/AcclaimWeb/ (vendor: "Harris
Recording Solutions" / AcclaimWeb -- NOT publicsearch.us/GovOS, a completely
different vendor despite the similar-sounding "official records search"
role. Ellis's PROBATE case-search (a different, unrelated system: LGS
Online Solutions) should not be confused with this one.)

## Guest vs. authenticated access -- the central finding

Guest (unauthenticated) access only offers a Name-required search --
useless for a "what's new today" daily sweep (confirmed: submitting a
blank-name date+doctype search returns "Name is required.", not results).
Logging in unlocks six more search types, including **"Property (By Date
Range/Doc Type)"** -- exactly what's needed, no name required.

This means the scraper REQUIRES a real account (env vars ACCOUNT_EMAIL /
ACCOUNT_PASSWORD, wired to the ACCOUNT_EMAIL/ACCOUNT_PASSWORD GitHub
secrets already used by register_ellis.py). This is legitimate account use
(the site's own UI invites guests to "create an account for more search
options"), not a bot-detection bypass -- registration itself is
reCAPTCHA-gated and was completed by a human, not by this code.

## No street address available for free

The results grid columns are: Grantor, Grantee, Document Type, Document #,
Book\\Page, Record Date, Legal Description. There is NO address column.
Every row's only action is an "Add To Cart" button (`onclick=
"displaySelectedOrders(this)"`) -- a purchase queue for the scanned
document image, not a free detail/preview view. Getting the real street
address requires buying the document from the vendor; this scraper does
NOT do that (no payment flow implemented -- a business decision, not an
engineering gap). Records are therefore NAME-only leads: address/city/
zip_code are always ''. main.py's `_useful()` must accept name-only
records for Ellis to produce anything (an address-only requirement, as
some other counties in this repo need, would silently drop every one).

## "NOTICE" doctype is a broad catch-all, not "Notice of Trustee's Sale"

Checked "Document Type Groups" -- there is no finer-grained sub-category;
"NOTICE" is the only relevant top-level bucket, and it is genuinely mixed:
real trustee-sale-shaped rows (e.g. a homebuilder as Grantor, an
individual as Grantee, a Lot/Block legal description -- consistent with a
builder-financed construction loan foreclosure) sit alongside entirely
unrelated filings like city ordinances/resolutions (Grantor "WAXAHACHIE
CITY OF", Grantee "PUBLIC", legal description "ORDINANCE NO 3746").
Filtered in code (see `_looks_like_individual_lead`) rather than assumed
away: require the Grantee to parse into a genuine two-part person name
(rejects single-token values like "PUBLIC") AND pass the existing
entity-exclusion check (rejects "CITY OF", "LLC", etc. -- this also
catches builder-to-builder / city / HOA transactions where BOTH parties
are entities). A blank/NA Legal Description is NOT used as a reject
signal -- some genuine individual-Grantee rows have it blank too, and a
positive "must contain LOT/BLOCK" filter would silently drop real leads
that guessing shouldn't drop (SYSTEM_GUIDE.md's own preference: a loud,
recoverable degradation over silently discarding data).

## Name field order -- LASTNAME FIRSTNAME [MIDDLE], no comma

Confirmed from real sample rows (e.g. "MCKAIG MARY JANE", "TORRES ALAN
RAUL") -- last name FIRST, no comma, unlike base.py's `parse_name()`
(which assumes first-name-first) or the sister probate repo's comma
-delimited "LAST, FIRST MIDDLE" parser. A dedicated parser is used here
(`_parse_grantee_name`) rather than misapplying either of those.

## Pagination -- NOT yet live-verified

417 filings were observed in a 60-day sample window (1 - 50 of 417 items,
Kendo UI grid pager). Pagination is implemented using standard Kendo Grid
pager conventions (`.k-pager-numbers`, `.k-pager-next`) since a live
results page was never generated far enough into a probe run to click
through pages -- verify via the first real dry-run's per-page log lines
(SYSTEM_GUIDE.md Sec.9 bug #2: don't assume pagination works, confirm via
row-count logs, iterate if it doesn't advance).
"""
import re
import time
from datetime import date, timedelta
from typing import Dict, List, Optional

from .base import BaseScraper, is_residential_lead, launch_chromium

BASE_URL = 'https://ellisccktxpublicsearch.us/AcclaimWeb/'

# Rolling lookback window for the daily job -- env-overridable for manual
# backfills, matching the convention used elsewhere in this project (see
# scrapers/publicsearch.py in the sister repo).
import os
WINDOW_DAYS = int(os.environ.get('ELLIS_FC_WINDOW_DAYS', '14'))
MAX_PAGES = int(os.environ.get('ELLIS_FC_MAX_PAGES', '20'))


def _parse_grantee_name(raw: str):
    """'MCKAIG MARY JANE' -> ('Mary Jane', 'Mckaig'). LAST name is the
    FIRST token here (confirmed from real samples -- see module docstring),
    the opposite order from base.py's parse_name(). A single-token value
    (e.g. 'PUBLIC') returns ('', '') -- deliberately NOT treated as a
    last-name-only person, since that pattern is how non-individual filers
    show up in this dataset (see module docstring)."""
    raw = re.sub(r'\s+', ' ', (raw or '')).strip()
    if not raw:
        return '', ''
    parts = raw.split(' ')
    if len(parts) < 2:
        return '', ''
    last = parts[0].title()
    first = ' '.join(parts[1:]).title()
    return first, last


def _looks_like_individual_lead(grantee_raw: str) -> bool:
    first, last = _parse_grantee_name(grantee_raw)
    if not first or not last:
        return False
    return is_residential_lead(f"{first} {last}")


def _fmt_date(raw: str) -> str:
    raw = (raw or '').strip()
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', raw)
    if m:
        mm, dd, yy = m.groups()
        return f"{int(mm):02d}/{int(dd):02d}/{yy}"
    return raw


class EllisCountyScraper(BaseScraper):

    def __init__(self):
        super().__init__('Ellis')
        self.email = os.environ.get('ACCOUNT_EMAIL', '')
        self.password = os.environ.get('ACCOUNT_PASSWORD', '')

    def scrape(self, target_date: date) -> List[Dict]:
        self.logger.info(f"Scraping Ellis County for {target_date}")
        if not self.email or not self.password:
            self.logger.error(
                "Ellis: ACCOUNT_EMAIL/ACCOUNT_PASSWORD not set -- guest access "
                "cannot do a name-less date+doctype search (see module "
                "docstring). Skipping.")
            return []
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.logger.error("Ellis: Playwright not installed")
            return []

        raw_rows: List[Dict] = []
        try:
            with sync_playwright() as pw:
                browser = launch_chromium(pw)
                ctx = browser.new_context(user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
                ))
                page = ctx.new_page()
                page.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
                page.set_default_timeout(30_000)

                if not self._login(page):
                    browser.close()
                    return []

                start = target_date - timedelta(days=WINDOW_DAYS)
                if not self._search(page, start, target_date):
                    browser.close()
                    return []

                raw_rows = self._scrape_all_pages(page)
                browser.close()
        except Exception as exc:
            self.logger.error(f"Ellis: error: {exc}", exc_info=True)
            return []

        records = self._build_records(raw_rows, target_date)
        self.logger.info(f"Ellis: {len(records)} final records")
        return records

    # ── Login + search ───────────────────────────────────────────────────────

    def _login(self, page) -> bool:
        page.goto(BASE_URL, wait_until='networkidle', timeout=30_000)
        page.wait_for_timeout(1000)
        try:
            page.get_by_text('Ignore', exact=True).first.click(timeout=5000)
        except Exception:
            pass

        loc = page.get_by_text('Login', exact=False)
        clicked = False
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000)
                clicked = True
                break
        if not clicked:
            self.logger.error("Ellis: 'Login' link not found on landing page.")
            return False
        page.wait_for_timeout(1000)

        try:
            page.locator('#Username').fill(self.email, timeout=5000)
            page.locator('#Password').fill(self.password, timeout=5000)
            page.locator('input[type="submit"][value="Log in"]').first.click(timeout=8000)
        except Exception as e:
            self.logger.error(f"Ellis: login form interaction failed: {str(e)[:200]}")
            return False
        page.wait_for_timeout(2000)
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass

        body = page.evaluate("() => document.body.innerText || ''")
        logged_in = 'Welcome, Guest' not in body
        if not logged_in:
            self.logger.error("Ellis: login did not succeed (still shows 'Welcome, Guest').")
            return False
        self.logger.info("Ellis: login successful.")
        return True

    def _search(self, page, start: date, end: date) -> bool:
        loc = page.get_by_text('Property (By Date Range/Doc Type)', exact=False)
        clicked = False
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000, force=True)
                clicked = True
                break
        if not clicked:
            self.logger.error(
                "Ellis: 'Property (By Date Range/Doc Type)' search link not found -- "
                "was the account not actually granted this search type?")
            return False
        page.wait_for_timeout(1500)

        try:
            page.fill('#FromDatePicker', start.strftime('%m/%d/%Y'), timeout=5000)
            page.fill('#ToDatePicker', end.strftime('%m/%d/%Y'), timeout=5000)
        except Exception as e:
            self.logger.error(f"Ellis: date field fill failed: {str(e)[:200]}")
            return False

        try:
            page.locator('#DocTypesList').select_option(label='NOTICE', force=True, timeout=5000)
            page.evaluate("""
                () => {
                    const el = document.getElementById('DocTypesList');
                    if (el) el.dispatchEvent(new Event('change', {bubbles: true}));
                }
            """)
        except Exception as e:
            self.logger.error(f"Ellis: DocType select failed: {str(e)[:200]}")
            return False
        page.wait_for_timeout(500)

        try:
            page.locator('#SearchBtn').click(timeout=8000)
        except Exception:
            page.locator('input[type="button"][value="Search" i], '
                          'input[type="submit"][value="Search" i]').first.click(timeout=8000)
        page.wait_for_timeout(2500)
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass
        self.logger.info(f"Ellis: search submitted for {start:%m/%d/%Y}..{end:%m/%d/%Y}")
        return True

    # ── Pagination + row extraction ─────────────────────────────────────────

    _PARSE_ROWS_JS = """() => {
        const table = document.querySelector('table.k-selectable');
        if (!table) return [];
        const rows = Array.from(table.querySelectorAll('tr')).slice(1); // skip header
        return rows.map(tr => {
            const cells = Array.from(tr.querySelectorAll('td')).map(
                td => (td.textContent || '').replace(/\\s+/g, ' ').trim());
            // Columns: [Add To Cart][IsViewed][Grantor][Grantee][DocType][Doc#][Book\\Page][RecordDate][LegalDesc]
            return {
                grantor: cells[2] || '',
                grantee: cells[3] || '',
                doc_type: cells[4] || '',
                doc_id: cells[5] || '',
                record_date: cells[7] || '',
                legal_description: cells[8] || '',
            };
        }).filter(r => r.grantor || r.grantee || r.doc_id);
    }"""

    def _wait_for_results(self, page) -> bool:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                n = page.evaluate(
                    "() => document.querySelectorAll('table.k-selectable tr').length")
            except Exception:
                n = 0
            if n > 1:  # more than just the header row
                return True
            try:
                no_results = page.evaluate(
                    "() => /no records|no results|0 items/i.test(document.body.innerText||'')")
            except Exception:
                no_results = False
            if no_results:
                self.logger.info("Ellis: portal explicitly reports no results.")
                return False
            page.wait_for_timeout(400)
        self.logger.warning(
            "Ellis: results table never appeared within 25s -- treating as empty "
            "(may be a slow portal, not genuinely 0 results).")
        return False

    def _parse_rows(self, page) -> List[Dict]:
        try:
            return page.evaluate(self._PARSE_ROWS_JS) or []
        except Exception as e:
            self.logger.warning(f"Ellis: row parse failed: {e}")
            return []

    def _click_next_page(self, page, page_num: int) -> bool:
        """Click the Kendo pager's next-page control and wait for the first
        row's doc_id to actually change (SYSTEM_GUIDE.md Sec.9 bug #2 -- a
        fixed sleep here would risk reading a stale/still-rendering page)."""
        before_rows = self._parse_rows(page)
        before_id = before_rows[0]['doc_id'] if before_rows else ''

        for sel in [f'.k-pager-numbers a[data-page="{page_num + 1}"]',
                    '.k-pager-nav.k-pager-next', '.k-pager-next',
                    'a[title="Go to the next page"]']:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=8000)
                    break
            except Exception:
                continue
        else:
            self.logger.warning(f"Ellis: no next-page control matched after page {page_num}.")
            return False

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            rows = self._parse_rows(page)
            if rows and rows[0]['doc_id'] != before_id:
                return True
            page.wait_for_timeout(300)
        self.logger.warning(
            f"Ellis: page did not visibly change after clicking next from page {page_num}.")
        return False

    def _scrape_all_pages(self, page) -> List[Dict]:
        rows: List[Dict] = []
        if not self._wait_for_results(page):
            return rows

        page_num = 1
        while True:
            page_rows = self._parse_rows(page)
            first = page_rows[0]['grantee'] if page_rows else '(none)'
            self.logger.info(f"Ellis: page {page_num} -> {len(page_rows)} rows (first grantee: {first!r})")
            rows.extend(page_rows)

            if page_num >= MAX_PAGES:
                self.logger.warning(f"Ellis: hit MAX_PAGES={MAX_PAGES} safety cap -- stopping early.")
                break
            if not self._click_next_page(page, page_num):
                self.logger.info(f"Ellis: stopping pagination after page {page_num}.")
                break
            page_num += 1

        return rows

    # ── Filtering + record building ─────────────────────────────────────────

    def _build_records(self, raw_rows: List[Dict], target_date: date) -> List[Dict]:
        records = []
        kept_individual = 0
        for r in raw_rows:
            grantee = r.get('grantee', '')
            if not _looks_like_individual_lead(grantee):
                continue
            kept_individual += 1

            first, last = _parse_grantee_name(grantee)
            rec = self.build_record(
                first_name=first,
                last_name=last,
                file_date=_fmt_date(r.get('record_date', '')),
                doc_id=r.get('doc_id', ''),
            )
            records.append(rec)

        self.logger.info(
            f"Ellis: {len(raw_rows)} rows scanned -> {kept_individual} had an individual "
            f"-looking Grantee -> {len(records)} final records")
        return records
