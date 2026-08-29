"""
Collin County Foreclosure Notices scraper.

Portal: https://apps2.collincountytx.gov/ForeclosureNotices
NOTE: the "apps." subdomain from the original brief (apps.collincountytx.gov)
404s ("File or directory not found"). The real, live app is on "apps2." --
confirmed by probing: the county's own /county-clerk page links to it under
"Search Foreclosed Properties" / "Foreclosures". Verify this again if the
scraper ever starts getting 404s -- the county could rename the app host
again.

Platform: a Blazor SERVER app (MudBlazor component library; transport is
SignalR over /_blazor/*, confirmed via network capture during investigation)
-- NOT query-param driven like publicsearch.us/GovOS. The URL never changes
across filters or pagination; all state lives server-side over a persistent
connection. That means this scraper must DRIVE the live UI with Playwright
(load once, then click "Next page" repeatedly) -- there is no results-URL
shortcut to build directly.

Fields available directly in the results list -- NO OCR needed for any of
these:
  - Property address (street + embedded city/state/zip on one line)
  - City (SEPARATELY labeled -- and can genuinely differ from the address
    line's embedded city: e.g. one real record showed address line
    "108 GARFIELD CT CELINA, TX 75009" [USPS/postal city] alongside a
    labeled "City: Weston" [the actual county/municipal boundary city].
    We treat the labeled City: field as authoritative for the `city` output
    column, and only use the address line to recover the street + zip.
  - Sale Date, File Date (already "MM/DD/YYYY", no reformatting needed)
  - Property Type (e.g. "Residential Single Family (A1)")

Fields NOT available anywhere on this portal (verified, not assumed):
  - Owner / grantor name
  - A document ID / instrument number
Verified by: isolating one real results row (a single <td class="mud-table
-cell"> in a MudTable) and clicking it directly -- no expansion, no
navigation, no new content of any kind appeared. Also checked: clicking a
map marker (only shows the address in a small popup, nothing more), and
scanning the entire DOM for any export/download/CSV/Excel/print affordance
(none exists -- no title/aria-label anywhere matches). This is a genuine,
confirmed limitation of this specific PUBLIC-facing lookup tool (it's a
simplified "is there a notice for my address" consumer tool, not the
county's full official-records search -- that's a separate publicsearch.us
instance at collin.tx.publicsearch.us). Records built here are address-only
(first_name/last_name/doc_id are always ''). base.py's build_record() and
main.py's `_useful()` already support address-only records (an address
alone is kept -- a name is not required).

Owner-name enrichment: investigated and ruled out (2026-08-29). Three
free/no-login sources were checked, none work:
  1. collin.tx.publicsearch.us has NO "Foreclosures" department at all
     (its department list is only Property Records/Plats/Assumed Names/
     Census Records/Marriage -- department=FC returns a genuine error
     page). This is why the county runs the separate apps2 consumer tool
     in the first place.
  2. Its "Property Records" department (department=RP) DOES return
     Grantor/Grantee names directly in the table -- no OCR needed -- but
     has NO address field (only a Legal Description: subdivision/lot/
     block), and "NOTICE" is a broad catch-all doc type across ~30k
     records/60-day window (same ambiguity as Ellis's doctype problem --
     see scrapers/ellis.py), so there is no reliable, low-cost way to
     identify which of those rows are even NTS filings, let alone match
     them to an apps2 address (apps2 exposes no legal description either
     -- verified via probe_collin_apps2_fields.py -- so there's no shared
     join key between the two sources).
  3. Collin CAD (collincad.org), the standard real-world skip-tracing
     cross-reference (county tax-appraisal owner-of-record by address),
     is behind a Cloudflare "Checking your browser..." interstitial (403)
     -- same bot-verification category already ruled out for other
     counties; not something this project will attempt to solve.
See probe_collin_publicsearch.py, probe_collin_apps2_fields.py, and
probe_collin_cad.py for the investigation. A paid, non-scraping property
data/skip-trace API (looked up by address) is the realistic remaining path
if owner names for Collin become a priority -- not a portal to scrape.

Property Type filtering -- the hint vs. reality:
  The business's hint was to select "Residential Single Family" and
  "Residential Mobile Home" and exclude "Residential Townhomes" via the
  portal's own Property Type filter UI. Probing confirmed the filter DOES
  exist with (close to) that exact wording, plus more categories than the
  hint mentioned. Full observed option list (exact text, with a live
  snapshot count that will drift day to day):
    Commercial (C3), Commercial (F1), Residential Duplex (B2),
    Residential Mobile Home (A2), Residential Single Family (A1),
    Residential Single Family (C1), Residential Townhomes (A4)
  Note there are TWO "Residential Single Family" entries under different
  assessor codes (A1 and C1) -- both textually match the business's hint,
  so both are included (matched by category-name PREFIX, not by hardcoding
  specific codes, so a future new code under the same category name is
  still included automatically). Also note: the "All Properties Types"
  facet total (~393) is well below the portal's grand total (~712) -- most
  of that gap is records with NO property type classified at all. Those are
  excluded by construction (they match no allow-list prefix), which is the
  conservative reading of the business's hint (only include what's
  confirmed as one of the target residential categories).
  IMPLEMENTATION CHOICE: rather than drive the Property Type filter's popover
  in the UI (MudBlazor renders its options as plain non-form <div> "list
  items", not real <input type=checkbox> elements -- there's no stable,
  simple way to "check a box" there, and no URL/query-param shortcut to
  preset it), this scraper pulls EVERY page unfiltered and applies the
  Property Type allow-list in Python. This is simpler, avoids a fragile
  custom-multiselect UI automation, and is a genuine defense-in-depth: even
  if the portal's UI changes, the filter is enforced in code either way.

Pagination -- verified mechanics (SYSTEM_GUIDE.md Sec.9 bug #2 -- do not
guess this, it silently truncates):
  The pager exposes STABLE, unique aria-labels: "First page", "Previous
  page", "Page N", "Current page N", "Next page", "Last page". The row of
  visible numbered buttons (e.g. 1,2,3,4,5,6,...,28,29) is a fixed/decorative
  window -- clicking the "..." does nothing and it does NOT slide to expose
  middle pages -- so the only reliable way to reach every page is to click
  "Next page" repeatedly. We confirm each click by reading the "Current
  page N" label (ground truth), NOT by diffing displayed content -- a real
  production run showed the very FIRST "Next page" click after initial load
  can take up to ~15-25s (a one-time server/SignalR warm-up cost), while
  every subsequent click was ~0.2-0.3s. A generous per-click timeout plus
  one retry (matching the guide's "re-check once before trusting" guidance)
  absorbed that cleanly in that run. As a further hardening on top of that
  (see `_click_next`'s docstring), if a slow click's response ever lands
  late enough to overshoot past the expected next page, we continue from
  wherever we verifiably landed (logging which page(s) may have been
  skipped) rather than treating anything short of an exact +1 as a fatal
  "pagination stalled" -- a bounded, visible degradation instead of
  abandoning the rest of the scrape over one timing race.
"""
import re
import time
from datetime import date
from typing import Dict, List, Optional

