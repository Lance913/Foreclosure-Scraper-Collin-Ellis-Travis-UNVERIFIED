"""
Probe -- Ellis County AcclaimWeb "Add To Cart" checkout flow. The results
grid's only per-row action is a purchase queue for the scanned document
image (see scrapers/ellis.py docstring) -- checking what it actually costs
per document before deciding whether paying for real addresses is worth
it, rather than guessing a price.
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
log = logging.getLogger('PROBE')

ART_DIR = 'probe_artifacts'
BASE_URL = 'https://ellisccktxpublicsearch.us/AcclaimWeb/'

EMAIL = os.environ['ACCOUNT_EMAIL']
PASSWORD = os.environ['ACCOUNT_PASSWORD']


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


def dump_body_text(page, label, n=100):
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
        context = browser.new_context(user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ))
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30_000)

        page.goto(BASE_URL, wait_until='networkidle', timeout=30_000)
        page.wait_for_timeout(1000)
        try:
            page.get_by_text('Ignore', exact=True).first.click(timeout=5000)
        except Exception:
            pass

        # Log in.
        loc = page.get_by_text('Login', exact=False)
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000)
                break
        page.wait_for_timeout(1000)
        page.locator('#Username').fill(EMAIL)
        page.locator('#Password').fill(PASSWORD)
        page.locator('input[type="submit"][value="Log in"]').first.click(timeout=8000)
        page.wait_for_timeout(2000)

        # Go to the date+doctype search, run a broad search.
        loc = page.get_by_text('Property (By Date Range/Doc Type)', exact=False)
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000, force=True)
                break
        page.wait_for_timeout(1500)

        from datetime import date, timedelta
        today = date.today()
        start = today - timedelta(days=14)
        page.fill('#FromDatePicker', start.strftime('%m/%d/%Y'))
        page.fill('#ToDatePicker', today.strftime('%m/%d/%Y'))
        page.locator('#DocTypesList').select_option(label='NOTICE', force=True, timeout=5000)
        page.evaluate("""
            () => {
                const el = document.getElementById('DocTypesList');
                if (el) el.dispatchEvent(new Event('change', {bubbles: true}));
            }
        """)
        page.wait_for_timeout(500)
        page.locator('#SearchBtn').click(timeout=8000)
        page.wait_for_timeout(2500)
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass
        shot(page, '01_results')

        # Add the first row to cart.
        try:
            page.locator('table.k-selectable tr:nth-child(2) button').first.click(timeout=8000)
            page.wait_for_timeout(1500)
            shot(page, '02_after_add_to_cart')
            dump_body_text(page, 'after_add_to_cart')
        except Exception as e:
            log.error(f"Add to cart click failed: {str(e)[:200]}")

        # Look for a cart/checkout link showing price.
        for text in ['Cart', 'Checkout', 'View Cart', 'Items']:
            loc = page.get_by_text(text, exact=False)
            for i in range(loc.count()):
                el = loc.nth(i)
                if el.is_visible():
                    try:
                        log.info(f"Clicking cart-related link: {text!r}")
                        el.click(timeout=5000)
                        page.wait_for_timeout(1500)
                        shot(page, f'03_cart_{text.replace(" ", "_")}')
                        dump_body_text(page, f'cart_{text}')
                    except Exception:
                        pass
                    break

        browser.close()

    log.info("Probe complete.")


if __name__ == '__main__':
    main()
