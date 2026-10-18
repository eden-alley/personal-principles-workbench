const {chromium}=require('playwright');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const report={model:'gpt-6.1-sol',effort:'high',mock:false,errors:[]};
 try{
  const context=await browser.newContext({storageState:'artifacts/librechat-runtime/test-browser-state.json',viewport:{width:1440,height:1000}});
  const page=await context.newPage();
  page.on('pageerror',e=>report.errors.push(e.message));
  await page.goto('http://127.0.0.1:3081/c/new');
  const box=page.locator('#prompt-textarea');
  await box.fill('这是本地连通性测试。请只回复：连接成功。');
  report.start=Date.now();
  await box.press('Enter');
  await page.waitForFunction(()=>document.body.innerText.includes('连接成功。') && document.querySelectorAll('[data-message-id]').length>0, {timeout:120000}).catch(()=>{});
  await page.waitForTimeout(1500);
  report.elapsedMs=Date.now()-report.start;
  report.url=page.url();
  report.body=await page.locator('body').innerText();
  report.buttons=await page.locator('button').evaluateAll(es=>es.map(e=>({text:e.innerText,label:e.getAttribute('aria-label'),title:e.title})));
  await page.screenshot({path:'artifacts/librechat-first-chat.png'});
  fs.writeFileSync('artifacts/librechat-chat-check.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
