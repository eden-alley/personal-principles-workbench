const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const report={checks:[]};
 try{
  const page=await browser.newPage();let bearer='';
  page.on('request',r=>{if(r.url().startsWith('http://127.0.0.1:3081/api/')){const h=r.headers().authorization;if(h)bearer=h;}});
  const credentials=JSON.parse(fs.readFileSync('artifacts/librechat-runtime/test-account.json'));
  await page.goto('http://127.0.0.1:3081/login');
  await page.locator('[name=email]').fill(credentials.email);await page.locator('[name=password]').fill(credentials.password);
  await page.locator('button[type=submit]').click();await page.locator('#prompt-textarea').waitFor();
  await page.locator('#prompt-textarea').fill('请简短解释什么是递归。');await page.getByTestId('send-button').click();
  await page.getByRole('button',{name:/停止|Stop/}).waitFor();await page.waitForTimeout(700);
  await page.getByRole('button',{name:/停止|Stop/}).click();
  await page.locator('#prompt-textarea').fill('记住：重试验收示例 '+Date.now());
  await page.getByTestId('send-button').click();
  await page.waitForFunction(()=>{const es=document.querySelectorAll('[data-testid="message-body"]');return es.length&&es[es.length-1].innerText.includes('已保存为原则')},null,{timeout:120000});
  await page.getByTestId('regenerate-generation-button').last().waitFor({timeout:120000});
  assert.match(await page.getByTestId('message-body').last().innerText(),/已保存为原则/);
  async function entries(){const r=await page.request.get('http://127.0.0.1:3081/api/library',{headers:{Authorization:bearer}});assert.ok(r.ok());return (await r.json()).entries;}
  const before=await entries();report.checks.push('Stop then next send succeeds');
  await page.getByTestId('regenerate-generation-button').last().click();
  await page.waitForTimeout(1500);
  await page.getByTestId('regenerate-generation-button').last().waitFor({timeout:120000});
  assert.equal((await entries()).length,before.length);report.checks.push('Regeneration does not duplicate explicit save');
  await page.reload();await page.locator('#prompt-textarea').waitFor();
  assert.match(await page.getByTestId('message-body').last().innerText(),/已保存为原则/);
  assert.equal((await entries()).length,before.length);report.checks.push('Refresh preserves receipt and library');report.pass=true;
 }catch(e){report.failure=e.message;console.error(e);process.exitCode=1;}
 finally{fs.writeFileSync('artifacts/library-retry-acceptance.json',JSON.stringify(report,null,2));await browser.close();}
})();
