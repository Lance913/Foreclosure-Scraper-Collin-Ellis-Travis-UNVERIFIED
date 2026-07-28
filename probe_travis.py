"""
Probe v6 -- Travis County: fix date-range field (label changes on dept select).

v5 result: Department selection to "Foreclosures" via click-text + ArrowDown
x3 + Enter WORKED ("dept now shows 'Foreclosures' in body text: True").

CRITICAL finding: once Foreclosures is selected, the Date Range field's
label CHANGES from "Recorded Date" to **"Sale Date"** (confirmed in the body
text dump: "Date Range | Sale Date"). The Foreclosures department filters by
auction date, not filing/recorded date -- which is actually exactly what we
want (upcoming trustee sale auctions). v5's date-range step failed because
it was still looking for text "Recorded Date", which no longer exists on
the page at that point -- a pure locator bug, not a portal problem.

v6 fix: click text "Sale Date" (falling back to "Recorded Date" just in
case) to open the date-range control post department-select, dump whatever
preset options it offers (may differ from the Recorded Date list -- a
forward-looking Sale Date filter might have different/no "Last N" presets),
then select the broadest one and submit.
"""
import logging
import os
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

PS_BASE = "https://travis.tx.publicsearch.us"
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
ART_DIR = 'probe_artifacts'


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


def dump_body_text(page, label, n=60):
    txt = page.evaluate("() => document.body.innerText || ''")
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    log.info(f"=== {label}: body text ({len(lines)} lines, showing first {n}) ===")
    for ln in lines[:n]:
        log.info(f"  | {ln}")
    return lines


def dump_table(page, label):
    info = page.evaluate("""() => {
        const tables = Array.from(document.querySelectorAll('table')).map(t => ({
            headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim()),
            rowCount: t.querySelectorAll('tr').length,
        }));
        const rows = [];
        const t = document.querySelector('table');
        if (t) {
            for (const tr of Array.from(t.querySelectorAll('tr')).slice(0, 10)) {
                rows.push(Array.from(tr.querySelectorAll('th,td')).map(c => (c.textContent||'').trim()));
            }
        }
        return {tables, rows};
    }""")
    log.info(f"=== {label}: tables={info['tables']} ===")
    for r in info['rows']:
        log.info(f"  ROW: {r}")
    return info


def dismiss_popups(page):
    try:
        page.keyboard.press('Escape')
        page.wait_for_timeout(300)
    except Exception:
        pass
    for sel in ['[aria-label="Close"]', 'button:has-text("×")', 'button:has-text("✕")',
                'button[class*="close" i]', '[class*="modal" i] button', '[class*="popup" i] button',
                '[class*="tour" i] button']:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=1500)
                log.info(f"dismissed popup via selector: {sel}")
                page.wait_for_timeout(400)
        except Exception:
            pass
    try:
        page.mouse.click(5, 5)
    except Exception:
        pass


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent=UA, viewport={'width': 1500, 'height': 1900})
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30_000)

        log.info(f"GOTO {PS_BASE}")
        page.goto(PS_BASE, wait_until='networkidle')
        page.wait_for_timeout(1200)
        dismiss_popups(page)
        page.wait_for_timeout(500)
        shot(page, '00_clean')

        # 1. Department -> Foreclosures via click-to-open + keyboard select.
        try:
            page.get_by_text('Land Records', exact=True).first.click(timeout=6000)
            page.wait_for_timeout(600)
            shot(page, '01_dept_open')
            opts = page.evaluate("""() => Array.from(document.querySelectorAll('[role="option"]'))
                .map(el => (el.textContent||'').trim()).filter(Boolean)""")
            log.info(f"dept options visible: {opts}")
            for _ in range(3):
                page.keyboard.press('ArrowDown')
                page.wait_for_timeout(150)
            page.keyboard.press('Enter')
            page.wait_for_timeout(1000)
            shot(page, '02_dept_selected')
            lines = dump_body_text(page, 'after dept keyboard-select', n=15)
            log.info(f"dept now shows 'Foreclosures' in body text: {'Foreclosures' in lines}")
        except Exception as e:
            log.error(f"department selection failed: {e}", exc_info=True)
            shot(page, '02_dept_FAILED')

        # 2. Date range -> broadest preset via click-to-open + keyboard select.
        #    NOTE: the field's label changes to "Sale Date" once the
        #    Foreclosures department is selected (was "Recorded Date" under
        #    Land Records) -- try the new label first, fall back to the old.
        try:
            try:
                date_trigger = page.get_by_text('Sale Date', exact=True).first
                date_trigger.click(timeout=4000)
                log.info("opened date-range control via 'Sale Date' label")
            except Exception:
                date_trigger = page.get_by_text('Recorded Date', exact=True).first
                date_trigger.click(timeout=4000)
                log.info("opened date-range control via 'Recorded Date' label (fallback)")
            page.wait_for_timeout(600)
            shot(page, '03_date_open')
            opts = page.evaluate("""() => Array.from(document.querySelectorAll('[role="option"]'))
                .map(el => (el.textContent||'').trim()).filter(Boolean)""")
            log.info(f"date options visible: {opts}")
            # Press ArrowDown generously (more than the option count) to land
            # on the LAST (broadest) option regardless of exact indexing --
            # most listbox widgets clamp at the last item rather than wrap.
            for _ in range(10):
                page.keyboard.press('ArrowDown')
                page.wait_for_timeout(100)
            page.keyboard.press('Enter')
            page.wait_for_timeout(1000)
            shot(page, '04_date_selected')
            dump_body_text(page, 'after date keyboard-select', n=15)
        except Exception as e:
            log.error(f"date range selection failed: {e}", exc_info=True)
            shot(page, '04_date_FAILED')

        dump_body_text(page, 'before clicking Search', n=20)

        # 3. Search.
        try:
            search_btn = page.locator('button:has-text("Search")').first
            search_btn.click(timeout=8000)
            page.wait_for_timeout(4000)
            try:
                page.wait_for_load_state('networkidle', timeout=15_000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            log.info(f"AFTER SEARCH: url={page.url} title={page.title()!r}")
            shot(page, '05_after_search')
            dump_table(page, 'after search')
            dump_body_text(page, 'after search (full)', n=100)
        except Exception as e:
            log.error(f"search click failed: {e}", exc_info=True)
            shot(page, '05_search_FAILED')
            dump_body_text(page, 'after search FAILED state', n=60)

        browser.close()


if __name__ == '__main__':
    main()
