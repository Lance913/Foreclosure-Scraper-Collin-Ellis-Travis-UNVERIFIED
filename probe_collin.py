"""
Probe v7 -- Collin County Foreclosure Notices portal -- PAGINATION mechanics.

Everything else is now understood (see probe v1-v6 history in git log):
  - Real URL: https://apps2.collincountytx.gov/ForeclosureNotices (Blazor
    Server + MudBlazor; NOT query-param driven -- must drive the live UI).
  - Card = one <tr> whose single <td class="mud-table-cell..."> holds the
    address <p> + a grid of labeled fields: City:, Sale Date:, File Date:,
    Property Type:. No owner name / doc id anywhere (verified via a properly
    -isolated single-card click -- no change). No CSV/export affordance.
  - Full Property Type facet list (exact wording + current counts):
      Commercial (C3) [2], Commercial (F1) [17], Residential Duplex (B2) [1],
      Residential Mobile Home (A2) [2], Residential Single Family (A1) [352],
      Residential Single Family (C1) [1], Residential Townhomes (A4) [18]
    Sum = 393, matching the "All Properties Types [393/393]" facet -- the
    other ~319 of 712 total records have NO property type classified.
    Decision: filter Property Type IN CODE (substring match against a small
    allow-list), not via the fragile UI popover -- simpler and more robust;
    scrape all pages regardless of type.
  - Page Size options: 5 / 10 / 25 only (no bigger page size available).
  - ~29 pages at size 25 for ~712 total unfiltered records.

THIS IS THE ONE REMAINING UNKNOWN before writing the real scraper:
pagination mechanics (SYSTEM_GUIDE.md Sec.9 bug #2 -- "pagination can
silently truncate" -- explicitly do not guess this).

Goals:
  1. Dump the exact outerHTML of the pager control (button classes/aria/
     structure) so we know a reliable selector for "next page".
  2. Actually click from page 1 -> 2 -> 3, confirming real content changes
     each time (compare first-card address), and log timing.
  3. Check what happens once the visible page-number window needs to slide
     (does a "..." exist and is it clickable, or is there a stable
     next-page arrow icon we should use instead of numbered buttons).
  4. Confirm the URL never changes across pages (so we know state truly
     lives server-side and our scraper must stay on one page instance).
"""
import logging
import os
import re
import time

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

ARTIFACT_DIR = 'probe_artifacts'
URL = "https://apps2.collincountytx.gov/ForeclosureNotices"


def save(name, content):
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    name = re.sub(r'[<>:"|?*\r\n]', '_', name)
    with open(os.path.join(ARTIFACT_DIR, name), 'w') as f:
        f.write(content)
    log.info(f"  saved probe_artifacts/{name} ({len(content)} chars)")


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


