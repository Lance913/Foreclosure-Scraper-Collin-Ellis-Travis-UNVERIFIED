"""
Collin owner-name enrichment via the county's official records search,
collin.tx.publicsearch.us (same GovOS/publicsearch.us platform already
proven for Bexar/Dallas/Tarrant/Denton/Johnson in the reference
Lance913/Scraper_Python system).

scrapers/collin.py (apps2.collincountytx.gov) stays the PRIMARY source for
Collin -- it lists every currently-open NTS with a full street address, but
has NO owner-name field anywhere in its UI (verified -- see that module's
docstring). This module is a secondary, best-effort ENRICHMENT pass only:
it never adds or drops a lead, it only fills in first_name/last_name on
records apps2.collincountytx.gov already produced, by matching street
address against collin.tx.publicsearch.us's own Foreclosures department
listing (which likewise has no name column in its results table -- the
owner name there requires OCR'ing the recorded document image, same
technique already proven for the other 5 counties).

Because this is enrichment and not the primary source, OCR is spent ONLY on
documents whose table address already matches one of apps2's records --
never a blind "OCR the first N rows" pass. This keeps runtime bounded no
matter how large collin.tx.publicsearch.us's own result set is, and means a
failure here can never regress the address-only baseline (every exception
path below just leaves records exactly as they came in from collin.py).

Address matching is approximate (house number + first street word,
case/punctuation-insensitive) because the two portals don't share a common
document ID -- there is a small theoretical risk of misattributing a name
across two different records that share both a house number and a first
street word (e.g. "108 Garfield Ct" vs "108 Garfield Ln") within the same
lookback window. Accepted as a reasonable best-effort tradeoff; the match
rate is logged every run so this is never silently wrong.
"""
import re
import time
from datetime import date, timedelta
from typing import Dict, List

from .base import launch_chromium, is_residential_lead
from . import publicsearch_extract as pse

BASE_URL = "https://collin.tx.publicsearch.us"
WINDOW_DAYS = 60   # wide enough to cover apps2's "currently open" notice population
MAX_PAGES = 40
OCR_BUDGET_SEC = 180
IMG_WAIT_MS = 7000

_PARSE_ROWS_JS = """() => {
    const out = []; const t = document.querySelector('table'); if (!t) return out;
    const heads = Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim().toLowerCase());
    const idx = (...names) => { for (const n of names) { const i = heads.findIndex(h => h.includes(n)); if (i >= 0) return i; } return -1; };
    const pa = idx('property address','legal');
    const dt = idx('doc type');
    for (const tr of Array.from(t.querySelectorAll('tr')).slice(1)) {
        const c = Array.from(tr.querySelectorAll('td')).map(td => (td.textContent||'').trim());
        if (!c.length) continue;
        const cb = tr.querySelector('input[id^="table-checkbox-"]');
        out.push({
            property_address: (pa >= 0 && pa < c.length) ? c[pa] : '',
            doc_type: (dt >= 0 && dt < c.length) ? c[dt] : '',
            doc_id: cb ? cb.id.replace('table-checkbox-', '') : '',
        });
    }
    return out;
}"""


def _norm_addr(street: str) -> str:
    """'108 Garfield Ct' / '108 GARFIELD CT, CELINA, TX, 75009' -> '108 GARFIELD'
    (house number + first street word -- robust to case/suffix/formatting
    differences between the two portals)."""
    s = re.sub(r'[^A-Za-z0-9 ]', ' ', (street or '').split(',')[0].upper())
    parts = [p for p in s.split() if p]
    if len(parts) < 2 or not parts[0].isdigit():
        return ''
    return f"{parts[0]} {parts[1]}"


def _is_nts(doc_type: str) -> bool:
    dt = (doc_type or '').upper()
    if any(x in dt for x in ('VOID', 'RESCISS', 'RESCIND', 'CANCEL', 'RELEASE', 'WITHDRAW')):
        return False
    return dt == '' or 'FORECLOS' in dt or 'TRUSTEE' in dt


