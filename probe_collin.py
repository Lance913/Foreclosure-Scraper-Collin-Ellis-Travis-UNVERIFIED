"""
Probe v4 -- Collin County Foreclosure Notices portal.

CONFIRMED URL (v3 found it): https://apps2.collincountytx.gov/ForeclosureNotices
(NOT "apps." -- that 404s. The county-clerk page links to "apps2".)
Title: "Foreclosures". It's a card-list SPA (not a plain <table>), 712 total
records observed. Each card so far shows: street+city/state/zip address,
City, Sale Date, File Date, Property Type (e.g. "Residential Townhomes (A4)",
"Residential Single Family (A1)") -- but NO owner name visible in the
truncated dump. Filters seen in body text: All Dates, Sale Dates, All Cities,
All Properties Types, Filed Date Start/End, Sort By, Reverse, Reset Filters.

This pass, all against https://apps2.collincountytx.gov/ForeclosureNotices:
  1. Capture every network response whose content-type is JSON (SPAs backing
     a card list like this are almost always driven by one JSON API call --
     if we find it, we may not need to scrape rendered HTML at all).
  2. Dump the full form/filter controls (esp. Property Types -- exact option
     text/values, to verify the "Residential Single Family" / "Residential
     Mobile Home" / "Residential Townhomes" hint against reality).
  3. Dump one result card's full outerHTML (structure/classes/any data-*
     attributes/hidden doc id).
  4. Click the first card and see what happens (navigation vs modal) --
     looking for the owner/grantor name and a document id/link.
  5. Full (untruncated) body text dump.
"""
import json
import logging
import os

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

ARTIFACT_DIR = 'probe_artifacts'
URL = "https://apps2.collincountytx.gov/ForeclosureNotices"

DUMP_FORM_JS = """() => {
    const els = (sel) => Array.from(document.querySelectorAll(sel));
    const inputs = els('input').map(i => ({
        type: i.type, id: i.id, name: i.name, placeholder: i.placeholder,
        aria: i.getAttribute('aria-label'), value: i.value, checked: i.checked,
        cls: i.className,
    }));
    const selects = els('select').map(s => ({
        id: s.id, name: s.name, aria: s.getAttribute('aria-label'), multiple: s.multiple,
        options: Array.from(s.options).map(o => ({text: (o.textContent||'').trim(), value: o.value})),
    }));
    const buttons = els('button, input[type=submit], input[type=button]').map(b => ({
        tag: b.tagName, text: (b.textContent || b.value || '').trim(), id: b.id, cls: b.className,
    }));
    return {inputs, selects, buttons, url: location.href, title: document.title};
}"""


def save(name, content):
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    path = os.path.join(ARTIFACT_DIR, name)
    with open(path, 'w') as f:
        f.write(content)
    log.info(f"  saved {path} ({len(content)} chars)")


