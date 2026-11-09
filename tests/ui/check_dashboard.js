// Browser checks for the dashboard page (nav, squad formation/list, engine diff, detail).
// Run by tests/ui/test_dashboard_page.py:  node check_dashboard.js <preview.html> <shots dir>
const { chromium, devices } = require('playwright');
const assert = require('assert');

const [, , PAGE, SHOTS] = process.argv;
const url = (q = '', hash = '') => 'file://' + PAGE + q + hash;

async function open(browser, opts, q = '', hash = '') {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await page.goto(url(q, hash));
  await page.waitForTimeout(300);
  return { ctx, page, errs };
}

// wait until smooth scrolling has settled
async function settle(page) {
  let last = -1;
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(120);
    const y = await page.evaluate(() => document.scrollingElement.scrollTop);
    if (y === last) return;
    last = y;
  }
}
const noSideScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
const dashErrors = (page) => page.evaluate(() => window.__dash.errors);

async function checkLayout(page, label) {
  assert.ok(await noSideScroll(page), `${label}: horizontal page scroll`);
  // every card fits its column and long names truncate instead of overflowing
  // (C/V and flag badges sit on the card corner by design; they must stay inside the pitch)
  const bad = await page.evaluate(() => [...document.querySelectorAll('.hb-pcard')].filter((c) => {
    const r = c.getBoundingClientRect(), p = c.parentElement.parentElement.getBoundingClientRect();
    const inFlow = [...c.children].filter((k) => getComputedStyle(k).position !== 'absolute');
    const spill = inFlow.some((k) => { const b = k.getBoundingClientRect(); return b.left < r.left - 1 || b.right > r.right + 1; });
    const badges = [...c.children].filter((k) => getComputedStyle(k).position === 'absolute')
      .some((k) => { const b = k.getBoundingClientRect(); return b.left < p.left - 1 || b.right > p.right + 1; });
    return r.left < p.left - 1 || r.right > p.right + 1 || spill || badges;
  }).map((c) => c.dataset.player));
  assert.deepStrictEqual(bad, [], `${label}: cards overflow ${bad}`);
}

