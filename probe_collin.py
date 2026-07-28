"""
Probe v1 -- Collin County Foreclosure Notices portal.

Investigate: https://apps.collincountytx.gov/ForeclosureNotices

Nothing about this portal is known yet. Per SYSTEM_GUIDE.md Sec.6, do not guess
the form fields / filters / table schema -- dump them from the live page.

Goals for this pass:
  1. Dump every input/select/button on the landing page (id/name/placeholder/
     aria-label), and every <select> option verbatim -- especially anything
     resembling a "Property Type" filter (business hint: should offer
     "Residential Single Family" / "Residential Mobile Home" /
     "Residential Townhomes" -- verify exact wording, don't assume).
  2. Dump any table already present (headers + sample rows).
  3. Save full HTML + a screenshot as workflow artifacts so we can inspect
     structure that didn't make it into the JS dumps without burning another
     full run.
  4. If the landing page is itself the search form, try a broad/empty submit
     and dump what results look like (headers, row count, pagination controls).
"""
import logging
import os
import re
import sys

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

BASE = "https://apps.collincountytx.gov/ForeclosureNotices"
ARTIFACT_DIR = 'probe_artifacts'

DUMP_FORM_JS = """() => {
    const els = (sel) => Array.from(document.querySelectorAll(sel));
    const inputs = els('input').map(i => ({
        type: i.type, id: i.id, name: i.name, placeholder: i.placeholder,
        aria: i.getAttribute('aria-label'), value: i.value, checked: i.checked,
        cls: i.className,
    }));
    const selects = els('select').map(s => ({
        id: s.id, name: s.name, aria: s.getAttribute('aria-label'),
        multiple: s.multiple,
        options: Array.from(s.options).map(o => ({
            text: (o.textContent || '').trim(), value: o.value, selected: o.selected,
        })),
    }));
    const buttons = els('button, input[type=submit], input[type=button], a.btn, a[role=button]').map(b => ({
        tag: b.tagName, text: (b.textContent || b.value || '').trim(),
        id: b.id, name: b.name, type: b.type,
    }));
    const textareas = els('textarea').map(t => ({
        id: t.id, name: t.name, aria: t.getAttribute('aria-label'),
    }));
    // Checkbox / radio groups and any div-based "chip" style option lists
    // (React apps sometimes render options as <li>/<div role=option> instead
    // of a real <select>).
    const roleOptions = els('[role="option"], [role="listbox"] *, [role="checkbox"]').map(o => ({
        role: o.getAttribute('role'), text: (o.textContent || '').trim().slice(0, 80),
        id: o.id, cls: o.className,
    })).filter(o => o.text);
    return {
        inputs, selects, buttons, textareas, roleOptions,
        url: location.href, title: document.title,
    };
}"""

DUMP_TABLES_JS = """() => {
    return Array.from(document.querySelectorAll('table')).map(t => ({
        id: t.id, cls: t.className,
        headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent || '').trim()),
        rowCount: t.querySelectorAll('tr').length,
        sampleRows: Array.from(t.querySelectorAll('tr')).slice(1, 6).map(
            tr => Array.from(tr.querySelectorAll('td')).map(td => (td.textContent || '').trim())
        ),
    }));
}"""


def dump_form(page, label):
    info = page.evaluate(DUMP_FORM_JS)
    log.info(f"=== FORM DUMP ({label}) === url={info['url']} title={info['title']!r}")
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
    log.info(f"TEXTAREAS: {info['textareas']}")
    if info['roleOptions']:
        log.info(f"ROLE-BASED OPTIONS ({len(info['roleOptions'])}) -- possible custom dropdown/checklist:")
        for o in info['roleOptions'][:60]:
            log.info(f"  {o}")
    return info


def dump_tables(page, label):
    info = page.evaluate(DUMP_TABLES_JS)
    log.info(f"=== TABLES ({label}) === count={len(info)}")
    for idx, t in enumerate(info):
        log.info(f"  table[{idx}] id={t['id']!r} cls={t['cls']!r} headers={t['headers']} rowCount={t['rowCount']}")
        for r in t['sampleRows']:
            log.info(f"    row: {r}")
    return info


def save_snapshot(page, name):
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    html = page.content()
    html_path = os.path.join(ARTIFACT_DIR, f'{name}.html')
    with open(html_path, 'w') as f:
        f.write(html)
    try:
        page.screenshot(path=os.path.join(ARTIFACT_DIR, f'{name}.png'), full_page=True)
    except Exception as e:
        log.warning(f"screenshot failed for {name}: {e}")
    log.info(f"Saved snapshot '{name}': {len(html)} chars HTML + screenshot -> {ARTIFACT_DIR}/")


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

        log.info(f"Navigating to {BASE}")
        try:
            resp = page.goto(BASE, wait_until='networkidle', timeout=45000)
            log.info(f"HTTP status: {resp.status if resp else 'None'} | Final URL: {page.url}")
        except Exception as e:
            log.error(f"goto (networkidle) failed: {e} -- retrying with domcontentloaded")
            try:
                resp = page.goto(BASE, wait_until='domcontentloaded', timeout=45000)
                log.info(f"HTTP status: {resp.status if resp else 'None'} | Final URL: {page.url}")
            except Exception as e2:
                log.error(f"goto (domcontentloaded) also failed: {e2}")
                browser.close()
                sys.exit(1)
        page.wait_for_timeout(2500)

        body_text = page.evaluate("() => document.body.innerText || ''")
        log.info(f"BODY TEXT length={len(body_text)}. First 3000 chars:\n{body_text[:3000]}")

        dump_form(page, "landing")
        dump_tables(page, "landing")
        save_snapshot(page, "01_landing")

        # If this looks like an iframe-embedded app (common for .gov portals),
        # dump iframe info too.
        frames_info = [{'url': f.url, 'name': f.name} for f in page.frames]
        log.info(f"FRAMES: {frames_info}")
        if len(page.frames) > 1:
            for fr in page.frames[1:]:
                log.info(f"--- Inspecting iframe: {fr.url} ---")
                try:
                    finfo = fr.evaluate(DUMP_FORM_JS)
                    log.info(f"IFRAME FORM DUMP: {finfo}")
                except Exception as e:
                    log.warning(f"iframe dump failed: {e}")

        log.info("=== PROBE COMPLETE ===")
        browser.close()


if __name__ == '__main__':
    main()