from .base import BaseScraper, is_residential_lead, launch_chromium

SEARCH_URL = "https://apps2.collincountytx.gov/ForeclosureNotices"

# Category-name prefixes to keep (case-insensitive, matched against the
# "Property Type:" text with its trailing "(CODE)" still attached -- prefix
# match means both "Residential Single Family (A1)" and "Residential Single
# Family (C1)" are included without hardcoding either code). Deliberately
# excludes "Residential Townhomes" per the business's explicit hint, and
# excludes Commercial/Duplex/unclassified by simply not matching them.
PROPERTY_TYPE_ALLOW_PREFIXES = (
    'RESIDENTIAL SINGLE FAMILY',
    'RESIDENTIAL MOBILE HOME',
)

MAX_PAGES = 120                 # generous safety cap; ~29 pages observed live
CARD_WAIT_TIMEOUT_MS = 30_000   # initial-load poll budget (bug #1 defense)
NEXT_CLICK_TIMEOUT_S = 25       # per-click poll budget (first click can be slow)

# Known multi-word Collin County place names, used ONLY as a fallback when an
# address line's embedded (USPS/postal) city doesn't match the separately
# -labeled, authoritative City: field (see module docstring -- e.g. a real
# observed row: address line ends "...CELINA, TX 75009" while City: says
# "Weston"). Without this, a generic "last word before the comma" split
# would chop a real two-word city in half (e.g. slice "BLUE RIDGE" down to
# just "RIDGE", corrupting the recovered street). Mirrors the precedent set
# by the reference Harris scraper (harris_extract.py's HOUSTON_CITIES) for
# the same class of problem. Extend this list if a new mismatch turns up.
COLLIN_MULTIWORD_CITIES = [
    'ROYSE CITY', 'BLUE RIDGE', 'NEW HOPE', 'LOWRY CROSSING', 'SAINT PAUL',
    'ST PAUL', 'VAN ALSTYNE',
]

