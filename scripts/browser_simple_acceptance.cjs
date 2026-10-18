const {chromium}=require('playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
  const errors=[],shots=[],base='http://127.0.0.1:8766';
  page.on('pageerror',e=>errors.push(e.message));
  async function capture(name){
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false,name+' overflow');
   const path='artifacts/simple-'+name+'.png';await page.screenshot({path,fullPage:true});shots.push(path);
  }
  async function done(){await page.waitForFunction(()=>document.querySelector('[data-chat-progress]')?.textContent.includes('可继续聊'));}
  await page.goto(base+'/');
  await capture('home-desktop');
  await page.evaluate(()=>{window.simpleDocumentMarker='same-document';});
  await page.locator('#chat-content').fill('【虚构验收】我想保留阅读时自由探索的空间，不希望每次都要完成一篇笔记。');
  await page.getByRole('button',{name:'发送',exact:true}).click();await done();
  const conversation=page.url();assert.match(conversation,/\/cases\/\d+\/$/);
  assert.equal(await page.locator('.chat-message-assistant').count(),1);
  assert.equal(await page.locator('#chat-content').inputValue(),'');
  await page.locator('#chat-content').fill('但碰到真正有触动的内容，我也想偶尔记录。');
  await page.locator('#chat-content').press('Control+Enter');await done();
  assert.equal(await page.locator('.chat-message-assistant').count(),2);
  assert.equal(await page.locator('.chat-message-user').count(),2);
  assert.equal(await page.evaluate(()=>window.simpleDocumentMarker),'same-document');
  assert.equal(await page.locator('.chat-options').getAttribute('open'),null);
  await capture('conversation-desktop');
  await page.reload();assert.equal(await page.locator('.chat-message-assistant').count(),2);
  await page.goto(base+'/');await page.locator('#chat-content').fill('【虚构验收】这条只保存，暂时不需要回复。');
  await page.getByRole('button',{name:'仅保存',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-chat-progress]')?.textContent.includes('已保存。需要时'));
  assert.equal(await page.locator('.chat-message-assistant').count(),0);
  await page.getByRole('button',{name:'继续回复',exact:true}).click();await done();
  assert.equal(await page.locator('.chat-message-user').count(),1);
  await page.goto(base+'/');
  let intercepted=false;
  await page.route('**/chat/send/',async route=>{
   if(intercepted)return route.continue();intercepted=true;
   const response=await route.fetch();const events=(await response.text()).trim().split('\n').map(JSON.parse);
   const saved=events.find(e=>e.type==='saved');assert.ok(saved);
   await route.fulfill({status:200,contentType:'application/x-ndjson',body:JSON.stringify(saved)+'\n'+JSON.stringify({type:'error',code:'test_disconnect',message:'验收模拟：回复已经保存，但浏览器没有收到完成事件。'})+'\n'});
  });
  const retryText='【虚构验收】这条用于检查断线后重试，不应重复保存。';
  await page.locator('#chat-content').fill(retryText);await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.locator('[data-chat-error]:not([hidden])').waitFor();
  assert.equal(await page.locator('#chat-content').inputValue(),retryText);
  await page.getByRole('button',{name:'重试发送',exact:true}).click();await done();
  assert.equal(await page.locator('.chat-message-user').count(),1);assert.equal(await page.locator('.chat-message-assistant').count(),1);
  await page.unroute('**/chat/send/');
  await page.goto(base+'/sources/new/');assert.equal(await page.locator('#id_title').isVisible(),false);assert.equal(await page.locator('#id_text').isVisible(),true);
  await capture('source-desktop');
  await page.locator('#id_text').fill('【虚构验收】只填原文也能保存素材。');
  await Promise.all([page.waitForURL(/\/sources\/\d+\/$/),page.getByRole('button',{name:'保存素材',exact:true}).click()]);
  const source=page.url();
  await page.goto(base+'/settings/');assert.equal(await page.locator('#id_codex_model').isVisible(),false);
  assert.equal(await page.locator('#id_confirmed').isVisible(),false);await capture('settings-desktop');
  await page.getByText('高级设置：模型、推理强度与参考数量',{exact:true}).click();
  assert.equal(await page.locator('#id_codex_model').inputValue(),'gpt-6.1-sol');
  assert.equal(await page.locator('#id_codex_reasoning_effort').inputValue(),'high');
  await page.setViewportSize({width:390,height:844});
  for(const [name,url]of[['home-mobile',base+'/'],['conversation-mobile',conversation],['source-mobile',base+'/sources/new/'],['settings-mobile',base+'/settings/']]){await page.goto(url);await capture(name);}
  await page.goto(base+'/');await page.getByRole('button',{name:'导航',exact:true}).click();
  assert.equal(await page.locator('#side-nav').getAttribute('aria-hidden'),'false');
  await page.keyboard.press('Escape');assert.equal(await page.locator('#side-nav').getAttribute('aria-hidden'),'true');
  assert.deepEqual(errors,[]);
  fs.writeFileSync('artifacts/simple-browser-acceptance.json',JSON.stringify({mode:'explicit_demo',conversation,source,inlineTwoTurns:true,saveOnly:true,resume:true,retryAfterLostCompletion:true,sourceOnlyText:true,advancedCollapsed:true,mobileMenu:true,shots,errors},null,2));
  console.log('PASS simple chat: inline two turns, save/resume, lost completion retry, source save, settings, desktop/mobile, no JS errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
