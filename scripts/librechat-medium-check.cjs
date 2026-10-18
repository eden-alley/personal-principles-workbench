const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const report={configuredEffort:'medium',mock:false};
 try{
  const page=await browser.newPage();
  const credentials=JSON.parse(fs.readFileSync('artifacts/librechat-runtime/test-account.json'));
  await page.goto('http://127.0.0.1:3081/login');
  await page.locator('[name=email]').fill(credentials.email);
  await page.locator('[name=password]').fill(credentials.password);
  await page.locator('button[type=submit]').click();
  await page.locator('#prompt-textarea').fill('连通性测试，请只回复：连接成功。');
  const start=Date.now();
  await page.getByTestId('send-button').click();
  await page.waitForFunction(()=>{
    const es=document.querySelectorAll('[data-testid="message-body"]');
    return es.length>=2&&es[es.length-1].innerText.trim().length>0;
  },null,{timeout:130000});
  report.firstMs=Date.now()-start;
  await page.getByTestId('regenerate-generation-button').last().waitFor({timeout:130000});
  report.totalMs=Date.now()-start;
  report.answer=await page.getByTestId('message-body').last().innerText();
  assert.ok(report.answer.includes('连接成功'));
  report.pass=true;
 }finally{fs.writeFileSync('artifacts/librechat-medium-browser.json',JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e.message);process.exitCode=1;});
