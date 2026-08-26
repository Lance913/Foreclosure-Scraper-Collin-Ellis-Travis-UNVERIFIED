"""
Probe v1 -- Ellis County foreclosure portal investigation.

Per the probate-repo investigation (SYSTEM_GUIDE.md §7 step 1), Ellis
County's real-property recording search is NOT publicsearch.us -- it's a
different vendor, "AcclaimWeb", at:

    https://ellisccktxpublicsearch.us/AcclaimWeb/

(publicsearch.us is a separate system Ellis does NOT use for property
records -- the probate repo's Ellis scraper uses a THIRD, unrelated vendor,
LGS Online Solutions, for county-court/probate records. Don't confuse the
three.)

This probe, per SYSTEM_GUIDE.md §6, dumps (in order, don't skip steps):
  1. The landing page's real search form -- every input/select/button, any
     "Document Type"/"Record Type" selector, and whether guest access is
     enough or a free account is required for date-range/doc-type search.
  2. If a search is reachable, the real results table headers + sample rows.
  3. Whether Notice of Trustee's Sale filings expose owner name/address
     directly in the table, or need a document click-through (+ possibly
     OCR) the way the publicsearch.us counties in the sister repo do.

Nothing here assumes AcclaimWeb behaves like publicsearch.us -- different
vendor, verify from scratch.
"""
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
log = logging.getLogger('PROBE')

ART_DIR = 'probe_artifacts'
BASE_URL = 'https://ellisccktxpublicsearch.us/AcclaimWeb/'


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


def dump_body_text(page, label, n=100):
    txt = page.evaluate("() => document.body.innerText || ''")
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    log.info(f"=== {label}: body text ({len(lines)} lines, showing first {n}) ===")
    for ln in lines[:n]:
        log.info(f"  | {ln}")
    return lines


def dump_form_fields(page, label):
    fields = page.evaluate("""
        () => {
            const out = [];
            document.querySelectorAll('input, select, button, textarea').forEach(el => {
                out.push({
                    tag: el.tagName, type: el.type || '', id: el.id || '',
                    name: el.name || '', placeholder: el.placeholder || '',
                    aria: el.getAttribute('aria-label') || '',
                    text: (el.textContent || el.value || '').trim().slice(0, 60),
                    visible: !!(el.offsetWidth || el.offsetHeight),
                });
            });
            return out;
        }
    """)
    log.info(f"=== {label}: {len(fields)} form field(s) ===")
    for f in fields:
        log.info(f"  | {f}")

    selects = page.evaluate("""
        () => Array.from(document.querySelectorAll('select')).map(s => ({
            id: s.id, name: s.name,
            options: Array.from(s.options).map(o => o.textContent.trim())
        }))
    """)
    log.info(f"=== {label}: {len(selects)} <select> element(s) with options ===")
    for s in selects:
        log.info(f"  | {s}")


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    from playwright.sync_api import sync_playwright
    from scrapers.base import launch_chromium

    with sync_playwright() as pw:
        browser = launch_chromium(pw)
        context = browser.new_context(user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ))
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30_000)

        log.info(f"Loading {BASE_URL} ...")
        resp = page.goto(BASE_URL, wait_until='networkidle', timeout=30_000)
        log.info(f"status={resp.status if resp else None} final_url={page.url!r} title={page.title()!r}")
        page.wait_for_timeout(1500)
        shot(page, '01_landing')
        dump_body_text(page, '01_landing')
        dump_form_fields(page, '01_landing')

        # Dismiss the "your browser is out of date... Update browser Ignore"
        # banner first -- it likely overlays the page and intercepts clicks
        # on anything underneath (same class of issue as Travis's onboarding
        # tour popup).
        try:
            ignore_btn = page.get_by_text('Ignore', exact=True)
            if ignore_btn.count() > 0:
                log.info("Dismissing browser-warning banner via 'Ignore'")
                ignore_btn.first.click(timeout=5000)
                page.wait_for_timeout(500)
        except Exception as e:
            log.warning(f"Dismissing banner failed (non-fatal): {str(e)[:200]}")

        # Look for a "Property (By Name)" or similar search entry point and
        # click into it to see the real search form (guest, no login). There
        # can be multiple matching elements (hidden templates/duplicates) --
        # pick the first genuinely VISIBLE one rather than assuming .first.
        try:
            candidates = ['Property (By Name)', 'Property Search', 'Search', 'Advanced Search']
            clicked = False
            for text in candidates:
                loc = page.get_by_text(text, exact=False)
                n = loc.count()
                if n == 0:
                    continue
                log.info(f"'{text}' matched {n} element(s) -- checking visibility of each")
                for i in range(n):
                    el = loc.nth(i)
                    is_vis = el.is_visible()
                    log.info(f"  [{i}] visible={is_vis}")
                    if is_vis:
                        log.info(f"Clicking visible match [{i}] for {text!r}")
                        el.click(timeout=8000)
                        clicked = True
                        break
                if clicked:
                    break
            if not clicked:
                log.warning("No VISIBLE search entry point found -- check '01_landing' dump above.")
            page.wait_for_timeout(2000)
            shot(page, '02_after_click_search_entry')
            dump_body_text(page, '02_after_click_search_entry')
            dump_form_fields(page, '02_after_click_search_entry')
        except Exception as e:
            log.error(f"Clicking into search failed: {str(e)[:300]}", exc_info=True)

        browser.close()

    log.info("Probe complete. Read the body-text and form-field dumps above before writing any scraper code.")


if __name__ == '__main__':
    main()
