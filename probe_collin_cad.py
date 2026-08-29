"""
Probe -- Collin Central Appraisal District (collincad.org) property search.
CAD (county tax appraisal) records are public and free, and index the
CURRENT OWNER OF RECORD for every parcel by address -- the standard
real-world skip-tracing cross-reference for exactly this gap: Collin's own
foreclosure-notice tool (apps2.collincountytx.gov) has a full address but
no owner name (see scrapers/collin.py), and its official-records search
(collin.tx.publicsearch.us) has no Foreclosures department and no address
field in Property Records (see probe_collin_publicsearch.py findings).

This probe only checks REACHABILITY and whether a real address search
returns an owner name without any login/CAPTCHA -- not building a scraper
yet.
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(levelname)s: %(message)s')
log = logging.getLogger('PROBE')

ART_DIR = 'probe_artifacts'
BASE = "https://www.collincad.org"

# A real, currently-open Collin NTS address observed in a prior scrape run
# (see main dry-run logs) -- using a real address to see a real result.
TEST_ADDRESS = "1000 MANCHESTER DR"


def recaptcha_visible(page):
    try:
        return page.evaluate(
            "() => { const el = document.querySelector('.g-recaptcha, iframe[src*=\"recaptcha\"], "
            "iframe[src*=\"hcaptcha\"], iframe[title*=\"challenge\" i]'); "
            "return !!el && el.offsetWidth > 0 && el.offsetHeight > 0; }")
    except Exception:
        return None


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


def dump_body_text(page, label, n=60):
    txt = page.evaluate("() => document.body.innerText || ''")
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    log.info(f"=== {label}: body text ({len(lines)} lines) ===")
    for ln in lines[:n]:
        log.info(f"  | {ln}")


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

        log.info(f"Loading {BASE} ...")
        try:
            resp = page.goto(BASE, wait_until='domcontentloaded', timeout=30_000)
            log.info(f"status={resp.status if resp else None} final_url={page.url!r} title={page.title()!r}")
        except Exception as e:
            log.error(f"goto failed: {str(e)[:300]}")
            return
        page.wait_for_timeout(2000)
        shot(page, '01_landing')
        log.info(f"reCAPTCHA visible on landing: {recaptcha_visible(page)}")

        # Find a property-search link/nav item.
        clicked = False
        for sel in ['a:has-text("Property Search")', 'a:has-text("Search")',
                    '[href*="search" i]', '[href*="property" i]']:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    log.info(f"Clicking property search via {sel!r}: {loc.inner_text()[:60]!r}")
                    loc.click(timeout=8000)
                    clicked = True
                    break
            except Exception:
                continue
        page.wait_for_timeout(2000)
        try:
            page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass
        log.info(f"After search-nav click (clicked={clicked}): url={page.url!r} title={page.title()!r}")
        shot(page, '02_search_page')
        dump_body_text(page, 'search_page')
        log.info(f"reCAPTCHA visible on search page: {recaptcha_visible(page)}")

        # Dump visible form fields so a real query can be built next.
        fields = page.evaluate("""
            () => Array.from(document.querySelectorAll('input, select')).map(el => ({
                tag: el.tagName, type: el.type||'', id: el.id||'', name: el.name||'',
                placeholder: el.placeholder||'', visible: !!(el.offsetWidth || el.offsetHeight)
            })).filter(f => f.visible)
        """)
        log.info(f"Visible form fields: {fields}")

        # Try a real address search if a plausible field exists.
        addr_field = None
        for f in fields:
            hay = f"{f['id']} {f['name']} {f['placeholder']}".lower()
            if 'address' in hay or 'situs' in hay or 'street' in hay:
                addr_field = f
                break
        if addr_field:
            sel = f"#{addr_field['id']}" if addr_field['id'] else f"[name='{addr_field['name']}']"
            log.info(f"Trying address search via {sel!r} with {TEST_ADDRESS!r}")
            try:
                page.fill(sel, TEST_ADDRESS, timeout=8000)
                page.keyboard.press('Enter')
                page.wait_for_timeout(3000)
                try:
                    page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    pass
                log.info(f"After address search: url={page.url!r} title={page.title()!r}")
                shot(page, '03_after_search')
                dump_body_text(page, 'after_search')
                log.info(f"reCAPTCHA visible after search: {recaptcha_visible(page)}")
            except Exception as e:
                log.error(f"Address search failed: {str(e)[:200]}")
        else:
            log.warning("No obvious address input field found -- see fields dump above.")

        browser.close()

    log.info("Probe complete.")


if __name__ == '__main__':
    main()
