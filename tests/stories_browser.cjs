'use strict';
// Real browser/HTTP/SQLite; interpretation is an explicit synthetic test fixture.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

test('browser: image → candidate → source comparison → PO approval → requirement → PRD and export', async () => {
  const server = spawn(process.env.PYTHON || 'python3', ['-m', 'tests.dom_server'], {stdio: ['ignore', 'pipe', 'inherit']});
  let browser;
  try {
    const origin = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('server startup timeout')), 15000);
      server.stdout.once('data', data => {clearTimeout(timer); resolve(data.toString().trim());});
      server.once('error', reject);
    });
    browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1050}, acceptDownloads: true});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const csp = []; page.on('console', m => {if (m.type() === 'error' && /Content Security Policy/.test(m.text())) csp.push(m.text());});
    const post = async (path, action) => {
      const done = page.waitForResponse(r => r.url().endsWith(path) && r.request().method() === 'POST');
      await action(); const response = await done;
      assert.equal(response.status(), 201, await response.text()); return response.json();
    };
    await page.goto(origin);
    await page.fill('#email', 'po@example.test'); await page.fill('#password', 'Planner-test-pass!');
    await page.locator('#login button').click(); await page.locator('#workspace').waitFor({state: 'visible'});
    await page.click('nav [data-page="stage2"]');
    await page.locator('#story-upload-form').waitFor();
    await page.fill('#story-upload-form [data-field="title"]', '합성 기획 이미지');
    // Small real PNG; it tests upload plumbing, not OCR quality.
    await page.locator('#story-upload-form input[type=file]').setInputFiles({name: 'synthetic.png', mimeType: 'image/png',
      buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64')});
    await post('/api/planning-assets', () => page.getByRole('button', {name: '기획 원본 저장', exact: true}).click());
    await post('/api/stories/extract', () => page.getByRole('button', {name: 'AI로 스토리 초안 해석'}).click());
    await post('/api/stories/apply-candidate', () => page.getByRole('button', {name: '새 스토리로 채택', exact: true}).click());
    await page.locator('#story-editor-form').waitFor();
    assert.equal(await page.locator('.story-source img').count(), 1);
    assert.equal(await page.locator('.story-source svg rect').count(), 1);
    await page.getByRole('button', {name: '원본 위치 보기', exact: true}).click();
    await page.locator('.story-source rect.selected').waitFor({state: 'attached'});
    await page.locator('.story-source input[type=range]').fill('150');
    const form = page.locator('#story-editor-form');
    await form.locator('[data-field="goal"]').fill('PO가 직접 정한 비교 목표');
    await form.locator('[data-field="journey"]').fill('성과 확인');
    await form.locator('[data-field="validation_plan"]').fill('광고 운영자와 조건 판단을 확인한다');
    for (const q of await form.locator('.story-question').all()) {
      await q.locator('select').selectOption('answered');
      await q.locator('textarea').last().fill('비교 판단 보류이며 광고를 자동 중단하지 않는다.');
    }
    const saved = await post('/api/stories', () => form.getByRole('button', {name: '스토리 초안 저장'}).click());
    await page.locator('.story-confirm input[type=checkbox]').first().check();
    await page.locator('.story-confirm input[type=checkbox]').nth(1).check();
    const confirmed = await post('/api/stories/confirm', () => page.getByRole('button', {name: '이 버전의 정의 확정'}).click());
    assert.equal(confirmed.validation_status, 'planned');
    await page.getByRole('button', {name: '버전 이력 보기'}).click();
    await page.locator('.story-history details').first().waitFor();
    assert.ok(await page.locator('.story-history details').count() >= 3);
    if (process.env.STORY_SCREENSHOT) await page.screenshot({path: process.env.STORY_SCREENSHOT, fullPage: true});
    await page.click('[data-story-tab="map"]');
    await page.locator('.story-map-card').waitFor();
    assert.ok((await page.locator('.story-map-column').innerText()).includes('성과 확인'));
    await page.click('[data-stage-menu="requirements"]');
    const req = page.locator('#story-requirement-form');
    await req.locator('[data-field="title"]').fill('판단 보류 상태');
    await req.locator('[data-field="condition"]').fill('표본이 부족할 때');
    await req.locator('[data-field="behavior"]').fill('판단 보류 사유를 표시한다');
    await post('/api/story-requirements', () => req.getByRole('button', {name: '기능 요구사항 저장', exact: true}).click());
    await page.click('[data-stage-menu="export"]');
    await page.getByRole('button', {name: '전달 내용 미리보기'}).click();
    await page.locator('#stage-content pre').waitFor();
    assert.ok((await page.locator('#stage-content pre').innerText()).includes('PO가 직접 정한 비교 목표'));
    const download = page.waitForEvent('download'); await page.getByRole('button', {name: 'JSON 내려받기'}).click();
    assert.equal((await download).suggestedFilename(), 'npd-story-package.json');
    await post('/api/stage2/prd', () => page.getByRole('button', {name: '검토한 확정안으로 PRD 초안 만들기'}).click());
    await page.locator('#page-prd').waitFor({state: 'visible'});
    await page.waitForFunction(() => document.querySelector('#prd-list').textContent.includes('사용자 스토리 기반 PRD'));
    // Reloaded interpretation cannot overwrite the PO-authored goal or its approval.
    await page.click('nav [data-page="stage2"]'); await page.click('[data-stage-menu="stories"]'); await page.click('[data-story-tab="import"]');
    await post('/api/stories/extract', () => page.getByRole('button', {name: 'AI로 스토리 초안 해석'}).click());
    const state = await page.evaluate(async () => (await fetch('/api/stage2')).json());
    assert.equal(state.stories[0].goal, 'PO가 직접 정한 비교 목표');
    assert.equal(state.stories[0].version, confirmed.version);
    assert.ok(saved.version < confirmed.version);
    // Adding the first product definition also requires review; valid originals
    // remain available while interpretation tied to the old context is hidden.
    await page.click('[data-stage-menu="context"]');
    await page.fill('#stage-context-form [data-field="customer"]', '대행사 운영자');
    await post('/api/stage2/context', () => page.getByRole('button', {name: '상품 기준 저장', exact: true}).click());
    await page.click('[data-stage-menu="stories"]'); await page.click('[data-story-tab="edit"]');
    await post('/api/stories', () => page.getByRole('button', {name: '현재 기준으로 검토용 초안 열기'}).click());
    await page.locator('#story-editor-form').waitFor();
    await page.locator('.story-source img').waitFor();
    assert.equal(await page.locator('#story-editor-form [data-field="goal"]').inputValue(), 'PO가 직접 정한 비교 목표');
    // No cross-project planning content remains in DOM after switch.
    await page.evaluate(async () => {
      const boot = await (await fetch('/api/bootstrap')).json();
      await fetch('/api/projects', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': boot.user.csrf}, body: JSON.stringify({title: 'Other story project'})});
    });
    await page.reload(); await page.locator('#workspace').waitFor({state: 'visible'});
    const option = await page.locator('#project-switch option').allTextContents();
    assert.ok(option.includes('Other story project'));
    await page.selectOption('#project-switch', {label: 'Other story project'});
    await page.click('nav [data-page="stage2"]');
    await page.locator('#story-upload-form').waitFor();
    assert.equal(await page.locator('.story-source img').count(), 0);
    assert.equal(await page.locator('.story-extraction').count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(csp, []);
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) {const done = once(server, 'exit'); server.kill(); await done;}
  }
});
