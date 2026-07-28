"""
Probe v8 -- Collin County Foreclosure Notices portal -- confirm "Next page"
click mechanic (the actual mechanism the real scraper will use).

v7 found the pager buttons have STABLE unique aria-labels: "First page",
"Previous page", "Page N", "Current page N", "Next page", "Last page" --
"First page"/"Previous page" are disabled=True on page 1. The '...' element
is decorative only (clicking it did nothing; the numbered-button window
[1,2,3,4,5,6,28,29] appears fixed, not a real jump control). v7's numbered
-button click test itself failed (Playwright role-name lookup needs the
accessible name "Page 2", not the visible text "2") so the actual
click-and-wait-for-change mechanic is still UNVERIFIED -- confirm it now
before writing the real scraper (SYSTEM_GUIDE.md Sec.9 bug #2: pagination
must wait for a real content change, never a fixed sleep, and must be
proven, not assumed).

Goals:
  1. Click button[aria-label="Next page"] three times (page 1->2->3->4),
     timing each click and confirming the first-card address actually
     changes (not a stale/cached re-render).
  2. Confirm the URL never changes (already expected, sanity re-check).
  3. Click button[aria-label="Last page"] and see the final page's row
     count (should be < 25) and whether "Next page" becomes disabled there.
  4. Log the exact wait time needed so the real scraper's timeout is sized
     correctly, not guessed.
"""
import logging
import os
import time

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

URL = "https://apps2.collincountytx.gov/ForeclosureNotices"


def wait_for_cards(page, timeout_ms=30000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            n = page.evaluate("() => (document.body.innerText.match(/Property Type:/g)||[]).length")
        except Exception:
            n = 0
        if n > 0:
            return n
        page.wait_for_timeout(400)
    return 0


def first_addr(page):
    try:
        return page.evaluate("""() => {
            const p = document.querySelector('td.mud-table-cell p.list-header, td.mud-table-cell p');
            return p ? p.textContent.replace(/\\s+/g,' ').trim() : '';
        }""")
    except Exception:
        return ''


def row_count(page):
    try:
        return page.evaluate("() => document.querySelectorAll('td.mud-table-cell').length")
    except Exception:
        return -1


def next_button_state(page):
    try:
        return page.evaluate("""() => {
            const b = document.querySelector('button[aria-label="Next page"]');
            return b ? {disabled: b.disabled, exists: true} : {exists: false};
        }""")
    except Exception:
        return {'exists': False}


def click_next(page, timeout_s=15):
    before = first_addr(page)
    t0 = time.monotonic()
    page.locator('button[aria-label="Next page"]').click(timeout=8000)
    deadline = time.monotonic() + timeout_s
    changed = False
    while time.monotonic() < deadline:
        now = first_addr(page)
        if now and now != before:
            changed = True
            break
        page.wait_for_timeout(200)
    dt = time.monotonic() - t0
    return changed, dt, before, first_addr(page)


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ))
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(20000)

        log.info("Loading page 1...")
        page.goto(URL, wait_until='domcontentloaded', timeout=45000)
        wait_for_cards(page)
        url0 = page.url
        log.info(f"PAGE 1: url={url0} first_addr={first_addr(page)!r} rows={row_count(page)} "
                  f"next_btn={next_button_state(page)}")

        seen_first_addrs = [first_addr(page)]
        for i in range(3):
            changed, dt, before, after = click_next(page)
            url_now = page.url
            log.info(f"CLICK NEXT #{i+1}: changed={changed} took={dt:.2f}s "
                      f"before={before!r} after={after!r} "
                      f"url_same={url_now == url0} rows={row_count(page)} "
                      f"next_btn={next_button_state(page)}")
            seen_first_addrs.append(after)
            if not changed:
                log.warning(f"  Content did NOT change on click #{i+1} -- investigating body text.")
                snippet = page.evaluate("() => document.body.innerText.slice(0,800)")
                log.info(f"  Body text snippet: {snippet}")

        log.info(f"All first-addresses seen across pages 1-4: {seen_first_addrs}")
        log.info(f"All unique: {len(set(seen_first_addrs)) == len(seen_first_addrs)}")

        # Jump straight to the last page.
        try:
            log.info("Clicking Last page...")
            before = first_addr(page)
            t0 = time.monotonic()
            page.locator('button[aria-label="Last page"]').click(timeout=8000)
            deadline = time.monotonic() + 15
            changed = False
            while time.monotonic() < deadline:
                now = first_addr(page)
                if now and now != before:
                    changed = True
                    break
                page.wait_for_timeout(200)
            dt = time.monotonic() - t0
            log.info(f"LAST PAGE: changed={changed} took={dt:.2f}s rows={row_count(page)} "
                      f"first_addr={first_addr(page)!r} next_btn={next_button_state(page)} "
                      f"url_same={page.url == url0}")
            # What page number is this, per the "Current page N" aria-label?
            cur = page.evaluate("""() => {
                const b = Array.from(document.querySelectorAll('button')).find(
                    b => (b.getAttribute('aria-label')||'').startsWith('Current page'));
                return b ? b.getAttribute('aria-label') : null;
            }""")
            log.info(f"LAST PAGE label: {cur}")
        except Exception as e:
            log.warning(f"Last-page click failed: {e}")

        log.info("=== PROBE COMPLETE ===")
        browser.close()


if __name__ == '__main__':
    main()