(async () => {
  const browser = await chromium.launch();
  const sizes = [
    ['iphone13', { ...devices['iPhone 13'] }],
    ['phone320', { viewport: { width: 320, height: 640 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true }],
    ['pane400', { viewport: { width: 400, height: 800 }, deviceScaleFactor: 1 }],
    ['desktop', { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 }],
  ];
  for (const [label, opts] of sizes) {
    for (const theme of ['light', 'dark']) {
      const { ctx, page, errs } = await open(browser, { ...opts, deviceScaleFactor: SHOTS ? 2 : 1 }, theme === 'dark' ? '?theme=dark' : '');
      const tag = `${label}/${theme}`;
      assert.deepStrictEqual(await dashErrors(page), [], `${tag}: script errors`);
      assert.deepStrictEqual(errs, [], `${tag}: page errors`);

      // --- formation matches the starting XI and the bench is in order
      const shape = await page.evaluate(() => {
        const sq = window.__dash.data.squad;
        const xi = sq.filter((r) => r.role === 'Starting');
        const count = (p) => xi.filter((r) => r.pos === p).length;
        const rows = [...document.querySelectorAll('.hb-pitch .hb-prow')].map((r) => r.children.length);
        const bench = [...document.querySelectorAll('.hb-benchrow .hb-pcard')].map((c) => c.dataset.player);
        const benchWant = sq.filter((r) => r.role.startsWith('Bench')).sort((a, b) => a.slot - b.slot).map((r) => String(r.element));
        return { rows, want: ['GKP', 'DEF', 'MID', 'FWD'].map(count), bench, benchWant,
                 label: document.querySelector('[data-draw=formation-shape]').textContent };
      });
      assert.deepStrictEqual(shape.rows, shape.want, `${tag}: pitch rows`);
      assert.deepStrictEqual(shape.bench, shape.benchWant, `${tag}: bench order`);
      assert.strictEqual(shape.label, shape.want.slice(1).join('-'));
      const badges = await page.evaluate(() => [...document.querySelectorAll('.hb-pitch .hb-pc-badge')].map((b) => b.textContent).sort().join(''));
      assert.strictEqual(badges, 'CV', `${tag}: captain and vice badges`);
      await checkLayout(page, tag);

      // --- sticky nav: each pill lands its section just below the bar, and becomes active
      for (const id of ['chips', 'squad', 'transfers', 'ft-history', 'model-health']) {
        await page.click(`.hb-nav a[href="#${id}"]`);
        await settle(page);
        await page.waitForTimeout(200);
        const r = await page.evaluate((id) => {
          const nav = document.querySelector('.hb-nav').getBoundingClientRect();
          const t = document.getElementById(id).getBoundingClientRect();
          const se = document.scrollingElement;
          return { navTop: nav.top, navBottom: nav.bottom, top: t.top, atEnd: se.scrollTop + se.clientHeight >= se.scrollHeight - 2,
                   active: document.querySelector('.hb-nav a.is-active')?.getAttribute('href') };
        }, id);
        assert.ok(r.navTop >= -1 && r.navTop <= 8, `${tag}: #${id} nav not at top ${JSON.stringify(r)}`);
        assert.ok(r.top >= r.navBottom - 1, `${tag}: #${id} hidden under the nav`);
        assert.ok(r.top <= r.navBottom + 40 || r.atEnd, `${tag}: #${id} not scrolled to (${r.top})`);
        assert.strictEqual(r.active, '#' + id, `${tag}: active pill after tapping #${id}`);
      }
      const links = await page.evaluate(() => window.__dash.log.filter((l) => l[0] === 'setLink').map((l) => l[1]));
      assert.ok(links.includes('squad') && links.includes('chips'), `${tag}: setLink`);
      // active highlight follows a manual scroll
      await page.evaluate(() => document.getElementById('players').scrollIntoView({ block: 'start' }));
      await page.waitForTimeout(300);
      assert.strictEqual(await page.evaluate(() => document.querySelector('.hb-nav a.is-active').getAttribute('href')), '#players', `${tag}: scroll-spy`);

      if (theme === 'light' && SHOTS) {
        await page.click('.hb-nav a[href="#squad"]');
        await settle(page);
        await page.screenshot({ path: `${SHOTS}/${label}-formation.png`, fullPage: false });
      }

      // --- detail panel: same values from a pitch card and from a list row
      const el = await page.evaluate(() => window.__dash.data.squad.find((r) => r.role === 'Starting' && r.armband === 'Captain').element);
      await page.click(`.hb-pitch [data-player="${el}"]`);
      await page.waitForTimeout(200);
      const fromCard = await page.evaluate(() => ({ hidden: document.getElementById('player-detail').hidden,
        name: document.querySelector('[data-draw=detail-name]').textContent,
        vals: [...document.querySelectorAll('[data-draw=detail]')].map((d) => d.textContent) }));
      assert.ok(!fromCard.hidden, `${tag}: detail opens from card`);
      assert.ok(!fromCard.vals.includes('—') || fromCard.vals.filter((v) => v === '—').length <= 1, `${tag}: detail mostly filled ${fromCard.vals}`);
      if (theme === 'light' && SHOTS) await page.screenshot({ path: `${SHOTS}/${label}-detail.png` });
      await page.click('[data-detail-close]');
      assert.ok(await page.evaluate(() => document.getElementById('player-detail').hidden), `${tag}: detail closes`);
      await page.click('[data-squad-view=list]');
      await page.waitForTimeout(150);
      const listState = await page.evaluate(() => ({ list: !document.getElementById('squad-pane-list').hidden,
        form: !document.getElementById('squad-pane-formation').hidden, params: window.__dash.params.squad_view }));
      assert.deepStrictEqual(listState, { list: true, form: false, params: 'list' }, `${tag}: list toggle + param`);
      await page.click(`#squad-pane-list tr[data-key="${el}"]`);
      await page.waitForTimeout(200);
      const fromRow = await page.evaluate(() => ({ name: document.querySelector('[data-draw=detail-name]').textContent,
        vals: [...document.querySelectorAll('[data-draw=detail]')].map((d) => d.textContent) }));
      assert.deepStrictEqual(fromRow, { name: fromCard.name, vals: fromCard.vals }, `${tag}: card and row detail differ`);
      await page.keyboard.press('Escape');
      if (theme === 'light' && SHOTS) {
        await page.locator('#squad').scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${SHOTS}/${label}-list.png` });
      }
      assert.ok(await noSideScroll(page), `${tag}: list view side scroll`);
      await page.click('[data-squad-view=formation]');

      // --- engine's pick: diff listed, pitch redrawn from the line-up, yours one tap away
      const diff = await page.evaluate(() => ({ shown: !document.getElementById('engine-diff').hidden,
        n: document.querySelectorAll('.hb-diff-list li').length, dashed: document.querySelectorAll('.hb-pcard.is-diff').length }));
      assert.ok(diff.shown && diff.n > 0 && diff.dashed > 0, `${tag}: engine diff ${JSON.stringify(diff)}`);
      await page.click('[data-squad-pick=engine]');
      await page.waitForTimeout(150);
      const eng = await page.evaluate(() => {
        const lu = window.__dash.data.lineup;
        const onPitch = [...document.querySelectorAll('.hb-pitch [data-player], .hb-benchrow [data-player]')].map((c) => c.dataset.player);
        return { same: JSON.stringify(onPitch.sort()) === JSON.stringify(lu.map((r) => String(r.element)).sort()),
                 cap: document.querySelector('.hb-pitch .hb-pc-badge')?.closest('[data-player]').dataset.player,
                 wantCap: String(lu.find((r) => r.armband === 'Captain').element) };
      });
      assert.ok(eng.same, `${tag}: engine pitch shows the engine's 15`);
      await checkLayout(page, tag + ' engine');
      if (theme === 'light' && SHOTS) {
        await page.locator('#squad').scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${SHOTS}/${label}-engine.png` });
      }
      if (theme === 'dark' && SHOTS && label === 'iphone13') {
        await page.click('[data-squad-pick=mine]');
        await page.click('.hb-nav a[href="#squad"]');
        await settle(page);
        await page.screenshot({ path: `${SHOTS}/${label}-dark.png` });
      }
      await ctx.close();
    }
  }

  // --- deep links: #chips lands on the chips plan; #squad-list opens the list view
  for (const [hash, id] of [['#chips', 'chips'], ['#chip-gain', 'chips'], ['#squad-list', 'squad']]) {
    const { ctx, page } = await open(browser, { ...devices['iPhone 13'] }, '', hash);
    await page.waitForTimeout(500);
    const r = await page.evaluate((id) => ({ top: document.getElementById(id).getBoundingClientRect().top,
      nav: document.querySelector('.hb-nav').getBoundingClientRect().bottom, list: !document.getElementById('squad-pane-list').hidden }), id);
    assert.ok(r.top >= r.nav - 1 && r.top < r.nav + 40, `${hash}: landed at ${r.top}`);
    if (hash === '#squad-list') assert.ok(r.list, '#squad-list opens the list view');
    await ctx.close();
  }

  // --- stored choice: the list view comes back from the page param
  {
    const { ctx, page } = await open(browser, { ...devices['iPhone 13'] }, '?view=list');
    await page.evaluate(() => { window.__dash.params.squad_view = 'list'; window.__dash.tick(); });
    assert.ok(await page.evaluate(() => !document.getElementById('squad-pane-list').hidden), 'param restores list view');
    await ctx.close();
  }

  // --- model datasets withheld (gate failed): pitch still renders from state, engine bits hidden
  {
    const { ctx, page } = await open(browser, { ...devices['iPhone 13'] });
    await page.evaluate(() => {
      for (const id of ['decision', 'captaincy', 'lineup', 'plan', 'players', 'fixtures_strength']) window.__dash.setData(id, null);
      const bare = window.__dash.data.squad.map((r) => { const o = { ...r }; for (const k of ['xpts_next', 'p_start', 'eo_top10k', 'next_diff', 'next5_diff', 'rec_role', 'rec_swap']) delete o[k]; return o; });
      window.__dash.setData('squad', bare);
    });
    await page.waitForTimeout(150);
    const r = await page.evaluate(() => ({ cards: document.querySelectorAll('.hb-pitch .hb-pcard').length,
      bench: document.querySelectorAll('.hb-benchrow .hb-pcard').length, diff: document.getElementById('engine-diff').hidden,
      toggle: document.querySelector('[data-squad-pick]').closest('[data-engine-only]').hidden,
      navTransfers: document.querySelector('.hb-nav a[href="#transfers"]').hidden, errors: window.__dash.errors }));
    assert.deepStrictEqual(r, { cards: 11, bench: 4, diff: true, toggle: true, navTransfers: true, errors: [] }, 'gate failed state');
    await ctx.close();
  }

  // --- a very long name truncates with an ellipsis at 320px
  {
    const { ctx, page } = await open(browser, { viewport: { width: 320, height: 640 } });
    await page.evaluate(() => {
      const sq = window.__dash.data.squad.map((r) => (r.armband === 'Captain' ? { ...r, player: 'Alexander-Arnold-Featherstonehaugh' } : r));
      window.__dash.setData('squad', sq);
    });
    await page.waitForTimeout(150);
    await checkLayout(page, 'long name');
    const ell = await page.evaluate(() => {
      const n = [...document.querySelectorAll('.hb-pc-name span:last-child')].find((s) => s.textContent.startsWith('Alexander'));
      return n.scrollWidth > n.clientWidth && getComputedStyle(n).textOverflow === 'ellipsis';
    });
    assert.ok(ell, 'long name is cut with an ellipsis');
    await ctx.close();
  }

  await browser.close();
  console.log('dashboard page: all checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
