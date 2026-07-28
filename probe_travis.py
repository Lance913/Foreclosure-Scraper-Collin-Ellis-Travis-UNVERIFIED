"""
Probe v2 -- Travis County portal reconnaissance.

Findings from v1:
  - tccsearch.org (assigned portal) fully TIMES OUT (30s, no response at all)
    even from the GitHub Actions Azure US runner -- not a clean 403. Need to
    characterize the failure mode (DNS/TCP/TLS/WAF-hang) with a longer
    timeout and a plain HTTP client (no browser overhead).
  - travis.tx.publicsearch.us is LIVE, and genuinely official ("COUNTY CLERK
    Dyana Limon-Mercado" -- the real Travis County Clerk). Quick Search shows
    a "Department" dropdown defaulting to "Land Records". The
    /search/advanced route 500s with a raw "Internal Server Error" (not a
    styled error page -- looks like a real backend bug or an unconfigured
    route for this tenant).

v2 goals:
  1. tccsearch.org: plain `requests` GET with a long timeout + full header/
     status dump (fast, cheap, disambiguates hang type). Also retry via
     Playwright with `domcontentloaded` (not `networkidle`, which can hang
     forever on background polling) and a longer timeout.
  2. travis.tx.publicsearch.us: enumerate the REAL department dropdown
     options on the working Quick Search page (not the broken /search/
     advanced route). Then test the direct results-URL pattern (used
     successfully for Bexar/Dallas/Tarrant/Denton/Johnson) for BOTH a
     Foreclosures-like department (if one exists in the dropdown) AND Land
     Records (known-good control), to see if the query API works even
     though the advanced-search form page doesn't.
"""
import logging
import os
from datetime import date, timedelta
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

TCC_BASE = "https://www.tccsearch.org"
TCC_SEARCH = f"{TCC_BASE}/RealEstate/SearchEntry.aspx"
PS_BASE = "https://travis.tx.publicsearch.us"

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

