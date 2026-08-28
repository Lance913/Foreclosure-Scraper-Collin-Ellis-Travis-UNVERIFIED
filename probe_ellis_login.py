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

        # Logged-in accounts unlock 'Property (By Date Range/Doc Type)' --
        # confirmed via login probe v1's menu dump -- exactly the name-less
        # search guest access lacked. Use that, not 'Property (By Name)'.
        loc = page.get_by_text('Property (By Date Range/Doc Type)', exact=False)
        clicked = False
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000, force=True)
                clicked = True
                break
        if not clicked:
            log.error("Could not find 'Property (By Date Range/Doc Type)' search link after login.")
            browser.close()
            return
        page.wait_for_timeout(1500)
        shot(page, '03_search_form_loggedin')
        log.info(f"reCAPTCHA visible while logged in: {recaptcha_visible(page)}")

        fields = page.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('input, select').forEach(el => {
                    out.push({tag: el.tagName, type: el.type||'', id: el.id||'', name: el.name||'',
                              visible: !!(el.offsetWidth || el.offsetHeight)});
                });
                return out;
            }
        """)
        log.info(f"=== 03_search_form_loggedin: {len(fields)} field(s) ===")
        for f in fields:
            log.info(f"  | {f}")

        from datetime import date, timedelta
        today = date.today()
        start = today - timedelta(days=60)
        # Field IDs confirmed identical to the guest 'By Name' form's date
        # pickers/doctype list in prior runs -- but this is a DIFFERENT
        # search page, so verify via the field dump above before trusting
        # these selectors still apply.
        try:
            page.fill('#FromDatePicker', start.strftime('%m/%d/%Y'), timeout=5000)
            page.fill('#ToDatePicker', today.strftime('%m/%d/%Y'), timeout=5000)
            log.info(f"Set date range {start:%m/%d/%Y} .. {today:%m/%d/%Y}")
        except Exception as e:
            log.error(f"Date field fill failed ({str(e)[:200]}) -- check field dump above for real IDs.")

        try:
            page.locator('#ShowOrHideDoctypeGroups').click(timeout=5000)
            page.wait_for_timeout(500)
            groups_html = page.evaluate("""
                () => {
                    const el = document.getElementById('DocTypesGroupList');
                    return el ? el.outerHTML.slice(0, 2000) : '(no DocTypesGroupList found)';
                }
            """)
            log.info(f"Document Type Groups content: {groups_html}")
            group_options = page.evaluate("""
                () => Array.from(document.querySelectorAll('[class*="group" i] li, [id*="Group" i] li'))
                    .map(li => (li.textContent||'').trim()).filter(Boolean)
            """)
            log.info(f"Group option texts found: {group_options}")
            shot(page, '03b_doctype_groups_open')
        except Exception as e:
            log.warning(f"Could not open Document Type Groups: {str(e)[:200]}")

        try:
            page.locator('#DocTypesList').select_option(label='NOTICE', force=True, timeout=5000)
            page.evaluate("""
                () => {
                    const el = document.getElementById('DocTypesList');
                    if (el) el.dispatchEvent(new Event('change', {bubbles: true}));
                }
            """)
            log.info("Selected DocType: NOTICE")
        except Exception as e:
            log.error(f"DocType select failed ({str(e)[:200]}) -- check field dump above for real IDs.")
        page.wait_for_timeout(500)
        shot(page, '04_before_submit_loggedin')

        try:
            page.locator('#SearchBtn').click(timeout=8000)
        except Exception as e:
            log.error(f"#SearchBtn click failed ({str(e)[:200]}) -- trying generic Search button.")
            page.locator('input[type="button"][value="Search" i], input[type="submit"][value="Search" i]').first.click(timeout=8000)
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

        # Click into the first real result row to see if the document
        # detail/image exposes a real street address (the results table
        # only has a legal description, not a mailing address). v1 clicked
        # the bare <tr> and nothing happened (same URL/body after) -- dump
        # the row's actual cell/link structure first, then click whatever
        # is truly clickable in it (likely the 'Record' column's icon/link,
        # not the row itself).
        try:
            row_html = page.evaluate("""
                () => {
                    const t = document.querySelector('table.k-selectable');
                    const tr = t ? t.querySelectorAll('tr')[1] : null;
                    return tr ? tr.outerHTML.slice(0, 2000) : '(no row found)';
                }
            """)
            log.info(f"First data row HTML: {row_html}")

            clicked = False
            for sel in ['table.k-selectable tr:nth-child(2) a',
                        'table.k-selectable tr:nth-child(2) img',
                        'table.k-selectable tr:nth-child(2) button',
                        'table.k-selectable tr:nth-child(2) td:first-child']:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    log.info(f"Clicking via {sel!r}")
                    loc.click(timeout=5000, force=True)
                    clicked = True
                    break
            if not clicked:
                log.error("No clickable element found in first row via any selector tried.")
                browser.close()
                return
            page.wait_for_timeout(2000)
            try:
                page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                pass
            log.info(f"After clicking first result: url={page.url!r} title={page.title()!r}")
            shot(page, '06_record_detail')
            detail_body = page.evaluate("() => document.body.innerText || ''")
            log.info(f"Record detail body ({len(detail_body)} chars):\n{detail_body[:3000]}")
        except Exception as e:
            log.error(f"Could not open first result's detail: {str(e)[:300]}", exc_info=True)

        browser.close()

    log.info("Login probe complete.")


if __name__ == '__main__':
    main()
