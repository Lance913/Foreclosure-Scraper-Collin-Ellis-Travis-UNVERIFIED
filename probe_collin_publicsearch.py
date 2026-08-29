"""
Probe -- collin_enrich.py's live dry-run found 0/133 matched addresses on
collin.tx.publicsearch.us. Before trusting that as "the platform genuinely
doesn't cover Collin's foreclosures the same way", dump what the FC results
page there actually returns (department options, table headers, row count,
any "no results" message) -- same diagnostic shape used successfully in the
reference repo's probe_publicsearch.py to debug an earlier Tarrant 0-row
mystery.
"""
import logging
import os
from datetime import date, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(levelname)s: %(message)s')
log = logging.getLogger('PROBE')

ART_DIR = 'probe_artifacts'
SLUG = 'collin'
BASE = f"https://{SLUG}.tx.publicsearch.us"


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    from playwright.sync_api import sync_playwright
    from scrapers.base import launch_chromium

    today = date.today()
    start = (today - timedelta(days=60)).strftime('%Y%m%d')
    end = today.strftime('%Y%m%d')
    results_url = f"{BASE}/results?department=FC&recordedDateRange={start},{end}&searchType=advancedSearch"

    with sync_playwright() as pw:
        browser = launch_chromium(pw)
        ctx = browser.new_context(user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ))
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30_000)

        log.info(f"Loading {BASE} ...")
        resp = page.goto(BASE, wait_until='domcontentloaded', timeout=30_000)
        log.info(f"status={resp.status if resp else None} final_url={page.url!r} title={page.title()!r}")
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(800)

        log.info("Loading /search/advanced to dump department options...")
        page.goto(BASE + '/search/advanced')
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(1500)
        depts = page.evaluate("""() => {
            const out=[];
            const lb=document.querySelector('#department-listbox');
            if(lb) for(const o of lb.querySelectorAll('[role="option"],li'))
                out.push({text:(o.textContent||'').trim(), value:o.getAttribute('data-value')||o.getAttribute('value')||''});
            return out;
        }""")
        log.info(f"department options (text/value): {depts}")

        def dump_results(url, label):
            log.info(f"Loading {label} -> {url}")
            page.goto(url)
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(5000)
            log.info(f"[{label}] final url: {page.url!r} title={page.title()!r}")
            info = page.evaluate("""() => {
                const tables=[...document.querySelectorAll('table')].map(t=>({
                    headers:[...t.querySelectorAll('th')].map(h=>(h.textContent||'').trim()),
                    rows: t.querySelectorAll('tr').length
                }));
                const body=(document.body.innerText||'');
                const nores=/no\\s+results|0\\s+results|no\\s+records|did not match/i.test(body);
                const m=body.match(/([\\d,]+)\\s+results/i);
                return {tables, noResultsMsg:nores, countPhrase: m?m[0]:'(none)',
                        bodyHead: body.split('\\n').map(s=>s.trim()).filter(Boolean).slice(0,50)};
            }""")
            log.info(f"[{label}] count={info['countPhrase']} noResultsMsg={info['noResultsMsg']}")
            for t in info['tables']:
                log.info(f"[{label}] table headers={t['headers']} rows={t['rows']}")
            rows = page.evaluate("""() => {
                const t=document.querySelector('table'); if(!t) return [];
                const heads=[...t.querySelectorAll('th')].map(h=>(h.textContent||'').trim());
                const out=[heads];
                for(const tr of [...t.querySelectorAll('tr')].slice(1,10))
                    out.push([...tr.querySelectorAll('td')].map(td=>(td.textContent||'').trim()));
                return out;
            }""")
            for r in rows:
                log.info(f"[{label}] ROW: {r}")
            page.screenshot(path=f'{ART_DIR}/01_{label}.png', full_page=True)
            if not info['tables'] or info['noResultsMsg']:
                log.info(f"[{label}] BODY TEXT (first 50 lines):")
                for ln in info['bodyHead']:
                    log.info(f"  | {ln}")

        dump_results(results_url, 'FC')

        # FC errored (department doesn't exist for Collin) -- try Property
        # Records (typical publicsearch.us dept code 'RP') with a doc-type
        # filter for trustee-sale notices, since a county without a
        # dedicated Foreclosures department may still index NTS filings
        # under general real-property recordings.
        rp_url = (f"{BASE}/results?department=RP"
                  f"&recordedDateRange={start},{end}&searchType=advancedSearch")
        dump_results(rp_url, 'RP')

        browser.close()

    log.info("Probe complete.")


if __name__ == '__main__':
    main()
