"""
One-shot registration for the Ellis County AcclaimWeb portal, so future
scrapes can log in and get full search options (guest access is limited to
a Name-required search -- see probe_ellis.py's findings). Reads the account
email/password from env vars so nothing sensitive is hardcoded.

Fields confirmed via probe_ellis_register.py: AgentFirstName, AgentLastName,
AgentEmail, AgentEmailConfirmation, AgentPassword, AgentPasswordConfirm.
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
log = logging.getLogger('REGISTER')

ART_DIR = 'probe_artifacts'
BASE_URL = 'https://ellisccktxpublicsearch.us/AcclaimWeb/'

EMAIL = os.environ['ACCOUNT_EMAIL']
PASSWORD = os.environ['ACCOUNT_PASSWORD']
FIRST_NAME = os.environ.get('ACCOUNT_FIRST_NAME', '3G')
LAST_NAME = os.environ.get('ACCOUNT_LAST_NAME', 'Leads')


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


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

        loc = page.get_by_text('Login', exact=False)
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000)
                break
        page.wait_for_timeout(1000)

        page.get_by_text('Register', exact=False).first.click(timeout=8000)
        page.wait_for_timeout(1000)
        shot(page, '01_register_form_blank')

        page.locator('#AgentFirstName').fill(FIRST_NAME)
        page.locator('#AgentLastName').fill(LAST_NAME)
        page.locator('#AgentEmail').fill(EMAIL)
        page.locator('#AgentEmailConfirmation').fill(EMAIL)
        page.locator('#AgentPassword').fill(PASSWORD)
        page.locator('#AgentPasswordConfirm').fill(PASSWORD)
        shot(page, '02_register_form_filled')

        recaptcha_visible = page.evaluate(
            "() => { const el = document.querySelector('.g-recaptcha, iframe[src*=\"recaptcha\"]'); "
            "return !!el && el.offsetWidth > 0 && el.offsetHeight > 0; }")
        log.info(f"Visible reCAPTCHA challenge before submit: {recaptcha_visible}")
        if recaptcha_visible:
            log.error("Visible CAPTCHA present -- NOT submitting (project policy: no CAPTCHA bypass). "
                      "Stopping here for manual review.")
            browser.close()
            return

        page.locator('input[type="submit"][value="Register"]').first.click(timeout=8000)
        page.wait_for_timeout(2500)
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass

        log.info(f"Post-submit: url={page.url!r} title={page.title()!r}")
        body = page.evaluate("() => document.body.innerText || ''")
        log.info(f"Body text after submit ({len(body)} chars):\n{body[:2000]}")
        shot(page, '03_after_submit')

        browser.close()

    log.info("Registration attempt complete. Check body text / screenshot above for confirmation "
             "or email-verification instructions.")


if __name__ == '__main__':
    main()
