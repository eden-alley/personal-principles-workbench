const {chromium}=require('playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
 const errors=[],base='http://127.0.0.1:8766';page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/settings/');
 assert.equal(await page.locator('#id_ai_provider').inputValue(),'codex');
 assert.equal(await page.locator('#id_codex_model').inputValue(),'gpt-6.1-sol');
 assert.equal(await page.locator('#id_codex_reasoning_effort').inputValue(),'high');
 assert.equal(await page.locator('#id_codex_model').isVisible(),false);
 await page.screenshot({path:'artifacts/simple-final-settings-desktop.png',fullPage:true});
 const resumeCase=process.argv[2];
 await page.addInitScript(()=>{
  const original=window.fetch;
  window.fetch=async(...args)=>{
   const response=await original(...args);
   if(String(args[0]).includes('/send/')){
    window.directChatCapture={deltas:0,complete:false};
    response.clone().text().then(text=>{for(const line of text.trim().split('\n')){const e=JSON.parse(line);if(e.type==='delta')window.directChatCapture.deltas++;if(e.type==='complete')window.directChatCapture.complete=true;}}).catch(()=>{});
   }
   return response;
  };
 });
 await page.goto(base+'/');await page.evaluate(()=>window.realChatDocument='same-document');
 const input='【虚构验收情境，不是用户真实经历】我想给周末留一点不安排任务的时间，但又担心这样会浪费时间。请用简短自然的中文帮我理清这个纠结，不要替我做决定。';
 const started=Date.now();let capture=null;
 if(resumeCase){await page.goto(base+'/cases/'+Number(resumeCase)+'/');}
 else{
  await page.locator('#chat-content').fill(input);
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-chat-progress]')?.textContent.includes('可继续聊')||!!document.querySelector('[data-chat-error]:not([hidden])'),{},{timeout:180000});
  if(await page.locator('[data-chat-error]:not([hidden])').count())throw new Error(await page.locator('[data-chat-error-text]').innerText());
  await page.waitForFunction(()=>window.directChatCapture?.complete);
  capture=await page.evaluate(()=>window.directChatCapture);assert.ok(capture.deltas>0);
  assert.equal(await page.evaluate(()=>window.realChatDocument),'same-document');
 }
 assert.equal(await page.locator('.chat-message-assistant').count(),1);
 assert.equal(await page.locator('.chat-message-assistant .demo-badge').count(),0);
 assert.equal(await page.locator('#chat-content').inputValue(),'');
 assert.equal(await page.locator('.nav-link.is-active').innerText(),'历史对话');
 const conversation=page.url(),reply=await page.locator('.chat-message-assistant .chat-copy').innerText();
 await page.screenshot({path:'artifacts/simple-final-codex-conversation-desktop.png',fullPage:true});
 await page.reload();assert.equal(await page.locator('.chat-message-assistant .chat-copy').innerText(),reply);
 await page.setViewportSize({width:390,height:844});
 for(const [name,path]of[['conversation',conversation],['home',base+'/'],['settings',base+'/settings/'],['source',base+'/sources/new/']]){
  await page.goto(path);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'artifacts/simple-final-'+name+'-mobile.png',fullPage:true});
 }
 assert.deepEqual(errors,[]);
 fs.writeFileSync('artifacts/simple-codex-acceptance.json',JSON.stringify({mock:false,model:'gpt-6.1-sol',reasoning_effort:'high',conversation,input,reply,deltas:capture?.deltas??null,verificationSeconds:(Date.now()-started)/1000,noDocumentNavigation:resumeCase?'covered by demo browser test':true,persistedAfterReload:true,note:resumeCase?'Original real call completed in the UI; Playwright response.text failed after completion. This run verifies that same saved reply without repeating inference. Delta count unavailable.':'',errors},null,2));
 console.log('PASS real Codex direct send → streamed inline reply → SQLite persistence; desktop/mobile, no JS errors');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
