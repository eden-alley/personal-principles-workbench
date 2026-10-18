const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');

(async () => {
  const report = JSON.parse(fs.readFileSync('artifacts/browser-acceptance.json', 'utf8'));
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.goto(report.decisionUrl);
    assert.match(await page.locator('.citation-list').innerText(), /版本 1/);
    assert.match(await page.locator('main').innerText(), /这条理由必须保持不变/);
    await page.goto(report.principleUrl);
    assert.match(await page.locator('.version-list').innerText(), /版本 2/);
    await page.goto(report.sourceUrl);
    assert.match(await page.locator('.source-copy').innerText(), /浏览器验收虚构素材/);
    const base = new URL(report.sourceUrl).origin;
    const download = await page.request.get(base + '/export/');
    assert.equal(download.status(), 200);
    fs.writeFileSync('artifacts/acceptance-export.zip', await download.body());
    await page.goto(base);
    await page.screenshot({ path: 'artifacts/home-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.getElementById('side-nav').getBoundingClientRect().right <= 0);
    await page.screenshot({ path: 'artifacts/home-mobile.png', fullPage: true });
    const result = { restart: 'passed', oldDecisionVersion: 1, currentPrincipleVersion: 2, originalSourcePreserved: true, exportDownloaded: true };
    fs.writeFileSync('artifacts/restart-check.json', JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
