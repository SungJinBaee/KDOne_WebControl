"""Isolated login check; no real config, scheduler or wallpad connection."""
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from webapp import create_app
from home_service import HomeService


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app({'TESTING': True, 'DEMO': False, 'ADMIN_PIN': '012345',
                          'SECRET_KEY': 'browser-test', 'CONFIG_DIR': directory,
                          'DATA_DIR': directory, 'START_SCHEDULER': False,
                          'SESSION_COOKIE_SECURE': False}, service=HomeService(demo=True))
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                for width in (1440, 390):
                    context = browser.new_context(viewport={'width': width, 'height': 900})
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('#admin-dialog')).to_be_visible()
                    keypad = page.locator('#pin-keypad')
                    layout = keypad.locator('button').all_text_contents()
                    assert sorted(layout[:10]) == list('0123456789')
                    expect(page.locator('#admin-submit')).to_be_disabled()
                    for digit in '999999':
                        keypad.get_by_role('button', name=digit, exact=True).click()
                    page.locator('#admin-submit').click()
                    expect(page.locator('#admin-error')).not_to_be_empty()
                    expect(page.locator('#admin-pin')).to_have_value('')
                    assert layout != keypad.locator('button').all_text_contents()
                    if width == 1440:
                        page.locator('#admin-pin').fill('01ab23')
                        expect(page.locator('#admin-pin')).to_have_value('0123')
                        expect(page.locator('#admin-submit')).to_be_disabled()
                        page.locator('#admin-pin').fill('')
                        page.locator('#admin-pin').focus()
                        page.keyboard.type('012345')
                        expect(page.locator('#admin-pin')).to_have_value('012345')
                        page.keyboard.press('Enter')
                    else:
                        keypad.get_by_role('button', name='0', exact=True).click()
                        page.keyboard.type('12345')
                        expect(page.locator('#admin-pin')).to_have_value('012345')
                        page.keyboard.press('Enter')
                    expect(page.locator('#admin-dialog')).not_to_be_visible()
                    page.locator('[data-tab="usage"]').click()
                    expect(page.locator('#usage-panel')).to_be_visible()
                    expect(page.locator('#usage-status')).to_contain_text('조회')
                    page.locator('#usage-month').fill('2024-02')
                    page.locator('#usage-compare').check()
                    page.locator('#usage-submit').click()
                    expect(page.locator('#usage-status')).to_contain_text('조회 완료')
                    assert page.locator('.usage-table tbody tr').count() == 29
                    expect(page.locator('.usage-table thead')).to_contain_text('전년')
                    assert page.locator('.usage-table thead th').all_text_contents() == ['기간', '전년', '선택연도', '증감']
                    page.locator('#usage-month').fill('2026-11')
                    page.locator('#usage-submit').click()
                    expect(page.locator('#usage-status')).to_contain_text('조회 완료')
                    assert page.locator('.usage-table tbody tr').count() == 30
                    expect(page.locator('.usage-table tbody tr').last.locator('td').first).not_to_have_text('—')
                    expect(page.locator('#usage-result .notice')).to_have_count(0)
                    page.locator('#usage-period').select_option('Month')
                    page.locator('#usage-year').fill('2026')
                    for category in ('Electricity', 'Water', 'Heating', 'Hotwater', 'Gas'):
                        page.locator('#usage-category').select_option(category)
                        page.locator('#usage-submit').click()
                        expect(page.locator('#usage-status')).to_contain_text('조회 완료')
                        assert page.locator('.usage-table tbody tr').count() == 12
                        expect(page.locator('.usage-table tbody tr').last.locator('th')).to_have_text('12')
                        expect(page.locator('.usage-table tbody tr').last.locator('td').first).not_to_have_text('—')
                    page.locator('#usage-compare').uncheck()
                    page.locator('#usage-submit').click()
                    expect(page.locator('#usage-status')).to_contain_text('조회 완료')
                    assert page.locator('.usage-table tbody tr').count() == 12
                    page.locator('#usage-panel > .settings-card > details > summary').click()
                    page.locator('#usage-category').select_option('Electricity')
                    page.locator('#usage-edit-day').fill('2026-11-20')
                    page.locator('#usage-history-button').click()
                    expect(page.locator('#usage-edit-save')).to_be_enabled()
                    page.locator('#usage-edit-value').fill('0.5')
                    page.locator('#usage-edit-unit').fill('kWh')
                    page.locator('#usage-edit-reason').fill('browser verified correction')
                    page.locator('#usage-edit-save').click()
                    expect(page.locator('#usage-edit-status')).to_contain_text('수정 이력')
                    # Refresh produces a conflict against a locked manual correction.
                    page.evaluate("""fetch('api/usage/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({category:'Electricity'})}).then(r=>r.json())""")
                    # A deterministic observed change supplies a review independent of today's date.
                    archived=app.extensions['usage_archive']
                    from datetime import date
                    archived.ingest('Electricity',date(2026,11,20),date(2026,11,20),{'Resources':[{'Start_Time':'2026-11-20','Amount':'10','Unit':'kWh'}]},'browser-test')
                    page.evaluate('loadUsageMaintenance()')
                    expect(page.locator('.usage-review').first).to_be_visible()
                    page.locator('.usage-review').first.locator('input').fill('keep corrected value')
                    page.locator('.usage-review').first.get_by_role('button',name='기존 값 유지',exact=True).click()
                    expect(page.locator('#usage-edit-status')).to_contain_text('검토 결과')
                    with page.expect_download() as download:
                        page.locator('#usage-export').click()
                    assert download.value.suggested_filename=='kdone-usage-export.zip'
                    page.evaluate("""renderUsage({name:'전기',selected:'2026-11',period:'Day',demo:true,
                      current:{start:'2026-11-01',end:'2026-11-23',unit:'kWh',total:6,points:[
                        {date:'2026-11-01',amount:5,unit:'kWh'},
                        {date:'2026-11-02',amount:1,unit:'kWh'}]},
                      previous:{start:'2025-11-01',end:'2025-11-30',unit:'kWh',total:9,points:[
                        {date:'2025-11-01',amount:3,unit:'kWh'},
                        {date:'2025-11-02',amount:2,unit:'kWh'},
                        {date:'2025-11-30',amount:4,unit:'kWh'}]}})""")
                    rows = page.locator('.usage-table tbody tr')
                    assert rows.nth(0).locator('td').all_text_contents() == ['3 kWh', '5 kWh', '+2 kWh']
                    assert rows.nth(1).locator('td').all_text_contents() == ['2 kWh', '1 kWh', '-1 kWh']
                    assert rows.last.locator('td').all_text_contents() == ['4 kWh', '—', '—']
                    page.evaluate("""renderUsage({name:'전기',selected:'2026-11',period:'Day',
                      current:{start:'2026-11-01',end:'2026-11-23',points:[]},
                      previous:{start:'2025-11-01',end:'2025-11-23',points:[]}})""")
                    expect(page.locator('#usage-result .notice')).to_contain_text('서버 업데이트')
                    page.locator('[data-tab="lights"]').click()
                    expect(page.locator('#usage-panel')).not_to_be_visible()
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                    context.close()
                browser.close()
            assert not errors, errors
            print('PIN and usage browser checks passed: desktop/mobile, five categories, daily/monthly, previous year')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            app.extensions['jobs'].close()


if __name__ == '__main__':
    main()