def first_card_signature(page):
    """First card's address line -- used to detect a real page change."""
    try:
        return page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('td.mud-table-cell'));
            return all.length ? all[0].textContent.trim().slice(0, 80) : '';
        }""")
    except Exception:
        return ''


def all_addresses(page):
    try:
        return page.evaluate("""() => {
            const cells = Array.from(document.querySelectorAll('td.mud-table-cell'));
            return cells.map(td => {
                const p = td.querySelector('p.list-header, p');
                return p ? p.textContent.replace(/\\s+/g,' ').trim() : '';
            });
        }""")
    except Exception:
        return []


def main():
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
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

        # ---- 1. Dump the pager control's exact HTML ----
        pager_html = page.evaluate("""() => {
            // Find a button whose text is exactly '2' (a page-number button),
            // then walk up to its containing nav/pagination wrapper.
            const btns = Array.from(document.querySelectorAll('button'));
            const two = btns.find(b => (b.textContent||'').trim() === '2');
            if (!two) return null;
            let el = two;
            for (let i = 0; i < 5 && el.parentElement; i++) el = el.parentElement;
            return el.outerHTML;
        }""")
        if pager_html:
            save('pager_control.html', pager_html)
            log.info(f"PAGER HTML:\n{pager_html[:4000]}")
        else:
            log.warning("Could not find a page-'2' button to anchor the pager dump.")

        # Dump every button's full attribute set within the pager area (aria-label,
        # title, disabled, class) -- specifically the icon-only ones flanking the
        # numbered buttons (candidates for a stable "next page" selector).
        pager_buttons = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            return btns.map((b,i) => ({
                i, text: (b.textContent||'').trim(),
                aria: b.getAttribute('aria-label'), title: b.getAttribute('title'),
                disabled: b.disabled, cls: b.className,
            }));
        }""")
        log.info(f"ALL BUTTONS ({len(pager_buttons)}) with aria/title/disabled:")
        for b in pager_buttons:
            log.info(f"  {b}")

        # ---- 2. Click page 1 -> 2 -> 3, confirm real content changes ----
        url0 = page.url
        sig1 = first_card_signature(page)
        addrs1 = all_addresses(page)
        log.info(f"PAGE 1: url={url0} first_card={sig1!r} n_addrs={len(addrs1)}")
        log.info(f"PAGE 1 addresses: {addrs1}")

        for target_page in [2, 3]:
            try:
                btn = page.get_by_role('button', name=str(target_page), exact=True).first
                if btn.count() == 0:
                    log.warning(f"No button labelled {target_page!r} found -- stopping pagination test.")
                    break
                before_sig = first_card_signature(page)
                t0 = time.monotonic()
                btn.click(timeout=8000)
                # Wait for the first card to actually change.
                changed = False
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    now_sig = first_card_signature(page)
                    if now_sig and now_sig != before_sig:
                        changed = True
                        break
                    page.wait_for_timeout(200)
                dt = time.monotonic() - t0
                sig_n = first_card_signature(page)
                addrs_n = all_addresses(page)
                url_n = page.url
                log.info(f"PAGE {target_page}: clicked in {dt:.2f}s, changed={changed}, "
                         f"url={url_n} (same_as_page1={url_n == url0}) "
                         f"first_card={sig_n!r} n_addrs={len(addrs_n)}")
                log.info(f"PAGE {target_page} addresses: {addrs_n}")
                overlap = set(addrs1) & set(addrs_n)
                log.info(f"PAGE {target_page}: overlap with page 1 addresses: {overlap}")
            except Exception as e:
                log.warning(f"Pagination to page {target_page} failed: {e}")
                break

        # ---- 3. Jump toward the tail to see how the sliding window / ellipsis behaves ----
        try:
            ell = page.get_by_text('...', exact=True).first
            if ell.count() > 0:
                log.info("Found a '...' element -- attempting to click it.")
                before_sig = first_card_signature(page)
                ell.click(timeout=5000)
                page.wait_for_timeout(1500)
                after_sig = first_card_signature(page)
                log.info(f"After clicking '...': first_card {before_sig!r} -> {after_sig!r}")
                # Dump what page-number buttons are visible now.
                nums = page.evaluate("""() => Array.from(document.querySelectorAll('button'))
                    .map(b => (b.textContent||'').trim()).filter(t => /^\\d+$/.test(t))""")
                log.info(f"Visible page-number buttons after '...' click: {nums}")
            else:
                log.info("No '...' element found (button-only pager, or all pages fit).")
        except Exception as e:
            log.warning(f"Ellipsis click test failed: {e}")

        # ---- 4. Try the last page button directly (label '29') to test far jump ----
        try:
            last_btn = page.get_by_role('button', name='29', exact=True).first
            if last_btn.count() > 0:
                before_sig = first_card_signature(page)
                last_btn.click(timeout=8000)
                page.wait_for_timeout(2000)
                after_sig = first_card_signature(page)
                addrs_last = all_addresses(page)
                log.info(f"PAGE 29 (direct click): first_card {before_sig!r} -> {after_sig!r}; "
                         f"n_addrs={len(addrs_last)}")
                log.info(f"PAGE 29 addresses: {addrs_last}")
            else:
                log.info("No page-'29' button visible directly (expected if window slid).")
        except Exception as e:
            log.warning(f"Direct last-page click failed: {e}")

        log.info("=== PROBE COMPLETE ===")
        browser.close()


if __name__ == '__main__':
    main()