def main():
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    json_responses = []  # (url, status, body_text)

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

        def on_response(resp):
            try:
                ct = resp.headers.get('content-type', '')
                if 'json' in ct.lower():
                    json_responses.append(resp)
            except Exception:
                pass

        page.on('response', on_response)

        log.info(f"Navigating to {URL}")
        resp = page.goto(URL, wait_until='networkidle', timeout=45000)
        log.info(f"status={resp.status if resp else None} final_url={page.url} title={page.title()!r}")
        page.wait_for_timeout(3000)

        # ---- 1. JSON network responses ----
        log.info(f"===== JSON RESPONSES CAPTURED: {len(json_responses)} =====")
        for r in json_responses:
            try:
                body = r.text()
            except Exception as e:
                body = f'<unreadable: {e}>'
            log.info(f"  {r.request.method} {r.url} -> {r.status} ({len(body)} bytes)")
            # Save full body to an artifact file; log a preview.
            safe = r.url.split('?')[0].rstrip('/').split('/')[-1] or 'root'
            save(f'json_{safe}_{len(body)}.json', body)
            log.info(f"    preview: {body[:1500]}")

        # ---- 2. Full form/filter dump ----
        info = page.evaluate(DUMP_FORM_JS)
        log.info(f"===== FORM DUMP ===== url={info['url']} title={info['title']!r}")
        log.info(f"INPUTS ({len(info['inputs'])}):")
        for i in info['inputs']:
            log.info(f"  {i}")
        log.info(f"SELECTS ({len(info['selects'])}):")
        for s in info['selects']:
            log.info(f"  id={s['id']!r} name={s['name']!r} aria={s['aria']!r} multiple={s['multiple']}")
            for o in s['options']:
                log.info(f"      option: {o}")
        log.info(f"BUTTONS ({len(info['buttons'])}):")
        for b in info['buttons']:
            log.info(f"  {b}")

        # ---- 2b. Try clicking the "Property Types" filter to expand it ----
        for label in ['Property Types', 'All Properties Types']:
            try:
                loc = page.get_by_text(label, exact=False).first
                if loc.count() > 0:
                    log.info(f"Clicking filter labelled {label!r}...")
                    loc.click(timeout=5000)
                    page.wait_for_timeout(1000)
                    opened = page.evaluate("""() => {
                        const opts = Array.from(document.querySelectorAll(
                            '[role="option"], [role="listbox"] *, li, .dropdown-item, .multiselect-item, input[type=checkbox]'
                        ));
                        return opts.map(o => ({
                            tag:o.tagName, role:o.getAttribute('role'),
                            text:(o.textContent||'').trim().slice(0,80),
                            type:o.type, checked:o.checked, value:o.value,
                        })).filter(o => o.text || o.type==='checkbox');
                    }""")
                    log.info(f"  Expanded options under {label!r} ({len(opened)}):")
                    seen = set()
                    for o in opened:
                        key = o['text']
                        if key in seen:
                            continue
                        seen.add(key)
                        log.info(f"    {o}")
                    save('property_type_dropdown.html', page.content())
                    page.keyboard.press('Escape')
                    page.wait_for_timeout(300)
                    break
            except Exception as e:
                log.warning(f"  click {label!r} failed: {e}")

        # ---- 3. One result card's outerHTML ----
        card_html = page.evaluate("""() => {
            // Heuristic: find a repeated-structure container holding many similar
            // children (the card list). Look for the element containing the text
            // 'Sale Date:' and walk up to a reasonably-sized card container.
            const all = Array.from(document.querySelectorAll('body *'));
            const hit = all.find(el => (el.textContent||'').includes('Sale Date:') && el.children.length <= 3);
            if (!hit) return null;
            let card = hit;
            for (let i = 0; i < 6 && card.parentElement; i++) {
                if (card.parentElement.querySelectorAll(':scope > *').length > 3) break;
                card = card.parentElement;
            }
            return card.outerHTML;
        }""")
        if card_html:
            save('sample_card.html', card_html)
            log.info(f"SAMPLE CARD HTML (first 3000 chars):\n{card_html[:3000]}")
        else:
            log.warning("Could not isolate a sample card via heuristic.")

        # ---- 4. Full body text (untruncated-ish) ----
        body_text = page.evaluate("() => document.body.innerText || ''")
        save('full_body_text.txt', body_text)
        log.info(f"FULL BODY TEXT length={len(body_text)} (saved as artifact). First 6000 chars:\n{body_text[:6000]}")

        save('landing.html', page.content())
        try:
            page.screenshot(path=os.path.join(ARTIFACT_DIR, 'landing.png'), full_page=True)
        except Exception:
            pass

        # ---- 5. Click the first card, see what happens ----
        json_responses.clear()
        try:
            clickable = page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('body *'));
                const hit = all.find(el => (el.textContent||'').includes('Sale Date:') && el.children.length <= 3);
                if (!hit) return null;
                let card = hit;
                for (let i = 0; i < 6 && card.parentElement; i++) {
                    if (card.parentElement.querySelectorAll(':scope > *').length > 3) break;
                    card = card.parentElement;
                }
                card.setAttribute('data-probe-target', '1');
                return true;
            }""")
            if clickable:
                url_before = page.url
                page.locator('[data-probe-target="1"]').first.click(timeout=8000)
                page.wait_for_timeout(3000)
                log.info(f"After click: url_before={url_before} url_after={page.url}")
                detail_text = page.evaluate("() => document.body.innerText || ''")
                save('after_click_body_text.txt', detail_text)
                log.info(f"AFTER-CLICK BODY TEXT (first 4000 chars):\n{detail_text[:4000]}")
                save('after_click.html', page.content())
                try:
                    page.screenshot(path=os.path.join(ARTIFACT_DIR, 'after_click.png'), full_page=True)
                except Exception:
                    pass
                log.info(f"JSON responses fired by the click: {len(json_responses)}")
                for r in json_responses:
                    try:
                        body = r.text()
                    except Exception as e:
                        body = f'<unreadable: {e}>'
                    log.info(f"  {r.request.method} {r.url} -> {r.status} ({len(body)} bytes)")
                    log.info(f"    preview: {body[:1500]}")
            else:
                log.warning("No clickable card found for step 5.")
        except Exception as e:
            log.warning(f"Click-through step failed: {e}")

        log.info("=== PROBE COMPLETE ===")
        browser.close()


if __name__ == '__main__':
    main()