_PARSE_ROWS_JS = """() => {
    const out = [];
    const cells = Array.from(document.querySelectorAll('td.mud-table-cell'));
    for (const td of cells) {
        const addrP = td.querySelector('p.list-header') || td.querySelector('p');
        const addr = addrP ? addrP.textContent.replace(/\\s+/g, ' ').trim() : '';
        const get = (label) => {
            const spans = Array.from(td.querySelectorAll('span.list-subheader'));
            const sp = spans.find(s => (s.textContent || '').trim() === label);
            if (!sp || !sp.parentElement) return '';
            const parentText = sp.parentElement.textContent || '';
            return parentText.replace(sp.textContent, '').replace(/\\s+/g, ' ').trim();
        };
        out.push({
            address: addr,
            city: get('City:'),
            sale_date: get('Sale Date:'),
            file_date: get('File Date:'),
            property_type: get('Property Type:'),
        });
    }
    return out;
}"""


def _property_type_allowed(pt: str) -> bool:
    return (pt or '').upper().strip().startswith(PROPERTY_TYPE_ALLOW_PREFIXES)


def _fmt_date(raw: str) -> str:
    raw = (raw or '').strip()
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', raw)
    if m:
        mm, dd, yy = m.groups()
        return f"{int(mm):02d}/{int(dd):02d}/{yy}"
    return raw


def _parse_mmddyyyy(raw: str):
    raw = (raw or '').strip()
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', raw)
    if not m:
        return None
    mm, dd, yy = (int(x) for x in m.groups())
    try:
        return date(yy, mm, dd)
    except ValueError:
        return None


def _parse_address_line(addr_line: str, known_city: str):
    """Split a results-row address line into (street, zip_code).

    Prefers anchoring on the KNOWN city (the separately-labeled City: field,
    which is authoritative -- see module docstring on the Celina/Weston
    postal-vs-boundary-city mismatch) rather than guessing where a
    multi-word city name (e.g. "Royse City", "Blue Ridge") starts within the
    address line -- a naive "last word before the comma" regex would slice
    a two-word city in half if we didn't already know its real length."""
    addr_line = re.sub(r'\s+', ' ', addr_line or '').strip()
    if not addr_line:
        return '', ''
    m_zip = re.search(r'(\d{5})\s*$', addr_line)
    zip_code = m_zip.group(1) if m_zip else ''

    known_city = (known_city or '').strip()
    if known_city:
        pat = re.compile(re.escape(known_city) + r'\s*,?\s*TX\s+' + re.escape(zip_code) + r'\s*$',
                          re.I)
        m = pat.search(addr_line)
        if m:
            return addr_line[:m.start()].strip(' ,'), zip_code

    # Fallback 1: a known multi-word place name (see COLLIN_MULTIWORD_CITIES)
    # -- checked before the generic single-word split so a genuine two-word
    # city isn't chopped in half when it doesn't match the labeled City:.
    for name in COLLIN_MULTIWORD_CITIES:
        name_pat = re.escape(name).replace(r'\ ', r'\s+')
        pat = re.compile(r'\b' + name_pat + r'\s*,?\s*TX\s+' + re.escape(zip_code) + r'\s*$', re.I)
        m = pat.search(addr_line)
        if m:
            return addr_line[:m.start()].strip(' ,'), zip_code

    # Fallback 2: strip a trailing "<letters-only words>, TX <zip>" tail.
    # (Greedy-then-backtrack means this naturally grabs just the LAST word,
    # e.g. "Allen" not "Way Allen" -- correct for single-word cities, but
    # would incorrectly slice an unlisted multi-word city; see the two
    # fallbacks above for the cases we know about.)
    m2 = re.match(r'^(.*\S)\s+([A-Za-z][A-Za-z .]*),?\s*TX\s+\d{5}\s*$', addr_line, re.I)
    if m2:
        return m2.group(1).strip(' ,'), zip_code
    return addr_line, zip_code


