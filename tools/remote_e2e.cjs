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
    await page.goto(url);
    if (new URL(page.url()).hash) throw new Error('key left in the address bar');
    await page.waitForSelector('#photo.ready', { timeout: 20000 });
    const start = await pending(); step('loaded', { pending: start }); await shot('01-screen');

    await page.click('#controller-toggle');
    await page.waitForTimeout(400);
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
    await shot('00-controller'); step('controller-no-preview-and-rapid-next', { commandRoundTripMs: latencies.map(n => Math.round(n)) });
    page.off('request', countPreview);
    await page.click('#controller-toggle');
    await page.waitForSelector('#photo.ready');
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
