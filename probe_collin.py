"""
Probe v3 -- Collin County Foreclosure Notices portal.

v1: https://apps.collincountytx.gov/ForeclosureNotices -> 404 (IIS "File or
    directory not found").
v2: apps.collincountytx.gov/ is a bare default IIS placeholder (no directory
    listing). www.collincountytx.gov homepage has no "foreclosure" link, but
    has a "County Clerk" link (/county-clerk). The site also has an apps2
    subdomain (apps2.collincountytx.gov, used for judicial search) -- so
    "apps" numbering may vary per tool.

This pass:
  1. Dump the /county-clerk page fully -- links + text -- looking for a
     foreclosure-notices / trustee-sale link.
  2. Try that page's search feature if present.
  3. Try apps2/apps3.collincountytx.gov variants of the ForeclosureNotices path.
"""
import logging
import os

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

ARTIFACT_DIR = 'probe_artifacts'

APP_HOST_CANDIDATES = [
    "https://apps2.collincountytx.gov/ForeclosureNotices",
    "https://apps3.collincountytx.gov/ForeclosureNotices",
    "https://apps.collincountytx.gov/ForeclosureNotices2",
]

DUMP_LINKS_JS = """() => {
    return Array.from(document.querySelectorAll('a')).map(a => ({
        text: (a.textContent || '').trim().slice(0, 100),
        href: a.getAttribute('href'),
    })).filter(l => l.text || l.href);
}"""


def safe_name(url):
    return (url.replace('https://', '').replace('http://', '')
            .replace('/', '_').replace('?', '_').strip('_') or 'root')


def snapshot(page, url):
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    name = safe_name(url)
    with open(os.path.join(ARTIFACT_DIR, f'{name}.html'), 'w') as f:
        f.write(page.content())
    try:
        page.screenshot(path=os.path.join(ARTIFACT_DIR, f'{name}.png'), full_page=True)
    except Exception:
        pass


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

        # 1) County Clerk page -- full dump.
        url = "https://www.collincountytx.gov/county-clerk"
        log.info(f"----- {url} -----")
        try:
            resp = page.goto(url, wait_until='networkidle', timeout=30000)
            log.info(f"  status={resp.status if resp else None} final_url={page.url} title={page.title()!r}")
        except Exception as e:
            log.warning(f"  load failed: {e}")
        page.wait_for_timeout(1000)
        body_text = page.evaluate("() => (document.body.innerText || '').trim()")
        log.info(f"  BODY TEXT (first 4000 chars):\n{body_text[:4000]}")
        links = page.evaluate(DUMP_LINKS_JS)
        log.info(f"  ALL LINKS ({len(links)}):")
        for l in links:
            log.info(f"    {l}")
        snapshot(page, url)

        # 2) Site-wide search for "foreclosure" if the site exposes /search?q=
        for search_url in [
            "https://www.collincountytx.gov/search?q=foreclosure",
            "https://www.collincountytx.gov/Search?searchPhrase=foreclosure",
        ]:
            log.info(f"----- {search_url} -----")
            try:
                resp = page.goto(search_url, wait_until='networkidle', timeout=20000)
                log.info(f"  status={resp.status if resp else None} final_url={page.url}")
                page.wait_for_timeout(1000)
                txt = page.evaluate("() => (document.body.innerText || '').trim()")
                log.info(f"  body first 2000 chars: {txt[:2000]}")
                slinks = page.evaluate(DUMP_LINKS_JS)
                hits = [l for l in slinks if 'foreclos' in (l['text'] + str(l['href'])).lower()]
                log.info(f"  links mentioning 'foreclos': {hits}")
            except Exception as e:
                log.warning(f"  failed: {e}")

        # 3) Alternate app-host guesses.
        for url in APP_HOST_CANDIDATES:
            log.info(f"----- Trying {url} -----")
            try:
                resp = page.goto(url, wait_until='domcontentloaded', timeout=20000)
                status = resp.status if resp else None
                title = page.title()
                log.info(f"  status={status} final_url={page.url} title={title!r}")
                if status and status < 400:
                    txt = page.evaluate("() => (document.body.innerText || '').trim()")
                    log.info(f"  body first 1000: {txt[:1000]}")
                    snapshot(page, url)
            except Exception as e:
                log.info(f"  FAILED: {str(e)[:150]}")

        log.info("=== PROBE COMPLETE ===")
        browser.close()


if __name__ == '__main__':
    main()