class CollinCountyScraper(BaseScraper):

    def __init__(self):
        super().__init__('Collin')

    def scrape(self, target_date: date) -> List[Dict]:
        self.logger.info(f"Scraping Collin County for {target_date}")
        self.logger.info(
            "Collin: this portal exposes no owner name and no document ID in its "
            "public UI (verified during investigation -- not a scraper bug); "
            "records are address-only leads. See scrapers/collin.py docstring.")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.logger.error("Collin: Playwright not installed")
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

                self.logger.info(f"Collin: loading {SEARCH_URL}")
                # NOTE: do NOT wait_until='networkidle' here -- the page embeds a
                # Leaflet/ArcGIS map that keeps fetching tiles, so network activity
                # may never go idle within a sane timeout (confirmed during
                # investigation: this alone timed out a whole probe run). Poll for
                # real content instead (see _wait_for_cards).
                page.goto(SEARCH_URL, wait_until='domcontentloaded', timeout=45_000)

                raw_rows = self._scrape_all_pages(page)
                browser.close()
        except Exception as exc:
            self.logger.error(f"Collin: error: {exc}", exc_info=True)
            return []

        records = self._build_records(raw_rows, target_date)
        self.logger.info(f"Collin: {len(records)} final records")
        return records

    # ── Pagination + row extraction ─────────────────────────────────────────

    def _wait_for_cards(self, page) -> int:
        """Poll until real row content is present, or the portal explicitly
        says there are no results. A fixed sleep here previously (system
        guide bug #1) silently read a slow-loading county as 0 results."""
        deadline = time.monotonic() + CARD_WAIT_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            try:
                n = page.evaluate(
                    "() => document.querySelectorAll('td.mud-table-cell').length")
            except Exception:
                n = 0
            if n > 0:
                return n
            try:
                no_results = page.evaluate(
                    "() => /no results|no records|nothing found/i"
                    ".test(document.body.innerText||'')")
            except Exception:
                no_results = False
            if no_results:
                self.logger.info("Collin: portal explicitly reports no results.")
                return 0
            page.wait_for_timeout(400)
        self.logger.warning(
            f"Collin: rows never appeared within {CARD_WAIT_TIMEOUT_MS/1000:.0f}s -- "
            "treating as empty (may be a slow portal, not genuinely 0 results).")
        return 0

    def _row_count(self, page) -> int:
        try:
            return page.evaluate("() => document.querySelectorAll('td.mud-table-cell').length")
        except Exception:
            return 0

    def _current_page_number(self, page) -> Optional[int]:
        try:
            label = page.evaluate("""() => {
                const b = Array.from(document.querySelectorAll('button')).find(
                    b => (b.getAttribute('aria-label')||'').startsWith('Current page'));
                return b ? b.getAttribute('aria-label') : null;
            }""")
        except Exception:
            return None
        if not label:
            return None
        m = re.search(r'\d+', label)
        return int(m.group()) if m else None

    def _next_disabled(self, page) -> bool:
        try:
            return bool(page.evaluate("""() => {
                const b = document.querySelector('button[aria-label="Next page"]');
                return !b || b.disabled;
            }"""))
        except Exception:
            return True

    def _click_next(self, page, current_page: int) -> Optional[int]:
        """Click "Next page" and return the page number actually reached, or
        None if it never advanced past `current_page` at all (genuinely
        stuck) after both attempts.

        Deliberately tolerant of landing PAST `current_page + 1`: the first
        click after initial load can take up to ~15-25s to resolve (a one
        -time server/SignalR warm-up, confirmed in a real production run --
        see module docstring), so it's theoretically possible for that slow
        response to finally land just as this method's own retry fires a
        second click, overshooting by more than one page. Treating "didn't
        land on EXACTLY current_page + 1" as fatal would abandon the ENTIRE
        rest of the scrape over one timing race; instead we continue from
        wherever we verifiably are (losing at most the skipped page's rows,
        not everything after it) and log a warning so it's visible, not
        silent. Even a skipped page isn't permanent data loss here: this
        portal lists every currently-open notice, not just new-since
        -yesterday ones, so a page missed today is picked up on tomorrow's
        run (see SYSTEM_GUIDE.md bug #1/#2 -- prefer a loud, recoverable
        degradation over an all-or-nothing failure)."""
        for attempt in (1, 2):
            try:
                page.locator('button[aria-label="Next page"]').click(timeout=10_000)
            except Exception as e:
                self.logger.warning(f"Collin: Next-page click error (attempt {attempt}): {e}")
                continue
            deadline = time.monotonic() + NEXT_CLICK_TIMEOUT_S
            while time.monotonic() < deadline:
                cur = self._current_page_number(page)
                if cur is not None and cur > current_page and self._row_count(page) > 0:
                    if cur != current_page + 1:
                        self.logger.warning(
                            f"Collin: expected page {current_page + 1} but landed on "
                            f"page {cur} (likely a slow prior response resolving late) "
                            f"-- page(s) {current_page + 1} to {cur - 1} may have been "
                            "skipped this run; continuing from here (self-heals on "
                            "tomorrow's run since this portal lists all open notices, "
                            "not just new ones).")
                    return cur
                page.wait_for_timeout(300)
            self.logger.warning(
                f"Collin: page did not advance past {current_page} within "
                f"{NEXT_CLICK_TIMEOUT_S}s (attempt {attempt}; now at page "
                f"{self._current_page_number(page)}, rows={self._row_count(page)}).")
        return None

    def _parse_rows(self, page) -> List[Dict]:
        try:
            return page.evaluate(_PARSE_ROWS_JS) or []
        except Exception as e:
            self.logger.warning(f"Collin: row parse failed: {e}")
            return []

    def _scrape_all_pages(self, page) -> List[Dict]:
        rows: List[Dict] = []
        n = self._wait_for_cards(page)
        if n == 0:
            return rows

        page_num = 1
        while True:
            page_rows = self._parse_rows(page)
            first_addr = page_rows[0]['address'] if page_rows else '(none)'
            self.logger.info(
                f"Collin: page {page_num} -> {len(page_rows)} rows (first: {first_addr!r})")
            rows.extend(page_rows)

            if self._next_disabled(page):
                self.logger.info(f"Collin: reached the last page ({page_num}); Next is disabled.")
                break
            if page_num >= MAX_PAGES:
                self.logger.error(
                    f"Collin: hit MAX_PAGES={MAX_PAGES} safety cap -- stopping early "
                    "(results may be truncated; investigate if this recurs).")
                break
            new_page = self._click_next(page, page_num)
            if new_page is None:
                self.logger.error(
                    f"Collin: pagination stalled after page {page_num} -- stopping "
                    "early (results may be truncated).")
                break
            page_num = new_page

        return rows

    # ── Filtering + record building ─────────────────────────────────────────

    def _build_records(self, raw_rows: List[Dict], target_date: date) -> List[Dict]:
        records = []
        kept_type = kept_upcoming = kept_address = 0
        for r in raw_rows:
            pt = r.get('property_type', '')
            if not _property_type_allowed(pt):
                continue
            kept_type += 1

            sale_d = _parse_mmddyyyy(r.get('sale_date', ''))
            if sale_d is None or sale_d < target_date:
                continue
            kept_upcoming += 1

            street, zip_code = _parse_address_line(r.get('address', ''), r.get('city', ''))
            if not re.match(r'^\d', street):
                self.logger.info(
                    f"Collin: skipping row with a non-street address value: "
                    f"{r.get('address', '')!r}")
                continue
            kept_address += 1

            rec = self.build_record(
                address=street,
                city=(r.get('city', '') or '').strip(),
                state='TX',
                zip_code=zip_code,
                file_date=_fmt_date(r.get('file_date', '')),
                sale_date=_fmt_date(r.get('sale_date', '')),
            )
            # No owner name is ever available on this portal (see module
            # docstring) so this is a no-op today (blank name always passes)
            # -- kept for parity with every other scraper and as a safety
            # net if the portal ever starts exposing a name.
            full_name = f"{rec['first_name']} {rec['last_name']}".strip()
            if not is_residential_lead(full_name):
                self.logger.info(f"Collin: dropping entity-looking name {full_name!r}")
                continue

            records.append(rec)

        self.logger.info(
            f"Collin: {len(raw_rows)} rows scanned -> {kept_type} matched target property "
            f"types -> {kept_upcoming} upcoming (sale_date >= {target_date}) -> "
            f"{kept_address} had a usable street address -> {len(records)} final records")
        return records
