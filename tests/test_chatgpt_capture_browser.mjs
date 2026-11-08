import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import axe from 'axe-core';

const root = fileURLToPath(new URL('../', import.meta.url));
const fixture = JSON.parse(await fs.readFile(new URL('./fixtures/capture/conversation.json', import.meta.url)));
const liveShape = JSON.parse(await fs.readFile(new URL('./fixtures/capture/live-shape.json', import.meta.url)));
const id = fixture.conversation_id;
const origin = 'https://chatgpt.com';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label) {
  for (let n = 0; n < 150; n++) {
    const result = await check();
    if (result) return result;
    await pause(30);
  }
  assert.fail(`Timed out: ${label}`);
}

// Real extension targets are absent from Playwright's pages(). Talk to that
// target over CDP; do not replace chrome.tabs, scripting, permissions or downloads.
async function attach(browser, targetId) {
  const { sessionId } = await browser.send('Target.attachToTarget', { targetId, flatten: false });
  let sequence = 0;
  function command(method, params = {}) {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const finish = (error, result) => {
        clearTimeout(timer);
        browser.off('Target.receivedMessageFromTarget', receive);
        if (error) reject(error); else resolve(result);
      };
      const receive = event => {
        if (event.sessionId !== sessionId) return;
        const message = JSON.parse(event.message);
        if (message.id === id) finish(message.error ? Error(message.error.message) : null, message.result);
      };
      const timer = setTimeout(() => finish(Error(`Popup RPC timed out: ${method}`)), 10000);
      browser.on('Target.receivedMessageFromTarget', receive);
      browser.send('Target.sendMessageToTarget', {
        sessionId, message: JSON.stringify({ id, method, params })
      }).catch(error => finish(error));
    });
  }
  async function evaluate(expression) {
    const value = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    assert.equal(value.exceptionDetails, undefined, JSON.stringify(value.exceptionDetails));
    return value.result.value;
  }
  return { command, evaluate };
}

