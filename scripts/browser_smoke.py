"""Optional browser check. Requires Playwright and an installed Chromium browser."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory() as directory:
        os.environ['HAVEN_DATA_DIR'] = directory
        from manage import demo
        with contextlib.redirect_stdout(io.StringIO()):
            demo()
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8765'], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            for _ in range(50):
                try:
                    with urlopen('http://127.0.0.1:8765/api/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.2)
            else:
                raise RuntimeError('Preview did not start.')
            with sync_playwright() as p:
                edge = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
                browser = p.chromium.launch(headless=True, **({'executable_path':str(edge)} if edge.exists() else {}))
                page = browser.new_page(viewport={'width':1440,'height':1000})
                errors=[]
                page.on('pageerror', lambda err: errors.append(str(err)))
                page.goto('http://127.0.0.1:8765')
                page.locator('#email').fill('admin@haven.local')
                page.locator('#password').fill('HavenDemo!2026')
                page.get_by_role('button',name='Sign in to Haven').click()
                page.locator('.summary-grid').wait_for()
                out=ROOT/'test-results'
                out.mkdir(exist_ok=True)
                page.screenshot(path=str(out/'dashboard-desktop.png'),full_page=True)
                for name in ['properties','tenants','invoices','payments','arrears','deposits','service','rent','repairs','inspections','communications','documents','tasks','reports','settings']:
                    page.locator(f'.nav-item[data-page="{name}"]').click()
                    page.locator('#main h1').wait_for()
                    assert page.locator('#main').inner_text().strip(), name
                page.locator('.nav-item[data-page="tenants"]').click()
                page.get_by_role('button',name='Open file').first.click()
                assert page.locator('#modal').is_visible()
                page.get_by_role('button',name='Close dialog').click()
                page.locator('.nav-item[data-page="invoices"]').click()
                page.get_by_role('button',name='Manage',exact=True).first.click()
                page.get_by_role('button',name='Create tenant link').click()
                link=page.locator('.link-output').inner_text()
                tenant=browser.new_page()
                tenant.goto(link)
                tenant.locator('#public-form').wait_for()
                assert 'Submit your payment receipt' in tenant.inner_text('body')
                page.get_by_role('button',name='Close dialog').click()
                page.locator('.nav-item[data-page="overview"]').click()
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(out/'dashboard-mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile page overflows horizontally'
                page.get_by_role('button',name='Toggle navigation').click()
                page.locator('.nav-item[data-page="properties"]').click()
                assert page.locator('.property-card').count()==6
                assert not errors, errors
                browser.close()
            print('Browser checks passed: login, all 16 screens, tenancy detail, receipt link, mobile navigation. No JavaScript errors.')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
