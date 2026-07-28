"""
Probe v6 -- Collin County Foreclosure Notices portal.

v5 crashed: `page.goto(..., wait_until='networkidle')` timed out (45s) on the
very first navigation. The page embeds a Leaflet/ArcGIS map that keeps
fetching tiles, so network activity may never go idle -- 'networkidle' is
the wrong wait condition here (this is exactly SYSTEM_GUIDE.md Sec.9 bug #1
territory: a bad wait condition silently costing us the whole probe). Fix:
navigate with 'domcontentloaded' and poll for real content (the string
"Property Type:" appearing in body text) instead. Also: do ONE navigation
for the whole probe (use the in-app "Reset Filters" button between
experiments, not repeated page.goto), and wrap every section in try/except
so one flaky step doesn't kill everything else.

Goals still open from v4/v5 (v5 crashed before gathering anything new):
  1. Isolate + click one real card -- does anything happen (owner name/doc id)?
  2. Full, correctly-scoped Property Type filter option list (exact wording).
  3. Page Size options.
  4. Map marker click -> popup with more info?
  5. Any export/download/CSV/print affordance.
  6. Toggle to only 2 property types -- does the total count/page count drop?
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


def wait_for_cards(page, timeout_ms=30000):
    """Poll until real card content is present (avoids 'networkidle', which
    never resolves because of the embedded map's continuous tile requests)."""
    deadline = time.monotonic() + timeout_ms / 1000
    last_n = 0
    while time.monotonic() < deadline:
        try:
            last_n = page.evaluate(
                "() => (document.body.innerText.match(/Property Type:/g)||[]).length")
        except Exception:
            last_n = 0
        if last_n > 0:
            return last_n
        try:
            no_results = page.evaluate(
                "() => /no results|no records|nothing found/i.test(document.body.innerText||'')")
        except Exception:
            no_results = False
        if no_results:
            log.info("  page explicitly reports no results.")
            return 0
        page.wait_for_timeout(500)
    log.warning(f"  cards never appeared within {timeout_ms/1000:.0f}s (last count={last_n}).")
    return last_n


def dump_popover(page, label):
    info = page.evaluate("""() => {
        const pops = Array.from(document.querySelectorAll('.mud-popover, [class*="popover"]'));
        return pops.map(p => ({
            cls: p.className,
            visible: p.offsetParent !== null,
            items: Array.from(p.querySelectorAll('.mud-list-item, li, [role="option"], input[type=checkbox]')).map(i => ({
                tag: i.tagName, text: (i.textContent||'').trim().slice(0,80),
                type: i.type, checked: i.checked,
            })),
            textPreview: (p.textContent||'').trim().slice(0, 500),
        }));
    }""")
    log.info(f"  POPOVERS after {label!r} click: {len(info)}")
    for p in info:
        if not p['visible']:
            continue
        log.info(f"    cls={p['cls']!r} visible={p['visible']} items={len(p['items'])}")
        seen = set()
        for it in p['items']:
            key = it['text']
            if key in seen or not key:
                continue
            seen.add(key)
            log.info(f"      {it}")
        if not p['items']:
            log.info(f"      textPreview: {p['textPreview']!r}")


def section(name):
    log.info(f"\n########## SECTION: {name} ##########")


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

        section("Initial load")
        page.goto(URL, wait_until='domcontentloaded', timeout=45000)
        n = wait_for_cards(page)
        log.info(f"Cards ready: {n} 'Property Type:' occurrences found on load.")

        # ---- 1. Isolate one card properly ----
        section("Isolate + inspect one card")
        try:
            card = page.evaluate(ISOLATE_CARD_JS)
            if card:
                log.info(f"ISOLATED CARD tag={card['tag']} cls={card['cls']!r}")
                save('sample_card_v2.html', card['html'])
                log.info(f"CARD HTML:\n{card['html']}")
            else:
                log.warning("Could not isolate a card.")
        except Exception as e:
            log.warning(f"Card isolation failed: {e}")

        # ---- 2. Click that one card, watch for changes ----
        section("Click one card")
        try:
            marked = page.evaluate(MARK_CARD_JS)
            log.info(f"Marked card for click: {marked}")
            if marked:
                before_text = page.evaluate("() => document.body.innerText || ''")
                before_nodes = page.evaluate("() => document.querySelectorAll('*').length")
                url_before = page.url
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
        except Exception as e:
            log.warning(f"Card click experiment failed: {e}")

        # ---- 3. Property Type filter popover ----
        section("Property Type filter popover")
        try:
            page.get_by_text('All Properties Types', exact=False).first.click(timeout=8000)
            page.wait_for_timeout(1200)
            dump_popover(page, 'Property Types')
            page.keyboard.press('Escape')
            page.wait_for_timeout(500)
        except Exception as e:
            log.warning(f"Property Type popover failed: {e}")

        # ---- 4. Page Size filter ----
        section("Page Size filter popover")
        try:
            page.get_by_label('Page Size', exact=False).first.click(timeout=8000)
            page.wait_for_timeout(1000)
            dump_popover(page, 'Page Size')
            page.keyboard.press('Escape')
            page.wait_for_timeout(500)
        except Exception as e:
            log.warning(f"Page Size popover failed: {e}")

        # ---- 5. Map marker click ----
        section("Map marker click")
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
            else:
                log.info("No leaflet marker found to click.")
        except Exception as e:
            log.warning(f"Map marker click failed: {e}")

        # ---- 6. Export/download affordance anywhere ----
        section("Export/download affordance search")
        try:
            exporters = page.evaluate(ALL_TITLED_JS)
            log.info(f"Elements with export/download/csv/excel/print title or aria: {exporters}")
        except Exception as e:
            log.warning(f"Export search failed: {e}")

        # ---- 7. Filter to Single Family + Mobile Home only, check count ----
        section("Toggle Property Type filter, observe count")
        try:
            # Reset first so we start from a known baseline.
            page.get_by_role('button', name='RESET FILTERS').click(timeout=5000)
            page.wait_for_timeout(1500)
            wait_for_cards(page)

            page.get_by_text('All Properties Types', exact=False).first.click(timeout=8000)
            page.wait_for_timeout(1000)
            all_items = page.evaluate("""() => {
                const pops = Array.from(document.querySelectorAll('.mud-popover, [class*="popover"]'));
                let out = [];
                for (const p of pops) {
                    if (p.offsetParent === null) continue;
                    out = out.concat(Array.from(p.querySelectorAll('.mud-list-item')).map(li => (li.textContent||'').trim()));
                }
                return out;
            }""")
            log.info(f"Property Type list items while open ({len(all_items)}): {all_items}")
            save('property_type_items.txt', '\n'.join(all_items))
        except Exception as e:
            log.warning(f"Toggle-filter section failed: {e}")

        log.info("\n=== PROBE COMPLETE ===")
        browser.close()


if __name__ == '__main__':
    main()
