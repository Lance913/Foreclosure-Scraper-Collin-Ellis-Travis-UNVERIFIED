"""
Probe v2 -- Collin County Foreclosure Notices portal.

v1 result: https://apps.collincountytx.gov/ForeclosureNotices -> HTTP 404
("File or directory not found" -- classic IIS 404 page), reachable (no
geo-block, no timeout). So the exact assignment URL is off somehow -- find
the real path.

This pass:
  1. Dump https://apps.collincountytx.gov/ (root) -- links + page text.
  2. Try a handful of case/slash/naming variations of the ForeclosureNotices
     path directly.
  3. Dump the main county site (collincountytx.gov) for a nav link or search
     result mentioning "foreclosure".
"""
import logging
import os
import sys

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

ARTIFACT_DIR = 'probe_artifacts'

CANDIDATES = [
    "https://apps.collincountytx.gov/",
    "https://apps.collincountytx.gov/ForeclosureNotices",
    "https://apps.collincountytx.gov/ForeclosureNotices/",
    "https://apps.collincountytx.gov/foreclosurenotices",
    "https://apps.collincountytx.gov/Foreclosure",
    "https://apps.collincountytx.gov/ForeclosureNotice",
    "https://apps.collincountytx.gov/ForeclosureSearch",
    "https://apps.collincountytx.gov/ForeclosureNotices/Search",
    "https://apps.collincountytx.gov/ForeclosureNotices/Default.aspx",
    "https://apps.collincountytx.gov/ForeclosureNotices/Home",
    "https://www.collincountytx.gov/",
]

DUMP_LINKS_JS = """() => {
    return Array.from(document.querySelectorAll('a')).map(a => ({
        text: (a.textContent || '').trim().slice(0, 90),
        href: a.getAttribute('href'),
    })).filter(l => l.text || l.href);
}"""


def safe_name(url):
    return (url.replace('https://', '').replace('http://', '')
            .replace('/', '_').strip('_') or 'root')


def probe_url(page, url):
    log.info(f"----- Trying {url} -----")
    try:
        resp = page.goto(url, wait_until='domcontentloaded', timeout=30000)
        status = resp.status if resp else None
        log.info(f"  status={status} final_url={page.url} title={page.title()!r}")
    except Exception as e:
        log.info(f"  FAILED to load: {str(e)[:200]}")
        return
    try:
        page.wait_for_load_state('networkidle', timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(800)

    body_text = page.evaluate("() => (document.body.innerText || '').trim()")
    log.info(f"  body length={len(body_text)}; first 500 chars: {body_text[:500]!r}")

    is_404 = ('not found' in body_text.lower()[:200]) or (status and status >= 400)
    if not is_404 or 'foreclosure' in body_text.lower():
        # Worth a closer look -- dump links and save a snapshot.
        links = page.evaluate(DUMP_LINKS_JS)
        interesting = [l for l in links if 'foreclos' in (l['text'] + str(l['href'])).lower()]
        if interesting:
            log.info(f"  LINKS mentioning 'foreclos': {interesting}")
        else:
            log.info(f"  total links: {len(links)}; first 25: {links[:25]}")
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

        for url in CANDIDATES:
            probe_url(page, url)

        # Try the main site's own search box for "foreclosure" if one exists.
        log.info("----- Searching www.collincountytx.gov for 'foreclosure' links -----")
        try:
            page.goto("https://www.collincountytx.gov/", wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(1000)
            links = page.evaluate(DUMP_LINKS_JS)
            hits = [l for l in links if 'foreclos' in (l['text'] + str(l['href'])).lower()]
            log.info(f"Homepage links mentioning 'foreclos': {hits}")
            clerk_links = [l for l in links if 'clerk' in (l['text'] + str(l['href'])).lower()]
            log.info(f"Homepage links mentioning 'clerk': {clerk_links}")
        except Exception as e:
            log.warning(f"main site probe failed: {e}")

        log.info("=== PROBE COMPLETE ===")
        browser.close()


if __name__ == '__main__':
    main()
