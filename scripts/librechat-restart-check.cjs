const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const report={};
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const credentials=JSON.parse(fs.readFileSync('artifacts/librechat-runtime/test-account.json'));
  await page.goto('http://127.0.0.1:3081/login');
  await page.locator('[name=email]').fill(credentials.email);
  await page.locator('[name=password]').fill(credentials.password);
  await page.locator('button[type=submit]').click();
  await page.locator('#prompt-textarea').waitFor();
  const acceptance=JSON.parse(fs.readFileSync('artifacts/librechat-acceptance.json'));
  const file=acceptance.checks.find(x=>x.answer.includes('Q7M4'));
  await page.goto(file.url); await page.locator('#prompt-textarea').waitFor();
  await page.getByTestId('message-body').last().waitFor();
  report.savedAnswer=(await page.getByTestId('message-body').last().innerText()).includes('Q7M4');
  report.savedAttachment=(await page.locator('body').innerText()).includes('librechat-fixture.txt');
  report.memoryHidden=await page.getByTestId('nav-panel-memories').count()===0;
  report.mcpHidden=await page.getByTestId('nav-panel-mcp-builder').count()===0;
  assert.ok(report.savedAnswer&&report.savedAttachment&&report.memoryHidden&&report.mcpHidden);
  await page.screenshot({path:'artifacts/librechat-final.png'});
  report.pass=true;
 }finally{fs.writeFileSync('artifacts/librechat-restart-check.json',JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e.message);process.exitCode=1;});
