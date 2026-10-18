const {chromium} = require('playwright');
const fs = require('node:fs');
const crypto = require('node:crypto');
const root = 'artifacts/librechat-runtime/';
(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const credentialsPath = root + 'test-account.json';
    const fresh = !fs.existsSync(credentialsPath);
    const credentials = fresh ? {email:'pilot-test@example.invalid', password:crypto.randomBytes(24).toString('base64url')} : JSON.parse(fs.readFileSync(credentialsPath));
    if (fresh) fs.writeFileSync(credentialsPath, JSON.stringify(credentials));
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    await page.goto('http://127.0.0.1:3081/' + (fresh ? 'register' : 'login'));
    if (fresh) {
      await page.locator('input[name=name]').fill('验收测试');
      await page.locator('input[name=username]').fill('pilot-test');
      await page.locator('input[name=confirm_password]').fill(credentials.password);
    }
    await page.locator('input[name=email]').fill(credentials.email);
    await page.locator('input[name=password]').fill(credentials.password);
    await page.locator('button[type=submit]').click();
    if (fresh) {
      await page.waitForURL('**/login');
      await page.locator('input[name=email]').fill(credentials.email);
      await page.locator('input[name=password]').fill(credentials.password);
      await page.locator('button[type=submit]').click();
    }
    await page.waitForURL('**/c/**');
    await page.waitForTimeout(1000);
    await page.context().storageState({path:root+'test-browser-state.json'});
    console.log(await page.locator('body').innerText());
    console.log(await page.locator('textarea, [contenteditable=true]').evaluateAll(es=>es.map(e=>({tag:e.tagName,id:e.id,placeholder:e.getAttribute('placeholder')}))));
    await page.screenshot({path:'artifacts/librechat-home.png'});
  } finally { await browser.close(); }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
