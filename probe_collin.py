"""
Probe v5 -- Collin County Foreclosure Notices portal.

Confirmed so far: https://apps2.collincountytx.gov/ForeclosureNotices is a
Blazor SERVER app (MudBlazor UI, SignalR/_blazor transport -- NOT a plain
JSON REST API, NOT query-param driven; state lives server-side over a
persistent connection, so the URL does not change with filters/pagination).
Card list (25/page, ~29 pages, ~712 total) shows per record: street+city/
state/zip address, City, Sale Date, File Date, Property Type (e.g.
"Residential Single Family (A1)", "Residential Townhomes (A4)",
"Residential Duplex (B2)" -- so more types exist than the two hinted at).
NO owner name / doc id visible in the card text so far. "All Properties
Types [393/393]" facet total is LOWER than "All Dates [712/712]" -- needs
explaining (some records may have no property type classification).
Previous click-through attempt used a broken DOM-isolation heuristic (grabbed
the whole page) so it proved nothing -- redo properly this round.

This pass, all against https://apps2.collincountytx.gov/ForeclosureNotices:
  1. Properly isolate ONE card's outerHTML (walk up from a leaf containing
     "Property Type:" only while still inside a single "Property Type:"
     occurrence) -- check for hidden ids/links/doc numbers.
  2. Click that ONE real card and watch for ANY change: URL, new DOM node
     count, body text diff, new network activity.
  3. Open the Property Type filter popover properly (scope query to
     mud-popover/mud-list content that appears after the click, not the
     whole document) -- get the FULL authoritative option list.
  4. Open the Page Size filter the same way -- what values are offered.
  5. Try clicking a Leaflet map marker/cluster -- does a popup reveal more.
  6. Look for an export/download/CSV/Excel/print affordance anywhere
     (title/aria-label on any element, not just visible text).
  7. Toggle Property Type to ONLY "Residential Single Family" + "Residential
     Mobile Home" (if present) and see how the total count changes, to sanity
     -check the 393-vs-712 facet-count mystery.
"""
import logging
import os
import re

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

ARTIFACT_DIR = 'probe_artifacts'
URL = "https://apps2.collincountytx.gov/ForeclosureNotices"

ISOLATE_CARD_JS = """() => {
    const all = Array.from(document.querySelectorAll('body *'));
    const leaf = all.find(el => el.children.length === 0 && (el.textContent||'').includes('Property Type:'));
    if (!leaf) return null;
    let card = leaf, node = leaf;
    for (let i = 0; i < 20 && node.parentElement; i++) {
        const txt = node.parentElement.textContent || '';
        const count = (txt.match(/Property Type:/g) || []).length;
        if (count > 1) break;
        card = node;
        node = node.parentElement;
    }
    return {html: card.outerHTML, tag: card.tagName, cls: card.className};
}"""

MARK_CARD_JS = """() => {
    const all = Array.from(document.querySelectorAll('body *'));
    const leaf = all.find(el => el.children.length === 0 && (el.textContent||'').includes('Property Type:'));
    if (!leaf) return false;
    let card = leaf, node = leaf;
    for (let i = 0; i < 20 && node.parentElement; i++) {
        const txt = node.parentElement.textContent || '';
        const count = (txt.match(/Property Type:/g) || []).length;
        if (count > 1) break;
        card = node;
        node = node.parentElement;
    }
    card.setAttribute('data-probe-target', '1');
    return {tag: card.tagName, cls: card.className, text: card.textContent.trim().slice(0, 200)};
}"""

ALL_TITLED_JS = """() => {
    const all = Array.from(document.querySelectorAll('*'));
    return all.map(el => ({
        tag: el.tagName,
        title: el.getAttribute('title'),
        aria: el.getAttribute('aria-label'),
        text: (el.textContent || '').trim().slice(0, 40),
    })).filter(x => (x.title && /export|download|excel|csv|print/i.test(x.title)) ||
                     (x.aria && /export|download|excel|csv|print/i.test(x.aria)));
}"""


def save(name, content):
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    name = re.sub(r'[<>:"|?*\r\n]', '_', name)
    path = os.path.join(ARTIFACT_DIR, name)
    with open(path, 'w') as f:
        f.write(content)
    log.info(f"  saved {path} ({len(content)} chars)")


