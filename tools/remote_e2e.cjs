// Drive the phone remote page in a phone-sized Chromium, like a person would: swipes, buttons,
// mode switch, stars, the label bottom sheet. Prints one JSON report line. Used by check_remote.py.
// usage: node remote_e2e.cjs URL OUTPUT_DIR
const path = require('path');
let playwright;
try { playwright = require('playwright'); } catch (_) { playwright = require(path.join(require('child_process').execSync('npm root -g').toString().trim(), 'playwright')); }
const { chromium, devices } = playwright;

(async () => {
  const [url, out] = process.argv.slice(2);
  const report = { steps: [], errors: [] };
  const launch = { headless: true };
  if (process.env.PLAYWRIGHT_CHROMIUM) launch.executablePath = process.env.PLAYWRIGHT_CHROMIUM;
  const browser = await chromium.launch(launch);
  const context = await browser.newContext({ ...devices['iPhone 13'], locale: 'zh-CN' });
  const page = await context.newPage();
  page.on('pageerror', (e) => report.errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !m.text().startsWith('Failed to load resource')) report.errors.push('console: ' + m.text()); });
  page.on('response', (r) => { if (r.status() >= 400 && !new URL(r.url()).pathname.startsWith('/art/')) report.errors.push(`http ${r.status()} ${new URL(r.url()).pathname}`); });
  const shot = (name) => page.screenshot({ path: path.join(out, name + '.png') });
  const status = () => page.textContent('#status');
  const pending = async () => Number(((await status()).match(/未筛选 ([\d,]+)/) || [0, '-1'])[1].replace(/,/g, ''));
  const step = (name, extra = {}) => report.steps.push({ name, ...extra });
  const waitFor = async (fn, what, ms = 15000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { if (await fn()) return; await page.waitForTimeout(60); }
    throw new Error('timed out: ' + what);
  };
  const photoId = () => page.evaluate(() => document.getElementById('photo').getAttribute('src') || '');
  async function swipe(dx, dy) {
    const box = await page.locator('#stage').boundingBox();
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.mouse.move(x, y); await page.mouse.down();
    for (let i = 1; i <= 8; i++) { await page.mouse.move(x + dx * i / 8, y + dy * i / 8); await page.waitForTimeout(12); }
    await page.mouse.up();
  }
  try {
    await context.addInitScript(() => localStorage.setItem('photosieve-controller-only', 'false'));
    const usbUrl = new URL(url); usbUrl.searchParams.set('via','usb');
    await page.goto(usbUrl.href);
    if (new URL(page.url()).hash) throw new Error('key left in the address bar');
    await page.waitForSelector('#photo.ready', { timeout: 20000 });
    const start = await pending(); step('loaded', { pending: start }); await shot('01-screen');

    await page.click('#controller-toggle');
    await page.waitForTimeout(400);
    if (!(await page.textContent('#connection')).includes('USB')) throw new Error('USB mode missing');
    let previewRequests = 0;
    const latencies = [];
    page.on('requestfinished', request => {
      if (request.url().endsWith('/api/act')) { const timing = request.timing(); latencies.push(timing.responseEnd); }
    });
    const countPreview = request => { if (request.url().includes('/api/photo')) previewRequests++; };
    page.on('request', countPreview);
    await page.evaluate(() => { for (let i = 0; i < 4; i++) document.getElementById('next').click(); });
    await page.waitForTimeout(1000);
    if (previewRequests) throw new Error('controller mode fetched photos');
    if (await page.locator('#stage').isVisible()) throw new Error('controller still shows preview');
    if (await page.locator('.controls-grade').isVisible()) throw new Error('grading controls visible in screening');
    const bounds = await page.locator('.controls-screen').boundingBox();
    if (bounds.y + bounds.height > page.viewportSize().height + 1) throw new Error('controller exceeds viewport');
    for (const size of [{width:360,height:740},{width:412,height:915},{width:844,height:390}]) {
      await page.setViewportSize(size);
      const box = await page.locator('.controls-screen').boundingBox();
      if (box.x < 0 || box.x + box.width > size.width + 1 || box.y + box.height > size.height + 1) throw new Error('controller overflow at '+size.width);
      if (await page.locator('.controls-grade').isVisible()) throw new Error('screening includes rating controls');
      await shot('controller-'+size.width);
    }
    await page.setViewportSize({width:390,height:844});
    await shot('00-controller'); step('controller-no-preview-and-rapid-next', { commandRoundTripMs: latencies.map(n => Math.round(n)) });
    page.off('request', countPreview);
    await page.click('#controller-toggle');
    await page.waitForSelector('#photo.ready');

    // Double-tap zooms in at full resolution (no action is taken), double-tap again zooms back out.
    const box0 = await page.locator('#stage').boundingBox();
    const hiRes = page.waitForResponse((r) => r.url().includes('w=3200') && r.status() === 200, { timeout: 15000 });
    await page.mouse.dblclick(box0.x + box0.width / 2, box0.y + box0.height / 2);
    await page.waitForSelector('#zoom-chip:not([hidden])', { timeout: 5000 }); await hiRes;
    await page.waitForTimeout(500);
    const zoomed = await page.evaluate(() => document.getElementById('photo').style.transform);
    if (!/scale\(/.test(zoomed)) throw new Error('double-tap did not zoom: ' + zoomed);
    await shot('01b-zoomed');
    await swipe(0, -300); await page.waitForTimeout(500);
    if ((await pending()) !== start) throw new Error('a drag while zoomed must pan, not keep');
    await page.mouse.dblclick(box0.x + box0.width / 2, box0.y + box0.height / 2);
    await page.waitForSelector('#zoom-chip', { state: 'hidden', timeout: 5000 }); await page.waitForTimeout(500);
    step('double-tap-zoom');

    // A two-finger pinch zooms in; pinching back below 1x leaves zoom.
    const cdp = await context.newCDPSession(page);
    const cx = box0.x + box0.width / 2, cy = box0.y + box0.height / 2;
    const touch = (type, d) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x: cx - d, y: cy, id: 1 }, { x: cx + d, y: cy, id: 2 }] });
    await touch('touchStart', 30);
    for (let d = 30; d <= 150; d += 15) { await touch('touchMove', d); await page.waitForTimeout(16); }
    await touch('touchEnd', 0); await page.waitForTimeout(600);
    const pinched = await page.evaluate(() => document.getElementById('photo').style.transform);
    if (!/scale\((?:[2-9]|1\.[5-9])/.test(pinched)) throw new Error('pinch did not zoom: ' + pinched);
    await touch('touchStart', 150);
    for (let d = 150; d >= 20; d -= 15) { await touch('touchMove', d); await page.waitForTimeout(16); }
    await touch('touchEnd', 0);
    await page.waitForSelector('#zoom-chip', { state: 'hidden', timeout: 5000 }); await page.waitForTimeout(500);
    if ((await pending()) !== start) throw new Error('pinching must never act on the photo');
    step('pinch-zoom');

    let before = await photoId();
    await swipe(0, -300); await waitFor(async () => (await pending()) === start - 1, 'swipe up keeps');
    await waitFor(async () => (await photoId()) !== before && await page.locator('#photo.ready').count(), 'next photo shown');
    step('swipe-up-keep');
    await swipe(0, 320); await waitFor(async () => (await pending()) === start - 2, 'swipe down deletes'); step('swipe-down-delete');
    await swipe(0, 40); await page.waitForTimeout(600);
    if ((await pending()) !== start - 2) throw new Error('a short drag must spring back, not act');
    step('short-drag-springs-back');
    await page.click('#keep'); await waitFor(async () => (await pending()) === start - 3, 'keep button'); step('button-keep');
    await page.click('#delete'); await waitFor(async () => (await pending()) === start - 4, 'delete button'); step('button-delete');
    await page.click('#restore'); await waitFor(async () => (await pending()) === start - 3, 'restore'); step('button-restore');
    before = await photoId();
    await swipe(-300, 0); await waitFor(async () => (await photoId()) !== before, 'swipe left next'); step('swipe-left-next');
    await page.click('#undo'); await page.waitForTimeout(800); step('undo');
    await shot('02-after-screening');

    await page.click('#mode-grade');
    await waitFor(async () => (await page.getAttribute('#app', 'data-mode')) === 'grade', 'grade mode');
    if (await page.locator('.controls-screen').isVisible()) throw new Error('grade includes screening controls');
    await page.waitForSelector('#photo.ready', { timeout: 20000 }); step('grade-mode'); await shot('03-grade');
    await page.click('.star[data-star="3"]');
    await waitFor(async () => (await page.locator('.star.on').count()) === 3 && !(await page.textContent('#name')).includes('正在写入'), 'three stars');
    step('star-3');
    const total = (await status()).match(/第 ([\d,]+) \/ ([\d,]+)/);
    await page.click('#label'); await page.waitForSelector('#sheet:not([hidden])'); await page.waitForTimeout(500); await shot('04-label-sheet');
    await page.click('.label-item[data-key="Red"]');
    await waitFor(async () => (await page.textContent('#label-text')) === '待精修', 'red label'); step('label-red');
    const index = async () => Number(((await status()).match(/第 ([\d,]+)/) || [0, '0'])[1]);
    const i0 = await index();
    await page.click('#grade-next'); await waitFor(async () => (await index()) === i0 + 1, 'next in grading');
    await swipe(300, 0); await waitFor(async () => (await index()) === i0, 'swipe right previous'); step('grade-navigation');
    await page.click('#clear'); await waitFor(async () => (await page.locator('.star.on').count()) === 0, 'clear'); step('clear-stars');
    await page.click('#mode-screen'); await waitFor(async () => (await page.getAttribute('#app', 'data-mode')) === 'screen', 'back to screening');
    step('screen-mode', { pending: await pending(), total: total && total[2] }); await shot('05-back');
  } catch (error) {
    report.errors.push(String(error && error.stack || error)); await shot('error').catch(() => {});
  }
  await browser.close();
  console.log(JSON.stringify(report));
})();
