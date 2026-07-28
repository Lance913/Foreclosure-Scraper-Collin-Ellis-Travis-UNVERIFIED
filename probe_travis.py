"""
Probe v4 -- Travis County: fix form interaction (popup was blocking clicks).

v3 result: all 3 form interactions (department, date range, search) failed
with "Locator.click: Timeout 5000ms exceeded" -- but the FIRST dump still
printed something (date-range-looking option text), which strongly suggests
the "Not sure where to start? Take the Tour" popup (visible overlaying the
form in earlier screenshots) is intercepting pointer events / making the
underlying combobox unreliable to hit via fuzzy text locators.

v4 approach:
  1. Explicitly dismiss the tour popup FIRST (try several strategies),
     screenshot to confirm it's gone.
  2. Use role=combobox (precise) instead of get_by_text (ambiguous -- "Land
     Records" appears both as the current-value display AND as a list
     option once opened).
  3. Screenshot after every single interaction step so failures are visually
     diagnosable, not just inferred from timeouts.
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


def dump_table(page, label):
    info = page.evaluate("""() => {
        const tables = Array.from(document.querySelectorAll('table')).map(t => ({
            headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim()),
            rowCount: t.querySelectorAll('tr').length,
        }));
        const rows = [];
        const t = document.querySelector('table');
        if (t) {
            for (const tr of Array.from(t.querySelectorAll('tr')).slice(0, 8)) {
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
    """Try several strategies to close the onboarding tour popup that
    overlays the form on first load. Non-fatal if none match."""
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
    # Last resort: click a neutral spot (top-left corner) to blur/close any
    # remaining transient tooltip.
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
        shot(page, '00_baseline')

        dismiss_popups(page)
        page.wait_for_timeout(500)
        shot(page, '01_after_dismiss')
        dump_body_text(page, 'after dismiss popups', n=40)

        # Diagnostic pass: enumerate every combobox on the page BEFORE
        # clicking anything, with its accessible name + bounding box.
        try:
            comboboxes = page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('[role="combobox"], select, [aria-haspopup="listbox"]').forEach(el => {
                    const r = el.getBoundingClientRect();
                    out.push({
                        tag: el.tagName, role: el.getAttribute('role'),
                        ariaLabel: el.getAttribute('aria-label') || '',
                        text: (el.textContent||'').trim().slice(0,60),
                        visible: r.width > 0 && r.height > 0,
                        box: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                    });
                });
                return out;
            }""")
            log.info(f"comboboxes found: {comboboxes}")
        except Exception as e:
            log.error(f"combobox enumeration failed: {e}")
            comboboxes = []

        # 1. Department combobox -- should be the FIRST one in reading order.
        try:
            dept_combo = page.locator('[role="combobox"]').first
            dept_combo.click(timeout=8000)
            page.wait_for_timeout(700)
            shot(page, '02_dept_open')
            opts = page.evaluate("""() => Array.from(document.querySelectorAll('[role="option"]'))
                .map(el => (el.textContent||'').trim()).filter(Boolean)""")
            log.info(f"dept dropdown options: {opts}")
            page.get_by_role('option', name='Foreclosures', exact=True).click(timeout=5000)
            page.wait_for_timeout(1000)
            shot(page, '03_dept_foreclosures_selected')
            dump_body_text(page, 'after selecting Foreclosures', n=30)
        except Exception as e:
            log.error(f"department selection failed: {e}", exc_info=True)
            shot(page, '03_dept_FAILED')

        # 2. Date range combobox -- second one.
        try:
            date_combo = page.locator('[role="combobox"]').nth(1)
            date_combo.click(timeout=8000)
            page.wait_for_timeout(700)
            shot(page, '04_date_open')
            opts = page.evaluate("""() => Array.from(document.querySelectorAll('[role="option"]'))
                .map(el => (el.textContent||'').trim()).filter(Boolean)""")
            log.info(f"date dropdown options: {opts}")
            clicked = False
            for label in ('Last 1 Year', 'Last 6 Months', 'Last 3 Months'):
                try:
                    page.get_by_role('option', name=label, exact=True).click(timeout=2000)
                    log.info(f"selected date range preset: {label}")
                    clicked = True
                    break
                except Exception:
                    continue
            if not clicked:
                log.warning("no date preset matched by name -- trying last option in list")
                try:
                    page.locator('[role="option"]').last.click(timeout=2000)
                    clicked = True
                except Exception:
                    pass
            page.wait_for_timeout(800)
            shot(page, '05_date_selected')
        except Exception as e:
            log.error(f"date range selection failed: {e}", exc_info=True)
            shot(page, '05_date_FAILED')

        dump_body_text(page, 'before clicking Search', n=30)

        # 3. Search button.
        try:
            search_btn = page.locator('button[type="submit"], button:has-text("Search")').first
            search_btn.scroll_into_view_if_needed(timeout=3000)
            search_btn.click(timeout=8000)
            page.wait_for_timeout(4000)
            try:
                page.wait_for_load_state('networkidle', timeout=15_000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            log.info(f"AFTER SEARCH: url={page.url} title={page.title()!r}")
            shot(page, '06_after_search')
            dump_table(page, 'after search')
            dump_body_text(page, 'after search (full)', n=80)
        except Exception as e:
            log.error(f"search click failed: {e}", exc_info=True)
            shot(page, '06_search_FAILED')
            dump_body_text(page, 'after search FAILED state', n=60)

        browser.close()


if __name__ == '__main__':
    main()
