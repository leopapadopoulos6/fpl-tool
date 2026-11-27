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
      // --- option line-up popup: every row of the options table builds its own team
      await page.click('[data-squad-pick=mine]');
      const options = await page.evaluate(() => window.__dash.data.decision.map((r) => r.option));
      for (const opt of options) {
        const btn = `#transfers tbody [data-option-xi="${opt}"]`;
        await page.click(btn);
        await page.waitForTimeout(150);
        const x = await page.evaluate((opt) => {
          const dlg = document.getElementById('option-xi');
          const rows = window.__dash.data.option_lineups.filter((r) => r.option === opt);
          const xi = rows.filter((r) => !r.role.startsWith('Bench'));
          const box = dlg.getBoundingClientRect();
          const pitchRows = [...dlg.querySelectorAll('.hb-pitch .hb-prow')].map((r) => r.children.length);
          const want = ['GKP', 'DEF', 'MID', 'FWD'].map((p) => xi.filter((r) => r.pos === p).length);
          const benchNames = [...dlg.querySelectorAll('.hb-benchrow .hb-pc-name span:last-child')].map((n) => n.textContent);
          const cap = [...dlg.querySelectorAll('.hb-pitch .hb-pc-badge')].find((b) => b.textContent === 'C');
          return { open: dlg.open, modal: dlg.matches(':modal'), pitchRows, want,
                   bench: benchNames, benchWant: rows.filter((r) => r.role.startsWith('Bench')).map((r) => r.player),
                   capName: cap ? cap.parentElement.querySelector('.hb-pc-name span:last-child').textContent : null,
                   capWant: (rows.find((r) => r.armband === 'Captain') || {}).player,
                   newTags: dlg.querySelectorAll('.hb-pc-tag').length, newWant: rows.filter((r) => r.is_new === 'Yes').length,
                   title: document.querySelector('[data-draw=xi-title]').textContent,
                   range: document.querySelector('[data-draw=xi-range]').textContent,
                   rangeWant: (() => { const d = window.__dash.data.decision.find((r) => r.option === opt); return `${Math.round(d.p10)}–${Math.round(d.p90)}`; })(),
                   beat: document.querySelector('[data-draw=xi-beat]').textContent,
                   pairs: document.querySelector('[data-draw=xi-pairs]').textContent,
                   pairsWant: window.__dash.data.decision.find((r) => r.option === opt).pairings,
                   fits: box.left >= -1 && box.right <= innerWidth + 1 && box.top >= -1 && box.bottom <= innerHeight + 1,
                   inner: dlg.scrollWidth <= dlg.clientWidth + 1, side: document.documentElement.scrollWidth <= innerWidth + 1 };
        }, opt);
        assert.ok(x.open && x.modal, `${tag}: option ${opt} popup opens as a modal`);
        assert.deepStrictEqual(x.pitchRows, x.want, `${tag}: option ${opt} formation`);
        assert.deepStrictEqual(x.bench, x.benchWant, `${tag}: option ${opt} bench order`);
        assert.strictEqual(x.capName, x.capWant, `${tag}: option ${opt} captain badge`);
        assert.ok(x.newTags >= x.newWant, `${tag}: option ${opt} new players tagged`);
        assert.ok(x.fits && x.inner && x.side, `${tag}: option ${opt} popup fits ${JSON.stringify(x)}`);
        assert.strictEqual(x.range, x.rangeWant, `${tag}: option ${opt} points range`);
        assert.ok(/^\d+%$/.test(x.beat), `${tag}: option ${opt} chance of beating the top 10k`);
        assert.ok(x.pairs.startsWith(x.pairsWant), `${tag}: option ${opt} same-match pairings`);
        if (SHOTS && theme === 'light') {
          const rec = await page.evaluate(() => window.__dash.data.decision.find((r) => r.recommended === 'Yes').option);
          if (opt === rec) await page.screenshot({ path: `${SHOTS}/${label}-option-xi.png` });
        }
        await page.keyboard.press('Escape');
        await page.waitForTimeout(100);
        const after = await page.evaluate((btn) => ({ open: document.getElementById('option-xi').open,
          focus: document.activeElement === document.querySelector(btn) }), btn);
        assert.ok(!after.open && after.focus, `${tag}: option ${opt} closes and returns focus`);
      }
      const risk = await page.evaluate(() => {
        const note = document.querySelector('[data-risk-note]');
        const t = document.querySelector('#transfers table[data-source=decision]');
        const cols = [...t.querySelectorAll('thead th[data-risk-col]')];
        const first = t.querySelector('tbody tr');
        return { note: !note.hidden && note.textContent.includes('Near-tie'),
                 cols: cols.length === 2 && cols.every((c) => !c.hidden),
                 colsShown: cols.map((c) => getComputedStyle(c).display !== 'none'),
                 cells: first.cells.length, heads: t.querySelectorAll('thead th').length };
      });
      assert.ok(risk.note && risk.cols, `${tag}: near-tie note and range columns ${JSON.stringify(risk)}`);
      assert.strictEqual(risk.cells, risk.heads, `${tag}: options table cells line up with headers`);
      // the lede button opens the recommended option, which is the Squad section's Engine's pick
      await page.click('#transfers [data-option-xi="rec"]');
      await page.waitForTimeout(150);
      const rec = await page.evaluate(() => {
        const ids = [...document.querySelectorAll('#option-xi .hb-pc-name span:last-child')].map((n) => n.textContent);
        return { ids, want: window.__dash.data.lineup.map((r) => r.player) };
      });
      assert.deepStrictEqual(rec.ids, rec.want, `${tag}: recommended popup equals the line-up dataset`);
      // backdrop tap closes
      await page.mouse.click(2, 2);
      await page.waitForTimeout(100);
      assert.ok(!(await page.evaluate(() => document.getElementById('option-xi').open)), `${tag}: backdrop closes the popup`);

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
      for (const id of ['decision', 'captaincy', 'lineup', 'option_lineups', 'plan', 'players', 'fixtures_strength']) window.__dash.setData(id, null);
      const bare = window.__dash.data.squad.map((r) => { const o = { ...r }; for (const k of ['xpts_next', 'p_start', 'eo_top10k', 'next_diff', 'next5_diff', 'rec_role', 'rec_swap']) delete o[k]; return o; });
      window.__dash.setData('squad', bare);
    });
    await page.waitForTimeout(150);
    const r = await page.evaluate(() => ({ cards: document.querySelectorAll('.hb-pitch .hb-pcard').length,
      bench: document.querySelectorAll('.hb-benchrow .hb-pcard').length, diff: document.getElementById('engine-diff').hidden,
      toggle: document.querySelector('[data-squad-pick]').closest('[data-engine-only]').hidden,
      navTransfers: document.querySelector('.hb-nav a[href="#transfers"]').hidden,
      xi: [...document.querySelectorAll('[data-xi-only]')].every((e) => e.hidden), errors: window.__dash.errors }));
    assert.deepStrictEqual(r, { cards: 11, bench: 4, diff: true, toggle: true, navTransfers: true, xi: true, errors: [] }, 'gate failed state');
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

  // --- gameweek review block: latest gameweek, decisions, players sorted by the miss
  for (const [label, opts] of [['iphone13', { ...devices['iPhone 13'] }], ['phone320', { viewport: { width: 320, height: 640 } }],
    ['desktop', { viewport: { width: 1280, height: 900 } }]]) {
    const { ctx, page } = await open(browser, { ...opts, deviceScaleFactor: SHOTS ? 2 : 1 });
    const has = await page.evaluate(() => (window.__dash.data.review_gw || []).length > 0);
    if (!has) { await ctx.close(); continue; }
    await page.click('.hb-nav a[href="#gw-review"]');
    await settle(page);
    const r = await page.evaluate(() => {
      const gws = window.__dash.data.review_gw.map((x) => x.gw);
      const latest = Math.max(...gws);
      const dec = window.__dash.data.review_decisions.filter((d) => d.gw === latest);
      const rows = [...document.querySelectorAll('[data-draw=rv-decisions] tr')].length;
      const misses = [...document.querySelectorAll('[data-draw=rv-players] tr')].map((tr) => {
        const c = tr.cells; return Math.abs(Number(c[3].textContent) - Number(c[2].textContent));
      });
      const sorted = misses.every((m, i) => i === 0 || misses[i - 1] + 0.11 >= m);
      return { shown: !document.getElementById('gw-review').hidden, gw: Number(document.querySelector('[data-draw=rv-gw]').textContent),
               latest, rows, want: dec.length, sorted, players: misses.length,
               you: document.querySelector('[data-draw=rv-you]').textContent,
               side: document.documentElement.scrollWidth <= innerWidth + 1, errors: window.__dash.errors };
    });
    assert.ok(r.shown && r.gw === r.latest && r.rows === r.want && r.players > 0 && r.sorted && r.side && r.you !== '—'
      && r.errors.length === 0, `${label}: gameweek review ${JSON.stringify(r)}`);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${label}-review.png` });
    const gws = await page.evaluate(() => [...new Set(window.__dash.data.review_gw.map((x) => x.gw))].sort((a, b) => a - b));
    if (gws.length > 1) {
      await page.selectOption('.hb-review-gw', String(gws[0]));
      await page.waitForTimeout(150);
      const g = await page.evaluate(() => Number(document.querySelector('[data-draw=rv-gw]').textContent));
      assert.strictEqual(g, gws[0], `${label}: review gameweek picker`);
    }
    await ctx.close();
  }
  {
    const { ctx, page } = await open(browser, { ...devices['iPhone 13'] });
    await page.evaluate(() => { for (const id of ['review_gw', 'review_decisions', 'review_players']) window.__dash.setData(id, null); });
    await page.waitForTimeout(100);
    const r = await page.evaluate(() => ({ block: document.getElementById('gw-review').hidden,
      pill: document.querySelector('.hb-nav a[href="#gw-review"]').hidden, errors: window.__dash.errors }));
    assert.deepStrictEqual(r, { block: true, pill: true, errors: [] }, 'no review: block and pill hidden');
    await ctx.close();
  }

  await browser.close();
  console.log('dashboard page: all checks passed');
})().catch((e) => { console.error(e); process.exit(1); });
