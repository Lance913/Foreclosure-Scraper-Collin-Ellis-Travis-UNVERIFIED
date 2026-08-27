"""
Probe -- log into Ellis County AcclaimWeb with the now-real account and
check: (1) does login succeed, (2) does a logged-in session unlock a
date-range+doctype search with no Name required (guest access required a
Name -- see probe_ellis.py's earlier finding), (3) does the reCAPTCHA
disappear once authenticated.
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
log = logging.getLogger('LOGIN-PROBE')

ART_DIR = 'probe_artifacts'
BASE_URL = 'https://ellisccktxpublicsearch.us/AcclaimWeb/'

EMAIL = os.environ['ACCOUNT_EMAIL']
PASSWORD = os.environ['ACCOUNT_PASSWORD']


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


def recaptcha_visible(page):
    return page.evaluate(
        "() => { const el = document.querySelector('.g-recaptcha, iframe[src*=\"recaptcha\"]'); "
        "return !!el && el.offsetWidth > 0 && el.offsetHeight > 0; }")


def dump_body_text(page, label, n=60):
    txt = page.evaluate("() => document.body.innerText || ''")
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    log.info(f"=== {label}: body text ({len(lines)} lines, showing first {n}) ===")
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
        shot(page, '01_login_form')

        page.locator('#Username').fill(EMAIL)
        page.locator('#Password').fill(PASSWORD)
        page.locator('input[type="submit"][value="Log in"]').first.click(timeout=8000)
        page.wait_for_timeout(2000)
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass
        shot(page, '02_after_login')
        dump_body_text(page, '02_after_login')

        body = page.evaluate("() => document.body.innerText || ''")
        logged_in = 'Welcome, Guest' not in body
        log.info(f"Login appears successful (no longer 'Welcome, Guest'): {logged_in}")

        # Go to Property (By Name) search and try a name-less date+doctype search.
        loc = page.get_by_text('Property (By Name)', exact=False)
        clicked = False
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000, force=True)
                clicked = True
                break
        if not clicked:
            log.error("Could not find 'Property (By Name)' search link after login.")
            browser.close()
            return
        page.wait_for_timeout(1500)
        shot(page, '03_search_form_loggedin')
        log.info(f"reCAPTCHA visible while logged in: {recaptcha_visible(page)}")

        from datetime import date, timedelta
        today = date.today()
        start = today - timedelta(days=60)
        page.fill('#FromDatePicker', start.strftime('%m/%d/%Y'))
        page.fill('#ToDatePicker', today.strftime('%m/%d/%Y'))
        log.info(f"Set date range {start:%m/%d/%Y} .. {today:%m/%d/%Y}")

        page.locator('#DocTypesList').select_option(label='NOTICE', force=True, timeout=5000)
        page.evaluate("""
            () => {
                const el = document.getElementById('DocTypesList');
                if (el) el.dispatchEvent(new Event('change', {bubbles: true}));
            }
        """)
        log.info("Selected DocType: NOTICE")
        page.wait_for_timeout(500)
        shot(page, '04_before_submit_loggedin')

        page.locator('#SearchBtn').click(timeout=8000)
        page.wait_for_timeout(2500)
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass
        log.info(f"Post-submit: url={page.url!r} title={page.title()!r}")
        shot(page, '05_results_loggedin')
        dump_body_text(page, '05_results_loggedin', n=150)

        tables = page.evaluate("""
            () => [...document.querySelectorAll('table')].map(t => ({
                id: t.id, cls: (t.className||'').toString().slice(0,60),
                headers: [...t.querySelectorAll('th')].map(h => (h.textContent||'').trim()),
                rowCount: t.querySelectorAll('tr').length
            }))
        """)
        log.info(f"Tables on results page: {tables}")

        browser.close()

    log.info("Login probe complete.")


if __name__ == '__main__':
    main()
