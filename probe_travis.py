"""
Probe v3 -- Travis County: drive the real Quick Search form for Foreclosures.

Findings from v1+v2:
  - tccsearch.org (assigned portal) is behind an ACTIVE Cloudflare bot
    challenge ("Just a moment... Performing security verification") on both
    plain `requests` and Playwright, from the GitHub Actions US runner. This
    is not a simple geo-block -- it's Cloudflare's managed challenge
    (cf-mitigated: challenge), which headless Chromium does not pass. Not
    ruling it out permanently, but it's a bad sign for a daily unattended job.
  - travis.tx.publicsearch.us IS live and official, and its Department
    dropdown genuinely has: Land Records, Assumed Names, Marriage,
    **Foreclosures**. Guessed query codes (department=FC/LR/NOS/FORECLOSURE)
    via direct URL did NOT work cleanly (mix of "Error with search query"
    and blank "Error" pages) -- Travis's tenant likely uses a different
    department code/id than the other 5 counties' publicsearch instances.
  - The /search/advanced route itself 500s on direct navigation but loads
    (blank) via client-side click-through. Quick Search, however, already
    exposes Department + Search Term + Date Range + Search -- no need to
    fight the advanced-search route at all.

v3 goal: drive the QUICK SEARCH form for real (select Foreclosures
department, pick the broadest date-range preset, submit), and read off:
  - the actual resulting URL (so we learn the real query param format for
    fast direct-navigation later, mirroring the other 5 counties' pattern),
  - the results table schema (headers + sample rows), or the empty/error
    state if there's genuinely nothing in that window.
"""
import logging
import os
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

PS_BASE = "https://travis.tx.publicsearch.us"
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
ART_DIR = 'probe_artifacts'


def dump_body_text(page, label, n=60):
    txt = page.evaluate("() => document.body.innerText || ''")
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    log.info(f"=== {label}: body text ({len(lines)} lines, showing first {n}) ===")
    for ln in lines[:n]:
        log.info(f"  | {ln}")


def dump_table(page, label):
    info = page.evaluate("""() => {
        const tables = Array.from(document.querySelectorAll('table')).map(t => ({
            headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim()),
            rowCount: t.querySelectorAll('tr').length,
        }));
        const rows = [];
        const t = document.querySelector('table');
        if (t) {
            for (const tr of Array.from(t.querySelectorAll('tr')).slice(0, 8)) {
                rows.push(Array.from(tr.querySelectorAll('th,td')).map(c => (c.textContent||'').trim()));
            }
        }
        return {tables, rows};
    }""")
    log.info(f"=== {label}: tables={info['tables']} ===")
    for r in info['rows']:
        log.info(f"  ROW: {r}")
    return info


def dump_listbox_options(page):
    return page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('[role="option"]').forEach(el => {
            const t = (el.textContent||'').trim();
            if (t) out.push(t);
        });
        return out;
    }""")


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent=UA, viewport={'width': 1500, 'height': 1900})
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30_000)

        log.info(f"GOTO {PS_BASE}")
        page.goto(PS_BASE, wait_until='networkidle')
        page.wait_for_timeout(1200)

        # 1. Open the Department combobox and select "Foreclosures".
        try:
            dept_field = page.get_by_text('Land Records', exact=False).first
            dept_field.click(timeout=5000)
            page.wait_for_timeout(600)
            opts = dump_listbox_options(page)
            log.info(f"department options (role=option scrape): {opts}")
            page.get_by_role('option', name='Foreclosures', exact=True).click(timeout=5000)
            page.wait_for_timeout(1000)
            page.screenshot(path=f'{ART_DIR}/01_dept_foreclosures_selected.png', full_page=True)
            dump_body_text(page, 'after selecting Foreclosures', n=30)
        except Exception as e:
            log.error(f"department selection failed: {e}", exc_info=True)
            page.screenshot(path=f'{ART_DIR}/01_dept_FAILED.png', full_page=True)

        # 2. Open the Date Range control and pick the broadest preset.
        try:
            date_field = page.get_by_text('Recorded Date', exact=False).first
            date_field.click(timeout=5000)
            page.wait_for_timeout(600)
            opts = dump_listbox_options(page)
            log.info(f"date range options (role=option scrape): {opts}")
            page.screenshot(path=f'{ART_DIR}/02_date_dropdown_open.png', full_page=True)
            # Prefer the broadest window so we maximize the chance of hitting
            # real data on the first real attempt.
            clicked = False
            for label in ('Last 1 Year', 'Last 6 Months', 'Last 3 Months'):
                try:
                    page.get_by_role('option', name=label, exact=True).click(timeout=2000)
                    log.info(f"selected date range preset: {label}")
                    clicked = True
                    break
                except Exception:
                    continue
            if not clicked:
                log.warning("no date range preset matched -- leaving default")
            page.wait_for_timeout(800)
        except Exception as e:
            log.error(f"date range selection failed: {e}", exc_info=True)
            page.screenshot(path=f'{ART_DIR}/02_date_FAILED.png', full_page=True)

        page.screenshot(path=f'{ART_DIR}/03_before_search.png', full_page=True)
        dump_body_text(page, 'before clicking Search', n=30)

        # 3. Click Search and see what we get.
        try:
            search_btn = page.get_by_role('button', name='Search', exact=False).first
            search_btn.click(timeout=5000)
            # Poll for navigation / results rather than a fixed sleep.
            page.wait_for_timeout(4000)
            try:
                page.wait_for_load_state('networkidle', timeout=15_000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            log.info(f"AFTER SEARCH: url={page.url} title={page.title()!r}")
            page.screenshot(path=f'{ART_DIR}/04_after_search.png', full_page=True)
            dump_table(page, 'after search')
            dump_body_text(page, 'after search (full)', n=80)
        except Exception as e:
            log.error(f"search click failed: {e}", exc_info=True)
            page.screenshot(path=f'{ART_DIR}/04_search_FAILED.png', full_page=True)

        browser.close()


if __name__ == '__main__':
    main()
