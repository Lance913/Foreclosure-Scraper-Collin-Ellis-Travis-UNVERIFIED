"""
Probe v7 -- exercise the REAL TravisCountyScraper._reach_results_page(),
then go one step further: parse the results table with the actual
publicsearch row-parser, open the first document, and OCR it if an image
shows up -- all in one run to maximize information per (currently
rate-limited-by-GH-billing) iteration.

Written while GitHub Actions runs were blocked by an account-wide billing
issue (unrelated to this code) -- unconfirmed until the next real run:
  - Whether the Sale Date range control behaves like the Department combobox
    (ArrowDown + Enter) or is a different widget (e.g. a raw date picker).
  - The real results table schema for Travis's Foreclosures department.
  - Whether owner name needs OCR (expected, per the platform's established
    pattern for the other 5 counties -- "Parties: No parties found" on the
    doc summary) -- or whether Travis indexes it differently.

This imports the ACTUAL scrapers/counties.py + scrapers/publicsearch.py code
(not a duplicate ad-hoc script), so a successful run here means the real
scraper is already validated, not just the probe.
"""
import io
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
log = logging.getLogger('PROBE')

ART_DIR = 'probe_artifacts'


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


def dump_body_text(page, label, n=80):
    txt = page.evaluate("() => document.body.innerText || ''")
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    log.info(f"=== {label}: body text ({len(lines)} lines, showing first {n}) ===")
    for ln in lines[:n]:
        log.info(f"  | {ln}")
    return lines


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    from playwright.sync_api import sync_playwright
    from scrapers.counties import TravisCountyScraper
    from scrapers.publicsearch import _PARSE_ROWS_JS, launch_chromium

    target_date = date.today()
    scraper = TravisCountyScraper()
    log.info(f"TravisCountyScraper: base_url={scraper.base_url}")

    captured_images = []

    def is_doc_image(u: str) -> bool:
        return '/files/documents/' in u and '/images/' in u and '.png' in u

    with sync_playwright() as pw:
        browser = launch_chromium(pw)
        context = browser.new_context(user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ))
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30_000)
        page.on('response', lambda r: captured_images.append(r.url) if is_doc_image(r.url) else None)

        # 1. Drive the REAL _reach_results_page() implementation.
        try:
            scraper._reach_results_page(page, target_date)
            shot(page, '01_reach_results_page_done')
        except Exception as e:
            log.error(f"_reach_results_page FAILED: {type(e).__name__}: {e}", exc_info=True)
            shot(page, '01_reach_results_page_FAILED')
            dump_body_text(page, 'state after failure', n=60)
            browser.close()
            return

        # 2. Parse with the REAL row parser (matches columns by header name).
        try:
            rows = page.evaluate(_PARSE_ROWS_JS)
            log.info(f"REAL PARSER: {len(rows)} rows parsed")
            for r in rows[:15]:
                log.info(f"  ROW: {r}")
        except Exception as e:
            log.error(f"row parser failed: {e}")
            rows = []

        # 2b. Also dump raw table headers/cells as a cross-check, in case the
        #     named-column matching in _PARSE_ROWS_JS doesn't find Travis's
        #     actual header text (would show blank fields above).
        raw = page.evaluate("""() => {
            const t = document.querySelector('table');
            if (!t) return {headers: [], rowCount: 0, sample: []};
            const headers = Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim());
            const sample = [];
            for (const tr of Array.from(t.querySelectorAll('tr')).slice(0, 6)) {
                sample.push(Array.from(tr.querySelectorAll('th,td')).map(c => (c.textContent||'').trim()));
            }
            return {headers, rowCount: t.querySelectorAll('tr').length, sample};
        }""")
        log.info(f"RAW TABLE: headers={raw['headers']} rowCount={raw['rowCount']}")
        for r in raw['sample']:
            log.info(f"  RAW ROW: {r}")
        if not raw['headers']:
            dump_body_text(page, 'no table found', n=60)

        # 3. If we got rows, open the first doc and see what a detail page
        #    looks like -- specifically whether party names are indexed
        #    (would mean OCR isn't needed) or "No parties found" (OCR needed,
        #    matching the established pattern for this platform).
        if rows and rows[0].get('doc_id'):
            doc_id = rows[0]['doc_id']
            log.info(f"Opening first doc: doc_id={doc_id}")
            try:
                captured_images.clear()
                page.goto(f"{scraper.base_url}/doc/{doc_id}", wait_until='domcontentloaded')
                page.wait_for_timeout(3000)
                shot(page, '02_first_doc')
                dump_body_text(page, 'doc detail page', n=60)

                # Wait a bit more for the page-1 image to show up over the network.
                for _ in range(20):
                    if any(is_doc_image(u) for u in captured_images):
                        break
                    page.wait_for_timeout(300)
                png_url = next((u for u in captured_images if is_doc_image(u)), None)
                log.info(f"doc image captured: {png_url}")

                if png_url:
                    body = context.request.get(png_url).body()
                    log.info(f"downloaded doc image: {len(body)} bytes")
                    with open(f'{ART_DIR}/first_doc_page1.png', 'wb') as f:
                        f.write(body)
                    try:
                        import pytesseract
                        from PIL import Image
                        txt = pytesseract.image_to_string(Image.open(io.BytesIO(body)))
                        log.info(f"=== RAW OCR TEXT (first doc, page 1) ===")
                        for ln in txt.split('\n'):
                            if ln.strip():
                                log.info(f"  OCR| {ln.strip()}")
                    except ImportError:
                        log.warning("pytesseract/PIL not installed in this probe run -- skipping OCR")
                    except Exception as e:
                        log.error(f"OCR failed: {e}")
            except Exception as e:
                log.error(f"doc detail probe failed: {e}", exc_info=True)
                shot(page, '02_first_doc_FAILED')
        else:
            log.info("No rows with a doc_id -- skipping document detail probe.")

        browser.close()


if __name__ == '__main__':
    main()