def dump_popover(page, label):
    """After a click that should have opened a MudBlazor popover, log its content."""
    info = page.evaluate("""() => {
        const pops = Array.from(document.querySelectorAll('.mud-popover, [class*="popover"]'));
        return pops.map(p => ({
            cls: p.className,
            visible: p.offsetParent !== null,
            items: Array.from(p.querySelectorAll('.mud-list-item, li, [role="option"], input[type=checkbox]')).map(i => ({
                tag: i.tagName, text: (i.textContent||'').trim().slice(0,80),
                type: i.type, checked: i.checked,
            })),
            textPreview: (p.textContent||'').trim().slice(0, 400),
        }));
    }""")
    log.info(f"  POPOVERS after {label!r} click: {len(info)}")
    for p in info:
        log.info(f"    cls={p['cls']!r} visible={p['visible']} items={len(p['items'])}")
        seen = set()
        for it in p['items']:
            key = it['text']
            if key in seen:
                continue
            seen.add(key)
            log.info(f"      {it}")
        if not p['items']:
            log.info(f"      textPreview: {p['textPreview']!r}")


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
        page.set_default_timeout(30000)

        log.info(f"Navigating to {URL}")
        page.goto(URL, wait_until='networkidle', timeout=45000)
        page.wait_for_timeout(2500)

        # ---- 1. Isolate one card properly ----
        card = page.evaluate(ISOLATE_CARD_JS)
        if card:
            log.info(f"===== ISOLATED CARD tag={card['tag']} cls={card['cls']!r} =====")
            save('sample_card_v2.html', card['html'])
            log.info(f"CARD HTML:\n{card['html']}")
        else:
            log.warning("Could not isolate a card.")

        # ---- 2. Click that one card, watch for changes ----
        marked = page.evaluate(MARK_CARD_JS)
        log.info(f"Marked card for click: {marked}")
        if marked:
            before_text = page.evaluate("() => document.body.innerText || ''")
            before_nodes = page.evaluate("() => document.querySelectorAll('*').length")
            url_before = page.url
            try:
                page.locator('[data-probe-target="1"]').first.click(timeout=8000, force=True)
                page.wait_for_timeout(2500)
                url_after = page.url
                after_text = page.evaluate("() => document.body.innerText || ''")
                after_nodes = page.evaluate("() => document.querySelectorAll('*').length")
                log.info(f"CLICK CARD: url {url_before} -> {url_after}; "
                         f"nodes {before_nodes} -> {after_nodes}; "
                         f"text changed = {before_text != after_text}")
                if before_text != after_text:
                    save('after_card_click_body.txt', after_text)
                    log.info(f"NEW BODY TEXT (first 3000):\n{after_text[:3000]}")
                save('after_card_click.html', page.content())
            except Exception as e:
                log.warning(f"Card click failed: {e}")

        # Reload fresh for the next experiments (avoid compounding state).
        page.goto(URL, wait_until='networkidle', timeout=45000)
        page.wait_for_timeout(2000)

        # ---- 3. Property Type filter popover ----
        try:
            page.get_by_text('All Properties Types', exact=False).first.click(timeout=8000)
            page.wait_for_timeout(1200)
            dump_popover(page, 'Property Types')
            save('property_type_popover.html', page.content())
            page.keyboard.press('Escape')
            page.wait_for_timeout(500)
        except Exception as e:
            log.warning(f"Property Type popover failed: {e}")

        # ---- 4. Page Size filter ----
        try:
            page.get_by_label('Page Size', exact=False).first.click(timeout=8000)
            page.wait_for_timeout(1000)
            dump_popover(page, 'Page Size')
            page.keyboard.press('Escape')
            page.wait_for_timeout(500)
        except Exception as e:
            log.warning(f"Page Size popover failed (trying alt selector): {e}")
            try:
                page.locator('text=Page Size').first.click(timeout=5000)
                page.wait_for_timeout(1000)
                dump_popover(page, 'Page Size (alt)')
                page.keyboard.press('Escape')
            except Exception as e2:
                log.warning(f"  alt also failed: {e2}")

        # ---- 5. Map marker click ----
        try:
            marker = page.locator('.leaflet-marker-icon, .leaflet-interactive').first
            if marker.count() > 0:
                marker.click(timeout=5000, force=True)
                page.wait_for_timeout(1500)
                popup_text = page.evaluate("""() => {
                    const p = document.querySelector('.leaflet-popup-content, .leaflet-popup');
                    return p ? p.textContent.trim() : null;
                }""")
                log.info(f"Map marker click -> popup text: {popup_text!r}")
                save('map_popup.html', page.content())
            else:
                log.info("No leaflet marker found to click.")
        except Exception as e:
            log.warning(f"Map marker click failed: {e}")

        # ---- 6. Export/download affordance anywhere ----
        exporters = page.evaluate(ALL_TITLED_JS)
        log.info(f"Elements with export/download/csv/excel/print title or aria: {exporters}")

        # ---- 7. Filter to only Single Family + Mobile Home, check count ----
        page.goto(URL, wait_until='networkidle', timeout=45000)
        page.wait_for_timeout(2000)
        try:
            page.get_by_text('All Properties Types', exact=False).first.click(timeout=8000)
            page.wait_for_timeout(1000)
            # Try to find a "Select All" toggle to clear it first, then pick two.
            all_items = page.evaluate("""() => {
                const pops = Array.from(document.querySelectorAll('.mud-popover, [class*="popover"]'));
                let out = [];
                for (const p of pops) {
                    if (p.offsetParent === null) continue;
                    out = out.concat(Array.from(p.querySelectorAll('.mud-list-item')).map(li => (li.textContent||'').trim()));
                }
                return out;
            }""")
            log.info(f"Property Type list items while open: {all_items}")
        except Exception as e:
            log.warning(f"Step 7 failed: {e}")

        log.info("=== PROBE COMPLETE ===")
        browser.close()


if __name__ == '__main__':
    main()