ART_DIR = 'probe_artifacts'
TODAY = date.today()


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
            for (const tr of Array.from(t.querySelectorAll('tr')).slice(0, 6)) {
                rows.push(Array.from(tr.querySelectorAll('th,td')).map(c => (c.textContent||'').trim()));
            }
        }
        return {tables, rows};
    }""")
    log.info(f"=== {label}: tables={info['tables']} ===")
    for r in info['rows']:
        log.info(f"  ROW: {r}")
    return info


# ── PART 1: tccsearch.org diagnostics ───────────────────────────────────────

def probe_tccsearch_requests():
    import requests
    for url in (TCC_BASE + '/', TCC_SEARCH):
        try:
            log.info(f"[requests] GET {url}")
            r = requests.get(url, headers={
                'User-Agent': UA,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            }, timeout=45, allow_redirects=True)
            log.info(f"[requests] {url} -> status={r.status_code} finalURL={r.url} "
                     f"elapsed={r.elapsed.total_seconds():.1f}s bytes={len(r.content)}")
            log.info(f"[requests] headers={dict(r.headers)}")
            log.info(f"[requests] body[:500]={r.text[:500]!r}")
        except Exception as e:
            log.error(f"[requests] {url} FAILED: {type(e).__name__}: {e}")


def probe_tccsearch_playwright(pw):
    browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(user_agent=UA, viewport={'width': 1400, 'height': 1900})
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page.set_default_timeout(45_000)

    for label, url in (('tcc root', TCC_BASE + '/'), ('tcc search', TCC_SEARCH)):
        try:
            log.info(f"[pw] GOTO {url} (wait_until=domcontentloaded, timeout=45s)")
            resp = page.goto(url, wait_until='domcontentloaded', timeout=45_000)
            log.info(f"[pw] {label}: status={resp.status if resp else '?'} "
                     f"finalURL={page.url} title={page.title()!r}")
            if resp:
                log.info(f"[pw] {label}: response headers={dict(resp.headers)}")
            page.wait_for_timeout(1500)
            os.makedirs(ART_DIR, exist_ok=True)
            fname = f"{ART_DIR}/{label.replace(' ', '_')}.png"
            page.screenshot(path=fname, full_page=True)
            dump_body_text(page, label, n=40)
        except Exception as e:
            log.error(f"[pw] {label} FAILED: {type(e).__name__}: {e}")
            try:
                page.screenshot(path=f"{ART_DIR}/{label.replace(' ', '_')}_FAILED.png")
            except Exception:
                pass

    browser.close()


# ── PART 2: travis.tx.publicsearch.us deep dive ─────────────────────────────

def probe_publicsearch(pw):
    browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(user_agent=UA, viewport={'width': 1400, 'height': 1900})
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page.set_default_timeout(30_000)

    # 1. Load quick search (known-good) and enumerate the REAL department dropdown.
    try:
        log.info(f"GOTO {PS_BASE}")
        page.goto(PS_BASE, wait_until='networkidle')
        page.wait_for_timeout(1200)
        # Dismiss the "Not sure where to start?" tour popup if present so it
        # doesn't block clicks.
        try:
            page.locator('button:has-text("Take the Tour")').first
            close_btn = page.locator('[aria-label="Close"], button:has(svg)').first
        except Exception:
            pass

        # Find the department field/combobox and click it to open options.
        dept_field = page.get_by_text('Land Records', exact=False).first
        log.info(f"dept_field count={dept_field.count() if dept_field else 'n/a'}")
        dept_field.click(timeout=5000)
        page.wait_for_timeout(800)
        page.screenshot(path=f'{ART_DIR}/dept_dropdown_open.png', full_page=True)

        opts = page.evaluate("""() => {
            // Try common option-list patterns: role=listbox/option, <li>, <ul>.
            const out = [];
            document.querySelectorAll('[role="option"], li[role], ul li').forEach(el => {
                const t = (el.textContent||'').trim();
                if (t) out.push(t);
            });
            return out;
        }""")
        log.info(f"ps travis: department dropdown options (generic scrape): {opts}")

        # Also dump ALL visible text on the page while the dropdown is open --
        # a robust fallback if the option markup doesn't match the selectors above.
        dump_body_text(page, 'ps travis (dropdown open)', n=60)
    except Exception as e:
        log.error(f"department dropdown probe failed: {e}", exc_info=True)

    # 2. Direct results-URL tests -- bypass the broken /search/advanced form
    #    entirely, mirroring the pattern that works for the other 5 counties.
    start = (TODAY - timedelta(days=60)).strftime('%Y%m%d')
    end = TODAY.strftime('%Y%m%d')
    for dept_code in ('FC', 'LR', 'NOS', 'FORECLOSURE'):
        url = f"{PS_BASE}/results?department={dept_code}&recordedDateRange={start},{end}&searchType=advancedSearch"
        try:
            log.info(f"GOTO {url}")
            resp = page.goto(url, wait_until='networkidle', timeout=30_000)
            page.wait_for_timeout(2500)
            log.info(f"ps travis results[{dept_code}]: status={resp.status if resp else '?'} "
                     f"finalURL={page.url} title={page.title()!r}")
            page.screenshot(path=f'{ART_DIR}/results_{dept_code}.png', full_page=True)
            info = dump_table(page, f'ps travis results[{dept_code}]')
            if not info['tables']:
                dump_body_text(page, f'ps travis results[{dept_code}] (no table)', n=40)
        except Exception as e:
            log.error(f"results[{dept_code}] FAILED: {type(e).__name__}: {e}")

    # 3. Click-through "Advanced Search" tab from the quick-search page (real
    #    in-app navigation) instead of a hard page.goto -- in case the SPA
    #    route only works via client-side routing.
    try:
        log.info(f"GOTO {PS_BASE} (for click-through advanced search)")
        page.goto(PS_BASE, wait_until='networkidle')
        page.wait_for_timeout(1000)
        adv_link = page.get_by_text('Advanced Search', exact=False).first
        adv_link.click(timeout=5000)
        page.wait_for_timeout(2000)
        log.info(f"ps travis: after clicking Advanced Search -> url={page.url} title={page.title()!r}")
        page.screenshot(path=f'{ART_DIR}/advanced_clickthrough.png', full_page=True)
        dump_body_text(page, 'ps travis advanced (click-through)', n=50)
    except Exception as e:
        log.error(f"advanced search click-through FAILED: {type(e).__name__}: {e}")

    browser.close()


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    log.info("########## PART 1a: tccsearch.org (plain requests) ##########")
    probe_tccsearch_requests()

    with sync_playwright() as pw:
        log.info("########## PART 1b: tccsearch.org (Playwright) ##########")
        probe_tccsearch_playwright(pw)
        log.info("########## PART 2: travis.tx.publicsearch.us ##########")
        probe_publicsearch(pw)


if __name__ == '__main__':
    main()
