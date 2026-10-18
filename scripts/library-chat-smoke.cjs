const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const report={checks:[]};
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  let bearer='';
  page.on('request',request=>{if(request.url().startsWith('http://127.0.0.1:3081/api/')){const header=request.headers().authorization;if(header)bearer=header;}});
  const credentials=JSON.parse(fs.readFileSync('artifacts/librechat-runtime/test-account.json'));
  await page.goto('http://127.0.0.1:3081/login');
  await page.locator('[name=email]').fill(credentials.email);
  await page.locator('[name=password]').fill(credentials.password);
  await page.locator('button[type=submit]').click();
  await page.locator('#prompt-textarea').waitFor();
  async function api(path){const res=await page.request.get('http://127.0.0.1:3081/api/library'+path,{headers:{Authorization:bearer}});return {status:res.status(),data:await res.json()};}
  const before=await api('');
  assert.equal(before.status,200);
  await page.locator('#prompt-textarea').fill('记住：验收示例——我可以自主选择独处，不必为了合群参加所有活动。');
  const start=Date.now();
  await page.getByTestId('send-button').click();
  await page.waitForFunction(()=>{const es=document.querySelectorAll('[data-testid="message-body"]');return es.length>=2&&es[es.length-1].innerText.trim().length>0},null,{timeout:30000});
  await page.getByTestId('regenerate-generation-button').last().waitFor({timeout:30000});
  const answer=await page.getByTestId('message-body').last().innerText();
  report.checks.push({answer,elapsedMs:Date.now()-start,url:page.url()});
  assert.match(answer,/已保存为原则/);
  const after=await api('');
  assert.equal(after.data.entries.length,before.data.entries.length+1);
  report.entry=after.data.entries[0];
  report.pass=true;
  await page.screenshot({path:'artifacts/library-chat-smoke.png'});
 }catch(error){report.failure=error.message;process.exitCode=1;}
 finally{fs.writeFileSync('artifacts/library-chat-smoke.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));await browser.close();}
})();