async function qualify(scenario) {
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'press-capture-'));
  const context = await chromium.launchPersistentContext(profile, {
    channel: 'chromium', headless: true, acceptDownloads: true,
    ignoreDefaultArgs: ['--disable-extensions'],
    args: ['--enable-unsafe-extension-debugging', '--disable-gpu']
  });
  const requests = [], unexpected = [], downloads = [];
  let releaseNative, nativeRequested = false;
  const held = new Promise(resolve => { releaseNative = resolve; });
  try {
    await context.route(/^https?:/, async route => {
      const request = route.request(), url = new URL(request.url());
      if (request.method() !== 'GET' || ![origin, 'https://example.org'].includes(url.origin)) {
        unexpected.push(request.url()); await route.abort(); return;
      }
      requests.push(url.pathname + url.search);
      if (url.pathname.startsWith('/c/') || url.origin === 'https://example.org') {
        await route.fulfill({ contentType: 'text/html',
          headers: { 'Content-Security-Policy': "default-src 'none'; connect-src 'self'; base-uri 'none'" },
          body: '<!doctype html><html lang="en"><title>Fixture</title><main>Synthetic conversation</main></html>' });
        return;
      }
      let body, status = 200;
      if (url.pathname === '/api/auth/session') {
        body = scenario === 'cookie-only' ? {} : { accessToken: 'synthetic-only-token', account: { id: 'fixture-account' } };
      } else if (url.pathname === '/backend-api/conversation/' + id) {
        nativeRequested = true;
        const headers = await request.allHeaders();
        assert.ok(headers.cookie?.includes('fixture-session=synthetic-cookie'));
        assert.equal(headers.authorization, scenario === 'cookie-only' ? undefined : 'Bearer synthetic-only-token');
        if (['navigate', 'popup-close'].includes(scenario)) await held;
        if (scenario === 'redirect') {
          await route.fulfill({ status: 302, headers: { location: 'https://example.org/credential-trap' } }); return;
        }
        if (scenario === 'challenge') {
          await route.fulfill({ contentType: 'text/html', body: '<html>challenge</html>' }); return;
        }
        if (scenario === 'unauthorized') status = 401;
        body = scenario === 'live-shape' ? liveShape.native : fixture;
      } else if (url.pathname === '/backend-api/conversations/' + id ||
                 url.pathname === '/backend-api/conversations/' + id + '/messages') {
        const older = url.pathname.endsWith('/messages');
        body = { conversation_id: id, current_node: scenario === 'changed-branch' ? 'a3' : 'a2',
          messages: older ? [fixture.mapping.u.message] : [fixture.mapping.a2.message],
          page_info: { has_previous_page: !older, start_cursor: older ? 'cursor-two' : 'cursor-one' } };
        if (scenario === 'live-shape') body = liveShape.selected_branch_pages[0];
        if (scenario === 'omission' && older) body.messages.push({ id: 'absent-from-graph' });
        if (scenario === 'audit-unavailable') status = 404;
        if (scenario === 'rate-limit') status = 429;
        if (scenario === 'stalled' && older) {
          body.messages = [fixture.mapping.a2.message]; body.page_info.has_previous_page = true;
        }
      } else { unexpected.push(request.url()); await route.abort(); return; }
      try { await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }); }
      catch (error) { if (scenario !== 'navigate') throw error; }
    });
    await context.addCookies([{ name: 'fixture-session', value: 'synthetic-cookie', url: origin,
      httpOnly: true, secure: true, sameSite: 'Lax' }]);
    const browser = await context.browser().newBrowserCDPSession();
    const loaded = await browser.send('Extensions.loadUnpacked', { path: path.join(root, 'tools/chatgpt-capture') });
    const page = context.pages()[0];
    await page.goto(origin + '/c/' + (scenario === 'local-url' ? 'local-chatgpt%3A' + id : id));
    page.on('download', download => downloads.push(download));
    if (scenario === 'hostile-page') await page.evaluate(() => {
      globalThis.fetch = () => { throw Error('Main-world fetch must not see credentials'); };
      HTMLAnchorElement.prototype.click = () => { throw Error('Main-world click must not handle ZIP'); };
    });
    if (scenario === 'multiple-tabs') {
      const other = await context.newPage();
      await other.goto(origin + '/c/aaaaaaaaaaaaaaaa');
      other.on('download', () => assert.fail('Wrong tab downloaded'));
    }
    await page.bringToFront();
    const tabs = (await browser.send('Target.getTargets', { filter: [{ type: 'tab' }] })).targetInfos;
    const tab = tabs.find(tab => tab.url === page.url());
    assert.ok(tab);
    let popupTarget;
    if (scenario === 'revoked-permission') {
      browser.send('Extensions.triggerAction', { id: loaded.id, targetId: tab.targetId }).catch(() => {});
      const grantedTarget = await until(async () => (await browser.send('Target.getTargets')).targetInfos
        .find(t => t.url === `chrome-extension://${loaded.id}/popup.html`), 'initial permission grant');
      const granted = await attach(browser, grantedTarget.targetId);
      await until(() => granted.evaluate('document.readyState === "complete"'), 'initial popup loaded');
      assert.equal(await granted.evaluate(`(async()=>{
        const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
        return (await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>true}))[0].result;
      })()`), true, 'the original action grants actual access');
      await browser.send('Target.closeTarget', { targetId: grantedTarget.targetId });
      await page.goto('https://example.org/');
      await page.goto(origin + '/c/' + id);
    }
    if (['no-permission', 'revoked-permission'].includes(scenario)) {
      // Opening the popup as a tab does not grant activeTab to a website.
      const popupPage = await context.newPage();
      await popupPage.goto(`chrome-extension://${loaded.id}/popup.html`);
      popupTarget = (await browser.send('Target.getTargets')).targetInfos.find(t => t.url === popupPage.url());
      await page.bringToFront();
    } else {
      browser.send('Extensions.triggerAction', { id: loaded.id, targetId: tab.targetId }).catch(() => {});
      popupTarget = await until(async () => (await browser.send('Target.getTargets')).targetInfos
        .find(t => t.url === `chrome-extension://${loaded.id}/popup.html`), 'real extension popup');
    }
    const popup = await attach(browser, popupTarget.targetId);
    await until(() => popup.evaluate('document.readyState === "complete" && Boolean(document.querySelector("#capture"))'), 'popup module loaded');
    if (['no-permission', 'revoked-permission'].includes(scenario)) {
      const denied = await popup.evaluate(`(async()=>{
        const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
        try { await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>true}); return false; }
        catch { return true; }
      })()`);
      assert.equal(denied, true, 'Chrome itself rejects injection without the action grant');
    }
    assert.equal(requests.filter(x => x.startsWith('/api/') || x.startsWith('/backend-api/')).length, 0,
      'no account API access before explicit Capture click');
    if (scenario === 'happy') {
      await popup.evaluate(axe.source);
      const violations = await popup.evaluate('(async()=> (await axe.run(document)).violations.map(v=>v.id))()');
      assert.deepEqual(violations, []);
      await popup.command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
      await popup.command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
      assert.equal(await popup.evaluate('document.activeElement.id'), 'capture');
      await popup.command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' });
      await popup.command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
    } else {
      const box = await popup.evaluate('(()=>{const b=document.querySelector("#capture").getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2}})()');
      await popup.command('Input.dispatchMouseEvent', { type: 'mousePressed', ...box, button: 'left', clickCount: 1 });
      await popup.command('Input.dispatchMouseEvent', { type: 'mouseReleased', ...box, button: 'left', clickCount: 1 });
    }
    if (scenario === 'popup-close') {
      await until(() => nativeRequested, 'capture started');
      await browser.send('Target.closeTarget', { targetId: popupTarget.targetId });
      releaseNative();
      await until(() => downloads.length === 1, 'capture continues after popup closes');
    } else if (scenario === 'navigate') {
      await until(() => nativeRequested, 'capture started');
      // SPA navigation leaves the injected world alive, so its own cancellation
      // must stop it. Full navigations additionally destroy that world.
      await page.evaluate(() => history.pushState({}, '', '/c/aaaaaaaaaaaaaaaa'));
      await pause(250); releaseNative();
      await until(() => popup.evaluate('document.querySelector("#status").dataset.kind === "error"'), 'navigation cancellation');
    } else {
      try {
        await until(() => popup.evaluate('!document.querySelector("#capture").disabled && Boolean(document.querySelector("#status").textContent)'), 'capture completed');
      } catch (error) {
        assert.fail(JSON.stringify({ error: error.message, requests, downloads: downloads.length,
          status: await popup.evaluate('document.querySelector("#status").textContent') }));
      }
      const status = await popup.evaluate('document.querySelector("#status").textContent');
      if (['unauthorized', 'changed-branch', 'challenge', 'redirect', 'no-permission', 'revoked-permission', 'local-url'].includes(scenario)) {
        assert.equal(await popup.evaluate('document.querySelector("#status").dataset.kind'), 'error', status);
        if (scenario === 'local-url') assert.match(status, /local ChatGPT URL.*Reopen the saved conversation/);
      } else if (['audit-unavailable', 'stalled', 'rate-limit'].includes(scenario)) assert.match(status, /audit unavailable/);
      else if (scenario === 'omission') assert.match(status, /1 selected-branch messages absent/);
      else if (scenario === 'live-shape') assert.match(status, /3 selected-branch messages absent/);
      else assert.match(status, /no extra IDs/);
    }
    const mustFail = ['unauthorized', 'changed-branch', 'challenge', 'redirect', 'no-permission', 'revoked-permission', 'navigate', 'local-url'].includes(scenario);
    assert.equal(downloads.length, mustFail ? 0 : 1, scenario);
    assert.deepEqual(unexpected, []);
    if (scenario === 'local-url') assert.equal(requests.length, 1, 'local URLs never trigger account requests');
    if (['no-permission', 'revoked-permission'].includes(scenario)) assert.equal(nativeRequested, false);
    if (!mustFail) {
      const download = downloads[0];
      assert.equal(await download.failure(), null);
      assert.equal(download.suggestedFilename(), `chatgpt-capture-${id}.zip`);
      await download.saveAs(path.join(process.env.PRESS_CAPTURE_OUTPUT_DIR, scenario + '.zip'));
    }
    return { scenario, browser: context.browser().version(), requests: requests.length, downloaded: !mustFail };
  } finally {
    releaseNative();
    await context.close();
    await fs.rm(profile, { recursive: true, force: true });
  }
}

for (const scenario of ['happy', 'cookie-only', 'omission', 'audit-unavailable', 'unauthorized',
  'changed-branch', 'hostile-page', 'multiple-tabs', 'challenge', 'redirect', 'stalled',
  'rate-limit', 'navigate', 'popup-close', 'no-permission', 'revoked-permission', 'local-url', 'live-shape']) {
  test(`real capture extension: ${scenario}`, { timeout: 30000 }, async t => {
    t.diagnostic(JSON.stringify(await qualify(scenario)));
  });
}
