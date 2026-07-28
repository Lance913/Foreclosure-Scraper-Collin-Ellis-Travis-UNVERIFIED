"""
Probe v1 -- Travis County portal reconnaissance.

Goal:
  1. Dump the tccsearch.org RealEstate/SearchEntry.aspx form in full (every
     input/select/button, all select options, surrounding label text) --
     this is the assigned portal (per the county clerk's own site, which
     links to tccsearch.org as THE public records search).
  2. ALSO check travis.tx.publicsearch.us (found via research) -- if it's a
     live, functioning instance of the SAME platform already used for
     Bexar/Dallas/Tarrant/Denton/Johnson in the sister repo, that's a much
     faster/safer path (reuse PublicSearchScraper) than reverse-engineering
     a brand-new ASP.NET WebForms flow. Don't assume either way -- verify.

Only meaningful when run on GitHub Actions (US IP) -- the real portal likely
geo-blocks non-US IPs. Screenshots are uploaded as a workflow artifact so
they can be inspected without local access.
"""
import logging
import os
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

TCC_BASE = "https://www.tccsearch.org"
TCC_SEARCH = f"{TCC_BASE}/RealEstate/SearchEntry.aspx"
PS_BASE = "https://travis.tx.publicsearch.us"

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

ART_DIR = 'probe_artifacts'


def dump_form(page, label):
    info = page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll('input, select, textarea, button'));
        return els.map(el => {
            const o = {
                tag: el.tagName, type: el.type || '', name: el.name || '',
                id: el.id || '', placeholder: el.placeholder || '',
                value: (el.value||'').slice(0,60),
                ariaLabel: el.getAttribute('aria-label') || '',
                text: (el.tagName === 'BUTTON' ? (el.textContent||'').trim().slice(0,40) : ''),
                visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
            };
            if (el.tagName === 'SELECT') {
                o.options = Array.from(el.options).map(op => ({value: op.value, text: (op.textContent||'').trim()}));
            }
            return o;
        });
    }""")
    log.info(f"=== {label}: {len(info)} form elements ===")
    for el in info:
        log.info(f"  {el}")
    return info


def dump_body_text(page, label, n=100):
    txt = page.evaluate("() => document.body.innerText || ''")
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    log.info(f"=== {label}: body text ({len(lines)} lines, showing first {n}) ===")
    for ln in lines[:n]:
        log.info(f"  | {ln}")


def dump_frames(page, label):
    frames = page.frames
    log.info(f"=== {label}: {len(frames)} frame(s) ===")
    for f in frames:
        log.info(f"  frame url={f.url!r} name={f.name!r}")


def probe_tccsearch(pw):
    browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(user_agent=UA, viewport={'width': 1400, 'height': 1900})
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page.set_default_timeout(30_000)

    # 1. Root
    try:
        log.info(f"GOTO {TCC_BASE}")
        resp = page.goto(TCC_BASE, wait_until='networkidle')
        log.info(f"tcc root: status={resp.status if resp else '?'} finalURL={page.url} title={page.title()!r}")
        page.wait_for_timeout(1000)
        page.screenshot(path=f'{ART_DIR}/01_tcc_root.png', full_page=True)
        links = page.evaluate("""() => Array.from(document.querySelectorAll('a'))
            .map(a => ({text:(a.textContent||'').trim(), href:a.getAttribute('href')}))
            .filter(l => l.text || l.href)""")
        log.info(f"tcc root: {len(links)} links")
        for l in links[:80]:
            log.info(f"  {l}")
        dump_body_text(page, 'tcc root', n=60)
    except Exception as e:
        log.error(f"tcc root failed: {e}", exc_info=True)

    # 2. RealEstate/SearchEntry.aspx
    try:
        log.info(f"GOTO {TCC_SEARCH}")
        resp = page.goto(TCC_SEARCH, wait_until='networkidle')
        log.info(f"tcc search: status={resp.status if resp else '?'} finalURL={page.url} title={page.title()!r}")
        page.wait_for_timeout(2000)
        page.screenshot(path=f'{ART_DIR}/02_tcc_search_entry.png', full_page=True)
        dump_frames(page, 'tcc SearchEntry.aspx')
        dump_form(page, 'tcc SearchEntry.aspx')
        dump_body_text(page, 'tcc SearchEntry.aspx', n=100)

        # ASP.NET WebForms fingerprint -- viewstate/eventvalidation presence,
        # and any __doPostBack usage in the page (hints at how submit works).
        aspnet_hints = page.evaluate("""() => ({
            hasViewState: !!document.querySelector('input[name="__VIEWSTATE"]'),
            hasEventValidation: !!document.querySelector('input[name="__EVENTVALIDATION"]'),
            formAction: (document.querySelector('form') || {}).action || '',
            formCount: document.querySelectorAll('form').length,
        })""")
        log.info(f"tcc search: aspnet hints: {aspnet_hints}")

        # Also check every iframe's document for form fields (WebForms sites
        # sometimes nest the actual search inside a frame).
        for i, f in enumerate(page.frames):
            if f == page.main_frame:
                continue
            try:
                els = f.evaluate("""() => Array.from(document.querySelectorAll('input, select, textarea, button'))
                    .map(el => ({tag: el.tagName, type: el.type||'', name: el.name||'', id: el.id||''}))""")
                log.info(f"tcc search: frame[{i}] url={f.url} elements={els}")
            except Exception as fe:
                log.info(f"tcc search: frame[{i}] eval failed: {fe}")
    except Exception as e:
        log.error(f"tcc SearchEntry.aspx failed: {e}", exc_info=True)

    browser.close()


def probe_publicsearch(pw):
    browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(user_agent=UA)
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page.set_default_timeout(30_000)

    try:
        log.info(f"GOTO {PS_BASE}")
        resp = page.goto(PS_BASE, wait_until='networkidle')
        log.info(f"ps travis: status={resp.status if resp else '?'} finalURL={page.url} title={page.title()!r}")
        page.wait_for_timeout(1000)
        page.screenshot(path=f'{ART_DIR}/03_ps_travis_root.png', full_page=True)
        dump_body_text(page, 'ps travis root', n=40)

        log.info(f"GOTO {PS_BASE}/search/advanced")
        page.goto(f"{PS_BASE}/search/advanced", wait_until='networkidle')
        page.wait_for_timeout(1500)
        page.screenshot(path=f'{ART_DIR}/04_ps_travis_advanced.png', full_page=True)
        depts = page.evaluate("""() => {
            const out = [];
            const lb = document.querySelector('#department-listbox');
            if (lb) for (const o of lb.querySelectorAll('[role="option"],li')) out.push((o.textContent||'').trim());
            return out;
        }""")
        log.info(f"ps travis: department options: {depts}")
        if not depts:
            dump_body_text(page, 'ps travis advanced (no depts found)', n=40)
    except Exception as e:
        log.error(f"publicsearch travis failed: {e}", exc_info=True)

    browser.close()


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    with sync_playwright() as pw:
        log.info("########## PART 1: tccsearch.org ##########")
        probe_tccsearch(pw)
        log.info("########## PART 2: travis.tx.publicsearch.us ##########")
        probe_publicsearch(pw)


if __name__ == '__main__':
    main()
