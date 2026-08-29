"""
Probe -- Travis County's real official records search, www.tccsearch.org
(the old travis.tx.publicsearch.us portal is confirmed dead -- see
probe_travis.py's earlier findings / PR notes. Traced the current live
link via the county's own real-property page, which points here).

curl/WebFetch both got a Cloudflare challenge (403 / cf-mitigated:
challenge) -- but Cloudflare's basic/managed challenge tier often resolves
transparently for a real browser with a normal JS-capable fingerprint,
unlike a hard interactive reCAPTCHA/AWS WAF wall. Testing with a real
Playwright browser from a GitHub Actions (US) IP rather than assuming
either way, per SYSTEM_GUIDE.md S6.
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
log = logging.getLogger('PROBE')

ART_DIR = 'probe_artifacts'
BASE_URL = 'https://www.tccsearch.org/'


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


def dump_body_text(page, label, n=80):
    txt = page.evaluate("() => document.body.innerText || ''")
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    log.info(f"=== {label}: body text ({len(lines)} lines, showing first {n}) ===")
    for ln in lines[:n]:
        log.info(f"  | {ln}")
    return lines


def dump_form_fields(page, label):
    fields = page.evaluate("""
        () => {
            const out = [];
            document.querySelectorAll('input, select, button').forEach(el => {
                out.push({
                    tag: el.tagName, type: el.type || '', id: el.id || '',
                    name: el.name || '', placeholder: el.placeholder || '',
                    text: (el.textContent || el.value || '').trim().slice(0, 60),
                    visible: !!(el.offsetWidth || el.offsetHeight),
                });
            });
            return out;
        }
    """)
    log.info(f"=== {label}: {len(fields)} form field(s) ===")
    for f in fields:
        if f['visible']:
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

        log.info(f"Loading {BASE_URL} ...")
        # networkidle timed out here in an earlier run -- a Cloudflare
        # challenge page likely keeps some background polling/heartbeat
        # activity going, so networkidle may never be reached (same class
        # of issue as Collin's Leaflet map -- see scrapers/collin.py).
        resp = page.goto(BASE_URL, wait_until='domcontentloaded', timeout=30_000)
        log.info(f"status={resp.status if resp else None} final_url={page.url!r} title={page.title()!r}")
        page.wait_for_timeout(3000)  # give a Cloudflare JS challenge time to auto-resolve
        shot(page, '01_landing')

        title = page.title()
        cf_challenge = 'just a moment' in title.lower() or 'attention required' in title.lower()
        log.info(f"Cloudflare challenge page still showing: {cf_challenge}")

        if cf_challenge:
            log.info("Waiting up to 15s more for the challenge to auto-resolve...")
            page.wait_for_timeout(15000)
            log.info(f"After wait: title={page.title()!r} url={page.url!r}")
            shot(page, '02_after_wait')

        dump_body_text(page, 'landing')
        dump_form_fields(page, 'landing')

        browser.close()

    log.info("Probe complete.")


if __name__ == '__main__':
    main()
