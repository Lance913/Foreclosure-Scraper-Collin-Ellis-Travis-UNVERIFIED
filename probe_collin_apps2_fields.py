"""
Probe -- does apps2.collincountytx.gov (scrapers/collin.py's source) expose a
Legal Description / Subdivision / Lot / Block field anywhere in its result
rows that the current scraper isn't capturing? If so, that's a far more
reliable join key against collin.tx.publicsearch.us's Property Records
department (which has Grantor/Grantee names in-table plus a Legal
Description column, but no street address) than fuzzy address matching.

Dumps EVERY span.list-subheader label found in the first several result
rows, not just the 4 the current scraper already parses (City/Sale Date/
File Date/Property Type), plus the full innerHTML of one row for a manual
sanity check.
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(levelname)s: %(message)s')
log = logging.getLogger('PROBE')

ART_DIR = 'probe_artifacts'
URL = "https://apps2.collincountytx.gov/ForeclosureNotices"


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    from playwright.sync_api import sync_playwright
    from scrapers.base import launch_chromium

    with sync_playwright() as pw:
        browser = launch_chromium(pw)
        ctx = browser.new_context(user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ))
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30_000)

        log.info(f"Loading {URL} ...")
        page.goto(URL, wait_until='domcontentloaded', timeout=45_000)
        page.wait_for_timeout(6000)

        labels = page.evaluate("""() => {
            const cells = Array.from(document.querySelectorAll('td.mud-table-cell'));
            const allLabels = new Set();
            for (const td of cells.slice(0, 10)) {
                const spans = Array.from(td.querySelectorAll('span.list-subheader'));
                for (const s of spans) allLabels.add((s.textContent||'').trim());
            }
            return Array.from(allLabels);
        }""")
        log.info(f"All list-subheader labels seen across first 10 rows: {labels}")

        first_row_html = page.evaluate("""() => {
            const td = document.querySelector('td.mud-table-cell');
            return td ? td.innerHTML : '(none)';
        }""")
        log.info(f"First row full innerHTML:\n{first_row_html}")

        # Also check: does clicking a row expand anything with more detail?
        # (module docstring says no, but double-check with a fresh probe.)
        page.screenshot(path=f'{ART_DIR}/01_first_rows.png', full_page=False)

        browser.close()

    log.info("Probe complete.")


if __name__ == '__main__':
    main()
