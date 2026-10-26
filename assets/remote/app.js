// 照片快筛 phone remote. Talks only to the desktop that served this page; every call carries the
// pairing key from the link's #fragment (kept in localStorage so a home-screen icon keeps working).
'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const app = $('app'), stage = $('stage'), card = $('card'), photo = $('photo');
  const STORE = 'photosieve-remote-key';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  let key = '';
  const fromLink = /(?:^|[#&])k=([A-Za-z0-9_-]{16,64})/.exec(location.hash);
  if (fromLink) {
    key = fromLink[1];
    try { localStorage.setItem(STORE, key); } catch (_) { /* private mode: the key lives for this tab */ }
    history.replaceState(null, '', location.pathname);   // keep the key out of the address bar
  } else {
    try { key = localStorage.getItem(STORE) || ''; } catch (_) { key = ''; }
  }
  if (!key) { $('pair').hidden = false; return; }

  let state = null, rev = 0, shownId = null, busyGesture = false, poller = null;
  let controllerOnly = false;
  try { controllerOnly = localStorage.getItem('photosieve-controller-only') !== 'false'; } catch (_) {}
  function setController(value) {
    controllerOnly = value; app.dataset.controller = String(value);
    $('controller-toggle').setAttribute('aria-pressed', String(value));
    $('controller-toggle').textContent = value ? '预览' : '按键';
    $('controller-toggle').setAttribute('aria-label', value ? '切换照片预览' : '切换大按键控制器');
    try { localStorage.setItem('photosieve-controller-only', String(value)); } catch (_) {}
    if (state) render();
  }
  $('controller-toggle').addEventListener('click', () => setController(!controllerOnly));
  app.dataset.controller = String(controllerOnly);
  $('controller-toggle').setAttribute('aria-pressed', String(controllerOnly));
  $('controller-toggle').textContent = controllerOnly ? '预览' : '按键';
  $('controller-toggle').setAttribute('aria-label', controllerOnly ? '切换照片预览' : '切换大按键控制器');
  const images = new Map();   // photo id -> {url, promise}
  const edge = Math.min(1600, Math.round(Math.max(innerWidth, innerHeight) * Math.min(3, devicePixelRatio || 1)));

  // ---- network ------------------------------------------------------------------------------
  class KeyError extends Error {}
  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, cache: 'no-store', headers: { 'X-Remote-Key': key, ...(options.headers || {}) } });
    if (response.status === 403) throw new KeyError('配对码已失效');
    if (!response.ok) throw new Error(`电脑返回 ${response.status}`);
    return response;
  }
  function forgetKey() {
    try { localStorage.removeItem(STORE); } catch (_) { /* nothing stored */ }
    $('pair').hidden = false;
    $('pair').querySelector('p').textContent = '配对码已更换或失效，请在电脑上重新扫码。';
  }
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

  async function pollLoop() {
    let backoff = 600;
    for (;;) {
      const controller = new AbortController(); poller = controller;
      try {
        const response = await api(`api/state?since=${rev}`, { signal: controller.signal });
        const next = await response.json();
        setOnline(true); backoff = 600;
        if (next.rev !== rev || next.instance !== state?.instance) apply(next);
      } catch (error) {
        if (error instanceof KeyError) { forgetKey(); return; }
        if (error.name === 'AbortError') continue;
        setOnline(false); await sleep(backoff); backoff = Math.min(6000, backoff * 1.8);
      }
    }
  }
  function repoll(force) {
    if (force) rev = 0;
    if (poller) poller.abort();
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) repoll(false); });

  // Serialize taps so HTTP scheduling cannot reverse keep/delete/next. Never retry an
  // uncertain command automatically: the desktop might already have applied it.
  let commandTail = Promise.resolve(), queued = 0;
  function act(action, value) {
    if (app.dataset.online !== 'true' || !state || state.blocked) {
      toast(state && state.blocked || '正在连接电脑，请稍候', true); return Promise.resolve(false);
    }
    if (queued >= 12) { toast('操作较快，请稍候', true); return Promise.resolve(false); }
    const mode = state.mode;
    queued++;
    const next = commandTail.then(() => {
      if (mode !== state.mode || app.dataset.online !== 'true') return false;
      return sendAction(action, value);
    }).finally(() => { queued--; });
    commandTail = next.catch(() => false);
    return next;
  }
  async function sendAction(action, value) {
    haptic(action === 'delete' ? 18 : 8);
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), 6000);
    try {
      const response = await api('api/act', { signal: timeout.signal, method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, value }) });
      const result = await response.json();
      if (!result.ok && result.reason) toast(result.reason, true);
      if (result.state && result.state.rev >= rev) apply(result.state);
      return result.ok;
    } catch (error) {
      setOnline(false);
      if (error instanceof KeyError) forgetKey(); else toast('连接中断，请在电脑确认最后一次操作', true);
      return false;
    } finally { clearTimeout(timer); }
  }

  function image(id) {
    if (images.has(id)) return images.get(id).promise;
    const entry = { url: '', promise: null };
    entry.promise = api(`api/photo?id=${id}&w=${edge}`).then((r) => r.blob()).then((blob) => { entry.url = URL.createObjectURL(blob); if (images.get(id) !== entry) URL.revokeObjectURL(entry.url); return entry.url; });
    entry.promise.catch(() => images.delete(id));
    images.set(id, entry);
    return entry.promise;
  }
  function keepImages(ids) {
    for (const [id, entry] of images) {
      if (!ids.includes(id)) { if (entry.url) URL.revokeObjectURL(entry.url); images.delete(id); }
    }
  }

  // ---- rendering ----------------------------------------------------------------------------
  function setOnline(value) {
    if (app.dataset.online === String(value)) return;
    app.dataset.online = String(value);
    if (!value) $('status').textContent = '连接中断，正在重连…';
    else if (state) render();
  }
  function apply(next) {
    if (state && next.instance === state.instance && next.rev < rev) return;
    if (state && next.instance !== state.instance) { keepImages([]); shownId = null; }
    state = next; rev = next.rev; render();
  }

  function render() {
    if (!state) return;
    if (!state.running) { $('status').textContent = '电脑已关闭手机遥控'; showEmpty('电脑已关闭手机遥控', null); return; }
    const mode = state.mode; app.dataset.mode = mode;
    $('folder').textContent = state.folder || '照片快筛';
    $('mode-screen').setAttribute('aria-pressed', String(mode === 'screen'));
    $('mode-grade').setAttribute('aria-pressed', String(mode === 'grade'));
    const part = mode === 'grade' ? state.grade : state.screen;
    if (!part) return;
    let status;
    if (state.blocked) status = state.blocked;
    else if (mode === 'screen') status = `未筛选 ${fmt(part.counts.pending)} · 保留 ${fmt(part.counts.keep)} · 已删除 ${fmt(part.counts.deleted)}`;
    else status = part.total ? `第 ${fmt(part.index + 1)} / ${fmt(part.total)} 张` : '没有照片';
    $('status').textContent = status;
    const can = part.can || {};
    for (const id of ['keep', 'delete', 'next', 'restore']) $(id).disabled = !can[id];
    $('undo').disabled = !can.undo;
    if (mode === 'grade') {
      $('previous').disabled = !can.previous; $('grade-next').disabled = !can.next; $('clear').disabled = !can.grade; $('label').disabled = !part.photo;
      const stars = part.photo ? part.photo.stars || 0 : 0;
      for (const button of document.querySelectorAll('.star')) {
        const n = Number(button.dataset.star);
        button.classList.toggle('on', n <= stars); button.setAttribute('aria-checked', String(n === stars)); button.disabled = !can.grade;
      }
      const label = part.labels.find((l) => l.key === (part.photo ? part.photo.label : '')) || part.labels[0];
      $('label-text').textContent = label ? label.text : '标签';
      $('label-swatch').style.background = label && label.color ? label.color : 'transparent';
      renderLabels(part);
    }
    if (part.photo) {
      $('empty').hidden = true; card.hidden = false;
      $('name').textContent = part.photo.name + (part.photo.pending ? ' · 正在写入' : '');
      $('camera').textContent = part.photo.camera || '';
      if (!controllerOnly) {
        show(part.photo.id);
        for (const id of part.next) image(id).catch(() => {});
        keepImages([part.photo.id, ...part.next]);
      } else { shownId = null; photo.removeAttribute('src'); keepImages([]); }
    } else if (mode === 'screen' && part.view !== 'pending') {
      showEmpty(`电脑正在查看“${part.view === 'keep' ? '保留' : '已删除'}”`, ['回到未筛选', () => act('pending')]);
    } else {
      showEmpty(part.message || '没有照片', null);
    }
  }
  const fmt = (n) => Number(n || 0).toLocaleString('zh-CN');

  function showEmpty(text, button) {
    card.hidden = true; $('empty').hidden = false; $('empty-text').textContent = text;
    $('name').textContent = ''; $('camera').textContent = ''; shownId = null;
    const action = $('empty-action');
    action.hidden = !button;
    if (button) { action.textContent = button[0]; action.onclick = button[1]; }
  }

  function show(id) {
    if (shownId === id) return;
    shownId = id; photo.classList.remove('ready');
    image(id).then((url) => {
      if (shownId !== id) return;
      photo.src = url;
      photo.decode().catch(() => {}).then(() => { if (shownId === id) photo.classList.add('ready'); });
    }).catch(() => { if (shownId === id) toast('这张照片的预览读取失败', true); });
  }

  // ---- labels sheet (bottom sheet, swipe down to dismiss) ------------------------------------
  function renderLabels(part) {
    const list = $('labels'); const current = part.photo ? part.photo.label : '';
    if (list.childElementCount !== part.labels.length) {
      list.textContent = '';
      for (const label of part.labels) {
        const item = document.createElement('button');
        item.className = 'label-item'; item.dataset.key = label.key; item.setAttribute('role', 'menuitemradio');
        const dot = document.createElement('span'); dot.className = 'swatch'; dot.style.background = label.color || 'transparent';
        if (!label.color) dot.style.borderColor = '#8C8A84';
        const text = document.createElement('span'); text.textContent = label.text;
        item.append(dot, text);
        item.addEventListener('click', () => { closeSheet(); act('label', label.key); });
        list.append(item);
      }
    }
    for (const item of list.children) item.setAttribute('aria-checked', String(item.dataset.key === current));
  }
  function openSheet() {
    if (!state || state.mode !== 'grade' || !state.grade.photo) return;
    $('sheet-backdrop').hidden = false; const sheet = $('sheet'); sheet.hidden = false; sheet.classList.remove('closing');
    sheet.style.transform = 'translateY(100%)'; requestAnimationFrame(() => { sheet.style.transform = ''; });
    haptic(6);
  }
  function closeSheet() {
    const sheet = $('sheet'); if (sheet.hidden) return;
    sheet.classList.add('closing'); sheet.style.transform = '';
    setTimeout(() => { sheet.hidden = true; $('sheet-backdrop').hidden = true; sheet.classList.remove('closing'); }, 240);
  }
  $('label').addEventListener('click', openSheet);
  $('sheet-backdrop').addEventListener('click', closeSheet);
  (() => {
    const sheet = $('sheet'); let start = null, last = 0;
    sheet.addEventListener('pointerdown', (e) => { start = { y: e.clientY, t: e.timeStamp }; last = 0; sheet.style.transition = 'none'; });
    sheet.addEventListener('pointermove', (e) => { if (!start) return; last = Math.max(0, e.clientY - start.y); sheet.style.transform = `translateY(${last}px)`; });
    const end = (e) => {
      if (!start) return; const speed = last / Math.max(1, e.timeStamp - start.t); start = null; sheet.style.transition = '';
      if (last > 90 || speed > .7) closeSheet(); else sheet.style.transform = '';
    };
    sheet.addEventListener('pointerup', end); sheet.addEventListener('pointercancel', end);
  })();

  // ---- physics: a damped spring integrated per frame ----------------------------------------
  let springFrame = 0;
  function spring(from, to, velocity, { stiffness = 380, damping = 30 } = {}, step, done) {
    cancelAnimationFrame(springFrame);
    if (reduced) { step(to); if (done) done(); return; }
    const p = { x: from.x, y: from.y }, v = { x: velocity.x * 1000, y: velocity.y * 1000 };   // px/ms -> px/s
    let last = performance.now();
    const frame = (now) => {
      let dt = Math.min(0.05, (now - last) / 1000); last = now;
      while (dt > 0) {             // fixed 1/240 s substeps keep stiff springs stable on 60 Hz phones
        const h = Math.min(dt, 1 / 240); dt -= h;
        for (const axis of ['x', 'y']) {
          const force = -stiffness * (p[axis] - to[axis]) - damping * v[axis];
          v[axis] += force * h; p[axis] += v[axis] * h;
        }
      }
      step(p);
      const settled = Math.abs(p.x - to.x) < .4 && Math.abs(p.y - to.y) < .4 && Math.hypot(v.x, v.y) < 8;
      if (settled) { step(to); if (done) done(); } else springFrame = requestAnimationFrame(frame);
    };
    springFrame = requestAnimationFrame(frame);
  }
  let pendingFling = null;
  function settleFling() { if (pendingFling) pendingFling(); }   // a new input finishes the running fling at once
  function fling(from, velocity, direction, done) {
    // Keep the finger's speed, add a push, and let the card leave the stage.
    cancelAnimationFrame(springFrame);
    let finished = false;
    const finish = () => { if (finished) return; finished = true; pendingFling = null; cancelAnimationFrame(springFrame); done(); };
    pendingFling = finish;
    const box = stage.getBoundingClientRect(); const p = { ...from };
    const push = 2.4; const v = { x: velocity.x + direction.x * push, y: velocity.y + direction.y * push };
    let last = performance.now();
    const frame = (now) => {
      const dt = Math.min(48, now - last); last = now;
      v.x += direction.x * .012 * dt; v.y += direction.y * .012 * dt;    // constant acceleration away from centre
      p.x += v.x * dt; p.y += v.y * dt; place(p, Math.max(0, 1 - Math.hypot(p.x, p.y) / (box.height * .9)));
      if (Math.abs(p.x) > box.width * 1.1 || Math.abs(p.y) > box.height * 1.05 || reduced) finish();
      else springFrame = requestAnimationFrame(frame);
    };
    springFrame = requestAnimationFrame(frame);
  }
  function place(p, opacity = 1) {
    card.style.transform = `translate3d(${p.x}px, ${p.y}px, 0) rotate(${p.x * .035}deg)`;
    card.style.opacity = String(opacity);
  }
  function hints(p) {
    const box = stage.getBoundingClientRect(); const fx = p.x / (box.width * .3), fy = p.y / (box.height * .25);
    const screen = state && state.mode === 'screen';
    const set = (id, v) => { $(id).style.opacity = String(Math.max(0, Math.min(1, v))); };
    set('hint-up', screen && Math.abs(fy) > Math.abs(fx) ? -fy : 0);
    set('hint-down', screen && Math.abs(fy) > Math.abs(fx) ? fy : 0);
    set('hint-left', Math.abs(fx) >= Math.abs(fy) ? -fx : 0);
    set('hint-right', Math.abs(fx) >= Math.abs(fy) ? fx : 0);
    $('hint-left').textContent = '下一张'; $('hint-right').textContent = screen ? '恢复' : '上一张';
  }
  function resetCard() { card.style.transition = 'none'; place({ x: 0, y: 0 }); hints({ x: 0, y: 0 }); }

  // Which action a swipe direction means in the current mode, if it is allowed right now.
  function swipeAction(dir) {
    if (!state || state.blocked) return null;
    const screen = state.mode === 'screen'; const can = (screen ? state.screen : state.grade).can || {};
    const map = screen ? { up: 'keep', down: 'delete', left: 'next', right: 'restore' } : { left: 'next', right: 'previous' };
    const action = map[dir];
    return action && can[action] ? action : null;
  }

  async function perform(action, direction) {
    const advancing = ['keep', 'delete', 'next'].includes(action);
    if (controllerOnly || !advancing || !direction) { await act(action); return; }
    busyGesture = true;
    const part = state.mode === 'screen' ? state.screen : state.grade;
    const upcoming = part.next && part.next[0];
    fling({ x: 0, y: 0 }, { x: 0, y: 0 }, direction, () => finishFling(upcoming));
    await act(action);
  }
  function finishFling(upcoming) {
    resetCard(); busyGesture = false;
    const current = state && (state.mode === 'screen' ? state.screen : state.grade);
    if (!controllerOnly && current && current.photo) { shownId = null; show(current.photo.id); }
    // The next photo rises into place on a stiff spring.
    spring({ x: 0, y: 24 }, { x: 0, y: 0 }, { x: 0, y: 0 }, { stiffness: 520, damping: 34 }, (p) => place(p, Math.min(1, 1 - p.y / 30)));
  }

  // ---- gestures on the photo: swipe, pull to refresh, long press -----------------------------
  (() => {
    let start = null, samples = [], pressTimer = 0, pulled = 0;
    stage.addEventListener('pointerdown', (e) => {
      if (e.button > 0) return;
      settleFling();
      cancelAnimationFrame(springFrame);
      start = { x: e.clientX, y: e.clientY, id: e.pointerId, edge: e.clientX < 24 };
      samples = [{ x: e.clientX, y: e.clientY, t: e.timeStamp }]; pulled = 0;
      stage.setPointerCapture(e.pointerId); card.style.transition = 'none';
      clearTimeout(pressTimer);
      pressTimer = setTimeout(() => { if (start && Math.hypot(samples.at(-1).x - start.x, samples.at(-1).y - start.y) < 10) { start = null; place({ x: 0, y: 0 }); openSheet(); } }, 520);
    });
    stage.addEventListener('pointermove', (e) => {
      if (!start || e.pointerId !== start.id) return;
      const p = { x: e.clientX - start.x, y: e.clientY - start.y };
      samples.push({ x: e.clientX, y: e.clientY, t: e.timeStamp }); if (samples.length > 6) samples.shift();
      const pullMode = !state || state.mode === 'grade' || card.hidden;
      if (pullMode && p.y > 0 && Math.abs(p.y) > Math.abs(p.x)) {   // pull to refresh
        pulled = Math.min(90, p.y * .5);
        $('pull').style.transform = `translateY(${pulled - 46}px)`; $('pull-text').textContent = pulled > 60 ? '松开刷新' : '下拉刷新';
        if (!card.hidden) place({ x: 0, y: pulled });
        return;
      }
      if (card.hidden) return;
      // Resist motion that no action would take, like a rubber band.
      const dir = Math.abs(p.x) > Math.abs(p.y) ? (p.x < 0 ? 'left' : 'right') : (p.y < 0 ? 'up' : 'down');
      const allowed = swipeAction(dir);
      const shown = allowed ? p : { x: p.x * .25, y: p.y * .25 };
      place(shown); hints(allowed ? p : { x: 0, y: 0 });
    });
    const end = (e) => {
      clearTimeout(pressTimer);
      if (!start || e.pointerId !== start.id) return;
      const s0 = samples[0], s1 = samples.at(-1); start = null;
      const dt = Math.max(1, s1.t - s0.t);
      const velocity = { x: (s1.x - s0.x) / dt, y: (s1.y - s0.y) / dt };
      const total = { x: s1.x - samples[0].x, y: s1.y - samples[0].y };
      if (pulled) {
        const refresh = pulled > 60; pulled = 0;
        spring({ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 0, y: 0 }, {}, () => {});
        $('pull').style.transform = ''; place({ x: 0, y: 0 });
        if (refresh) { haptic(6); keepImages([]); shownId = null; repoll(true); toast('已刷新'); }
        return;
      }
      if (card.hidden) return;
      const from = currentOffset();
      const box = stage.getBoundingClientRect();
      const horizontal = Math.abs(from.x) > Math.abs(from.y);
      const dir = horizontal ? (from.x < 0 ? 'left' : 'right') : (from.y < 0 ? 'up' : 'down');
      const distance = horizontal ? Math.abs(from.x) / box.width : Math.abs(from.y) / box.height;
      const speed = horizontal ? Math.abs(velocity.x) : Math.abs(velocity.y);
      const sameWay = horizontal ? Math.sign(velocity.x) === Math.sign(from.x) : Math.sign(velocity.y) === Math.sign(from.y);
      const action = swipeAction(dir);
      if (action && (distance > .28 || (speed > .55 && sameWay && distance > .06))) {
        hints({ x: 0, y: 0 });
        const direction = { left: { x: -1, y: 0 }, right: { x: 1, y: 0 }, up: { x: 0, y: -1 }, down: { x: 0, y: 1 } }[dir];
        if (['keep', 'delete', 'next'].includes(action)) {
          busyGesture = true;
          const part = state.mode === 'screen' ? state.screen : state.grade; const upcoming = part.next && part.next[0];
          fling(from, velocity, direction, () => finishFling(upcoming));
          act(action);
        } else {
          spring(from, { x: 0, y: 0 }, velocity, {}, (p) => { place(p); hints(p); });
          act(action);
        }
        return;
      }
      spring(from, { x: 0, y: 0 }, velocity, {}, (p) => { place(p); hints(p); });
      void total;
    };
    stage.addEventListener('pointerup', end); stage.addEventListener('pointercancel', end);
    function currentOffset() {
      const m = /translate3d\((-?[\d.]+)px, (-?[\d.]+)px/.exec(card.style.transform || '');
      return m ? { x: Number(m[1]), y: Number(m[2]) } : { x: 0, y: 0 };
    }
  })();

  // ---- buttons ------------------------------------------------------------------------------
  const directions = { keep: { x: 0, y: -1 }, delete: { x: 0, y: 1 }, next: { x: -1, y: 0 } };
  for (const button of document.querySelectorAll('[data-action]')) {
    button.addEventListener('click', () => {
      const action = button.dataset.action;
      settleFling();
      if (action === 'clear') { act('star', 0); return; }
      if (state && state.mode === 'screen' && directions[action] && !card.hidden) perform(action, directions[action]);
      else act(action);
    });
  }
  for (const button of document.querySelectorAll('.star')) {
    button.addEventListener('click', () => {
      button.classList.remove('pop'); void button.offsetWidth; button.classList.add('pop');
      setTimeout(() => button.classList.remove('pop'), 420);
      act('star', Number(button.dataset.star));
    });
  }
  for (const button of document.querySelectorAll('.mode')) {
    button.addEventListener('click', () => { if (state && state.mode !== button.dataset.mode) act('mode', button.dataset.mode); });
  }
  // A hardware keyboard on a tablet works like the desktop keys.
  document.addEventListener('keydown', (e) => {
    if (!state || e.repeat && e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const screen = state.mode === 'screen';
    const map = screen ? { ArrowUp: 'keep', ArrowDown: 'delete', ArrowRight: 'next', ArrowLeft: 'restore' } : { ArrowRight: 'next', ArrowLeft: 'previous' };
    if (!screen && /^[0-5]$/.test(e.key)) { act('star', Number(e.key)); e.preventDefault(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { act('undo'); e.preventDefault(); return; }
    const action = map[e.key]; if (!action) return;
    e.preventDefault(); settleFling();
    if (screen && directions[action]) perform(action, directions[action]); else act(action);
  });

  // ---- feedback -----------------------------------------------------------------------------
  let toastTimer = 0;
  function toast(text, bad = false) {
    const el = $('toast'); el.textContent = text; el.classList.toggle('bad', bad); el.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), bad ? 2600 : 1400);
  }
  function haptic(ms) { if (navigator.vibrate && !reduced) navigator.vibrate(ms); }
  for (const art of document.querySelectorAll('img.art')) art.addEventListener('error', () => { art.hidden = true; });

  pollLoop();
})();
