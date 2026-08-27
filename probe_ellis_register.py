"""
Probe -- Ellis County AcclaimWeb registration form. Dumps the real sign-up
fields before attempting registration for real (SYSTEM_GUIDE.md S6 -- don't
guess a form's fields).
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
log = logging.getLogger('PROBE')

ART_DIR = 'probe_artifacts'
BASE_URL = 'https://ellisccktxpublicsearch.us/AcclaimWeb/'


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


def dump_form_fields(page, label):
    fields = page.evaluate("""
        () => {
            const out = [];
            document.querySelectorAll('input, select, button, textarea').forEach(el => {
                out.push({
                    tag: el.tagName, type: el.type || '', id: el.id || '',
                    name: el.name || '', placeholder: el.placeholder || '',
                    aria: el.getAttribute('aria-label') || '',
                    required: el.required || false,
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

        # Find and click Login / Register.
        for text in ['Login', 'Log In', 'Register']:
            loc = page.get_by_text(text, exact=False)
            for i in range(loc.count()):
                el = loc.nth(i)
                if el.is_visible():
                    log.info(f"Clicking {text!r} match [{i}]")
                    el.click(timeout=8000)
                    page.wait_for_timeout(1500)
                    break
            else:
                continue
            break

        shot(page, '01_after_login_click')
        dump_form_fields(page, '01_after_login_click')

        # Look for an explicit "Register"/"Create Account"/"Sign Up" link on
        # whatever page we landed on (login page usually links to register).
        for text in ['Register', 'Create Account', 'Sign Up', "Don't have an account"]:
            loc = page.get_by_text(text, exact=False)
            if loc.count() > 0 and loc.first.is_visible():
                log.info(f"Found register link: {text!r} -- clicking")
                loc.first.click(timeout=8000)
                page.wait_for_timeout(1500)
                shot(page, '02_register_form')
                dump_form_fields(page, '02_register_form')
                break
        else:
            log.info("No separate register link found on this page -- may already be the register form.")

        browser.close()

    log.info("Probe complete.")


if __name__ == '__main__':
    main()
