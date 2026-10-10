"""Optional real-browser check: python tests/browser_check.py --screenshot PATH."""
import argparse
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Library, create_app
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect
import uvicorn


def run(screenshot=None):
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        photos = base / '测试照片'
        photos.mkdir()
        for i in range(1, 73):
            image = Image.new('RGB', (3200, 1800) if i==1 else (1400, 900), ((35 + i * 8)%255, (93 + i * 5)%255, (138 + i * 4)%255))
            draw = ImageDraw.Draw(image)
            draw.ellipse((880 + i * 7, 90, 1070 + i * 7, 280), fill=(246, 215, 147))
            draw.polygon([(0, 850), (370+i*8, 310+i*8), (900, 850)], fill=(49, 88, 106))
            draw.polygon([(470, 900), (1040-i*8, 350+i*8), (1500, 900)], fill=(31, 62, 79))
            draw.rectangle((0, 800, 1400, 900), fill=(24, 47, 62))
            image.save(photos / f'{i:02d}.jpg')
        library = Library(base / 'data')
        app, token = create_app(library, 'browser-check-token')
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        errors = []
        metadata_requests = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: metadata_requests.append(request.url) if '/api/context/' in request.url or '/api/photos?' in request.url else None)
                copy_destination=base/'复制结果'
                opened_locations=[]
                page.route('**/api/choose-folder', lambda route: route.fulfill(content_type='application/json',body=json.dumps({'path':str(copy_destination)})))
                def open_folder_route(route):
                    opened_locations.append(route.request.post_data_json['path'])
                    route.fulfill(content_type='application/json',body='{"ok":true}')
                page.route('**/api/open-folder', open_folder_route)
                page.goto(f'http://127.0.0.1:{port}/#token={token}')
                page.locator('#folder').fill(str(photos))
                page.locator('#scan').click()
                expect(page.locator('#filename')).to_have_text('01.jpg')
                expect(page.locator('#photo')).to_be_visible()
                expect(page.locator('#nextPhoto')).to_be_visible()
                expect(page.locator('#nextLabel')).to_contain_text('02.jpg')
                assert page.locator('#nextPane').bounding_box()['x'] > page.locator('#currentPane').bounding_box()['x']
                page.locator('#photo').click()
                expect(page.locator('#detail')).to_be_visible()
                expect(page.locator('#detailImage')).to_be_visible()
                assert page.locator('#detailImage').evaluate('(image)=>image.naturalWidth')==3200
                assert page.locator('#detailImage').bounding_box()['width']==3200
                page.keyboard.press('Escape')
                expect(page.locator('#detail')).to_be_hidden()
                page.locator('#nextPhoto').click()
                expect(page.locator('#detailImage')).to_be_visible()
                expect(page.locator('#detailTitle')).to_contain_text('02.jpg')
                page.locator('#detailClose').click()
                # Kept images disappear from the strip, even in the explicit All view.
                page.keyboard.press('ArrowUp')
                expect(page.locator('#filename')).to_have_text('02.jpg')
                expect(page.locator('#filmstrip [data-photo-id="1"]')).to_have_count(0)
                assert library.photo(1)['status']==1
                page.locator('#saveKept').click()
                expect(page.locator('#saveDialog')).to_be_visible()
                page.locator('#chooseDestination').click()
                expect(page.locator('#destination')).to_have_value(str(copy_destination))
                page.locator('#copyKept').click()
                expect(page.locator('#saveSummary')).to_contain_text('成功 1 张')
                assert (copy_destination/'01.jpg').read_bytes()==(photos/'01.jpg').read_bytes()
                page.locator('#openDestination').click()
                expect(page.locator('#saveDestination')).to_contain_text(str(copy_destination))
                page.locator('#saveClose').click()
                page.locator('[data-view="all"]').click()
                expect(page.locator('#filename')).to_have_text('01.jpg')
                expect(page.locator('#filmstrip [data-photo-id="1"]')).to_have_count(0)
                page.locator('[data-view="pending"]').click()
                expect(page.locator('#filename')).to_have_text('02.jpg')
                page.keyboard.press('Control+z')
                expect(page.locator('#filename')).to_have_text('01.jpg')
                expect(page.locator('#photo')).to_be_visible()
                page.keyboard.press('ArrowRight')
                expect(page.locator('#filename')).to_have_text('02.jpg')
                expect(page.locator('#photo')).to_be_visible()
                expect(page.locator('#nextLabel')).to_contain_text('03.jpg')
                page.keyboard.press('ArrowDown')
                expect(page.locator('#filename')).to_have_text('03.jpg')
                assert not (photos / '02.jpg').exists()
                expect(page.locator('#n-deleted')).to_have_text('1')
                expect(page.locator('#lastDeletePath')).to_contain_text('.photo-sieve-trash')
                page.locator('[data-view="deleted"]').click()
                expect(page.locator('.deleted-card')).to_have_count(1)
                expect(page.locator('.deleted-card')).to_contain_text(str(photos/'02.jpg'))
                expect(page.locator('.deleted-card')).to_contain_text('待删除位置')
                page.locator('.deleted-preview').click()
                expect(page.locator('#detailImage')).to_be_visible()
                page.keyboard.press('Escape')
                page.locator('.deleted-card .row button').nth(1).click()
                page.locator('.restore-photo').click()
                expect(page.locator('.deleted-card')).to_have_count(0)
                expect(page.locator('#n-deleted')).to_have_text('0')
                assert (photos/'02.jpg').is_file()
                page.locator('[data-view="pending"]').click()
                page.locator('.card').filter(has_text='02.jpg').first.click()
                expect(page.locator('#filename')).to_have_text('02.jpg')
                expect(page.locator('#photo')).to_be_visible()
                page.keyboard.press('ArrowDown')
                expect(page.locator('#filename')).to_have_text('03.jpg')
                expect(page.locator('#photo')).to_be_visible()
                page.keyboard.press('ArrowUp')
                expect(page.locator('#filename')).to_have_text('04.jpg')
                page.keyboard.press('ArrowLeft')
                expect(page.locator('#filename')).to_have_text('02.jpg')
                expect(page.locator('#photo')).to_be_visible()
                assert (photos / '02.jpg').exists()
                assert library.photo(3)['status']==1
                expect(page.locator('#nextLabel')).to_contain_text('04.jpg')
                expect(page.locator('#filmstrip [data-photo-id="3"]')).to_have_count(0)
                page.keyboard.press('Control+z')
                expect(page.locator('#filename')).to_have_text('03.jpg')
                expect(page.locator('#photo')).to_be_visible()
                page.locator('#previous').click()
                expect(page.locator('#filename')).to_have_text('02.jpg')
                expect(page.locator('#photo')).to_be_visible()
                page.locator('#photo').click(button='right')
                expect(page.locator('#filename')).to_have_text('03.jpg')
                expect(page.locator('#photo')).to_be_visible()
                page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',repeat:true,bubbles:true}))")
                assert (photos / '03.jpg').exists()
                page.keyboard.press('ArrowDown')
                expect(page.locator('#filename')).to_have_text('04.jpg')
                expect(page.locator('#photo')).to_be_visible()
                page.keyboard.press('ArrowDown')
                expect(page.locator('#filename')).to_have_text('05.jpg')
                expect(page.locator('#photo')).to_be_visible()
                page.keyboard.press('ArrowLeft')
                expect(page.locator('#filename')).to_have_text('04.jpg')
                expect(page.locator('#photo')).to_be_visible()
                page.keyboard.press('ArrowLeft')
                expect(page.locator('#filename')).to_have_text('03.jpg')
                expect(page.locator('#photo')).to_be_visible()
                assert (photos / '03.jpg').exists() and (photos / '04.jpg').exists()
                page.locator('#gridToggle').click()
                expect(page.locator('.card')).to_have_count(60)
                page.locator('.card').first.click()
                expect(page.locator('#filename')).to_have_text('01.jpg')
                expect(page.locator('#photo')).to_be_visible()
                with page.expect_download() as info:
                    page.locator('[data-export="all"]').click()
                assert info.value.suggested_filename == 'photos-all.csv'
                page.reload()
                expect(page.locator('#filename')).to_have_text('01.jpg')
                expect(page.locator('#photo')).to_be_visible()
                expect(page.locator('#nextPhoto')).to_be_visible()
                if screenshot:
                    page.screenshot(path=str(screenshot), full_page=True)
                # Deletion undo must survive reloading the browser.
                page.keyboard.press('ArrowDown')
                expect(page.locator('#filename')).to_have_text('02.jpg')
                page.reload()
                expect(page.locator('#filename')).to_have_text('02.jpg')
                page.keyboard.press('Control+z')
                expect(page.locator('#filename')).to_have_text('01.jpg')
                expect(page.locator('#photo')).to_be_visible()
                # Emptying the review queue must still leave Ctrl+Z available.
                count_before=len(metadata_requests)
                started=time.perf_counter()
                for i in range(1, 73):
                    expect(page.locator('#filename')).to_have_text(f'{i:02d}.jpg')
                    expect(page.locator('#photo')).to_be_visible()
                    page.keyboard.press('ArrowDown')
                elapsed=time.perf_counter()-started
                repeated_queries=len(metadata_requests)-count_before
                assert repeated_queries<12, repeated_queries
                print(f'72 synthetic JPEG deletes: {elapsed:.2f}s, {repeated_queries} queue queries (excluding image and action requests)')
                expect(page.locator('#empty')).to_be_visible()
                page.keyboard.press('ArrowLeft')
                expect(page.locator('#filename')).to_have_text('72.jpg')
                expect(page.locator('#photo')).to_be_visible()
                assert (photos / '72.jpg').exists()
                page.keyboard.press('ArrowUp')
                expect(page.locator('#empty')).to_be_visible()
                page.locator('#saveKept').click()
                move_destination=base/'移动结果'
                page.locator('#destination').fill(str(move_destination))
                page.locator('#moveKept').click()
                expect(page.locator('#saveSummary')).to_contain_text('移动：成功 1 张')
                assert not (photos/'72.jpg').exists()
                assert (move_destination/'72.jpg').is_file()
                assert str(copy_destination) in opened_locations
                assert str(photos/'.photo-sieve-trash') in opened_locations
                assert not errors, errors
                browser.close()
            print('Browser check passed: focus detail, Up/keep, hidden kept thumbnails, Left/restore, deleted list and locations, copy/move kept, continuous deletion and restart.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshot', type=Path)
    run(parser.parse_args().screenshot)
