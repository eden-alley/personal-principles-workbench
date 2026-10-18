// Uses only the explicitly isolated acceptance server, with real ChatGPT quota.
const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');

(async () => {
  const serverPid = Number(process.argv[2]);
  assert.ok(Number.isInteger(serverPid) && serverPid > 0, 'Pass the isolated test server PID');
  const children = () => Number(execFileSync('powershell.exe', ['-NoProfile', '-Command',
    `@(Get-CimInstance Win32_Process -Filter "ParentProcessId = ${serverPid} AND Name = 'codex.exe'").Count`],
    {encoding: 'utf8', windowsHide: true}).trim());
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const report = {mock: false, model: 'gpt-6.1-sol', reasoning_effort: 'high', errors: []};
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    page.setDefaultTimeout(15000);
    page.on('pageerror', e => report.errors.push(e.message));
    await page.addInitScript(() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]).includes('/send/')) {
          window.chatCapture = {statuses: 0, deltas: 0, complete: false, error: null};
          const capture = window.chatCapture;
          (async () => {
            const reader = response.clone().body.getReader(), decoder = new TextDecoder();
            let buffer = '';
            while (true) {
              const chunk = await reader.read();
              buffer += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
              const lines = buffer.split('\n'); buffer = lines.pop();
              for (const line of lines) {
                if (!line.trim()) continue;
                const event = JSON.parse(line);
                if (event.type === 'status') capture.statuses++;
                if (event.type === 'delta') capture.deltas++;
                if (event.type === 'saved') capture.url = event.url;
                if (event.type === 'complete') capture.complete = true;
                if (event.type === 'error') capture.error = event;
              }
              if (chunk.done) break;
            }
          })().catch(() => {});
        }
        return response;
      };
    });
    await page.goto('http://127.0.0.1:8766/');
    await page.locator('#chat-content').fill('【虚构取消验收，不是用户经历】我想安排阅读、运动和朋友聚会，又想让周末留有空白，三者时间冲突。请帮我理清取舍，不替我做决定。');
    await page.getByRole('button', {name: '发送', exact: true}).click();
    await page.waitForFunction(() => window.chatCapture?.statuses >= 2 || window.chatCapture?.deltas > 0 || window.chatCapture?.error, {}, {timeout: 60000});
    report.cancelledRequest = await page.evaluate(() => window.chatCapture);
    assert.equal(report.cancelledRequest.error, null);
    assert.equal(report.cancelledRequest.complete, false, 'Request must still be running when leaving');
    assert.ok(children() > 0, 'Real Codex child must be active');
    const leftAt = Date.now();
    await page.goto('http://127.0.0.1:8766/');
    const deadline = Date.now() + 15000;
    while (children() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 250));
    assert.equal(children(), 0, 'Cancelled request must release its owned SDK process');
    report.cancelCleanupSeconds = (Date.now() - leftAt) / 1000;
    fs.writeFileSync('artifacts/codex-cancel-acceptance.json', JSON.stringify(report, null, 2));

    await page.goto('http://127.0.0.1:8766' + report.cancelledRequest.url);
    assert.equal(await page.locator('.chat-message-assistant').count(), 0);
    assert.equal(await page.locator('#chat-content').inputValue(), '');
    assert.equal(await page.locator('[data-chat-send]').innerText(), '继续回复');
    const resumedAt = Date.now();
    await page.locator('[data-chat-send]').click();
    await page.waitForFunction(() => window.chatCapture?.complete || window.chatCapture?.error, {}, {timeout: 135000});
    report.resumedRequest = await page.evaluate(() => window.chatCapture);
    assert.equal(report.resumedRequest.error, null);
    assert.equal(report.resumedRequest.complete, true);
    assert.ok(report.resumedRequest.deltas > 0);
    await page.waitForFunction(() => document.querySelector('[data-chat-progress]')?.textContent.includes('可继续聊'));
    report.resumeSeconds = (Date.now() - resumedAt) / 1000;
    assert.equal(await page.locator('.chat-message-assistant').count(), 1);
    assert.equal(await page.locator('.chat-message-assistant .demo-badge').count(), 0);
    report.reply = await page.locator('.chat-message-assistant .chat-copy').innerText();
    await page.reload();
    assert.equal(await page.locator('.chat-message-assistant .chat-copy').innerText(), report.reply);
    report.persistedAfterReload = true;
    assert.deepEqual(report.errors, []);
    await page.screenshot({path: 'artifacts/codex-cancel-recovered.png', fullPage: true});
    fs.writeFileSync('artifacts/codex-cancel-acceptance.json', JSON.stringify(report, null, 2));
    console.log(JSON.stringify({pass: true, cancelCleanupSeconds: report.cancelCleanupSeconds, resumeSeconds: report.resumeSeconds, realDeltas: report.resumedRequest.deltas, persistedAfterReload: true}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