def _next_page(page) -> bool:
    for sel in ['[aria-label="next page"]', 'button:has-text("Next")', 'a:has-text("Next")']:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible() and el.is_enabled():
                el.click()
                page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
    return False


def enrich_with_owner_names(records: List[Dict], target_date: date, logger) -> None:
    """Mutate `records` in place, filling first_name/last_name where a
    matching street address is found on collin.tx.publicsearch.us."""
    wanted: Dict[str, List[Dict]] = {}
    for r in records:
        key = _norm_addr(r.get('address', ''))
        if key:
            wanted.setdefault(key, []).append(r)
    if not wanted:
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Collin enrich: Playwright not installed, skipping name enrichment")
        return

    start = (target_date - timedelta(days=WINDOW_DAYS)).strftime('%Y%m%d')
    end = target_date.strftime('%Y%m%d')
    results_url = (f"{BASE_URL}/results?department=FC"
                   f"&recordedDateRange={start},{end}&searchType=advancedSearch")

    captured: List[str] = []

    def is_doc_image(u: str) -> bool:
        return '/files/documents/' in u and '/images/' in u and '.png' in u

    matched = 0
    try:
        with sync_playwright() as pw:
            browser = launch_chromium(pw)
            context = browser.new_context(user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            ))
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            page.set_default_timeout(30_000)
            page.on('response', lambda r: captured.append(r.url) if is_doc_image(r.url) else None)

            logger.info("Collin enrich: warming publicsearch.us session...")
            page.goto(BASE_URL)
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(800)
            logger.info(f"Collin enrich: FC results -> {results_url}")
            page.goto(results_url)
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(3000)

            hits = []  # (doc_id_internal, [record dicts])
            seen_keys = set()
            for page_num in range(1, MAX_PAGES + 1):
                rows = page.evaluate(_PARSE_ROWS_JS) or []
                if not rows:
                    break
                for row in rows:
                    if not _is_nts(row.get('doc_type', '')):
                        continue
                    key = _norm_addr(row.get('property_address', ''))
                    if key and key in wanted and key not in seen_keys and row.get('doc_id'):
                        seen_keys.add(key)
                        hits.append((row['doc_id'], wanted[key]))
                logger.info(f"Collin enrich: page {page_num} -> {len(rows)} rows, "
                            f"{len(seen_keys)}/{len(wanted)} addresses matched so far")
                if len(seen_keys) >= len(wanted):
                    break
                if not _next_page(page):
                    break

            logger.info(f"Collin enrich: {len(hits)}/{len(wanted)} addresses found on "
                        f"publicsearch.us within a {WINDOW_DAYS}-day window; OCR'ing owner names...")

            deadline = time.monotonic() + OCR_BUDGET_SEC
            for doc_id, recs in hits:
                if time.monotonic() >= deadline:
                    logger.warning("Collin enrich: OCR time budget exhausted, stopping early.")
                    break
                captured.clear()
                try:
                    page.goto(f"{BASE_URL}/doc/{doc_id}", wait_until='domcontentloaded')
                except Exception:
                    continue
                img_deadline = time.monotonic() + IMG_WAIT_MS / 1000
                while time.monotonic() < img_deadline:
                    if any(is_doc_image(u) for u in captured):
                        break
                    page.wait_for_timeout(250)
                png_url = next((u for u in captured if is_doc_image(u)), None)
                if not png_url:
                    continue
                try:
                    body = context.request.get(png_url).body()
                    first, last, _, _, _ = pse.address_and_owner_from_png(body)
                except Exception as e:
                    logger.warning(f"Collin enrich: OCR error doc {doc_id}: {e}")
                    continue
                if not (first or last):
                    continue
                full = f"{first} {last}".strip()
                if not is_residential_lead(full):
                    continue
                for rec in recs:
                    rec['first_name'], rec['last_name'] = first, last
                    matched += 1

            browser.close()
    except Exception as exc:
        logger.error(f"Collin enrich: error: {exc}", exc_info=True)

    logger.info(f"Collin enrich: filled owner name on {matched}/{len(records)} records.")
