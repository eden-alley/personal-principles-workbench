/* Real Codex browser acceptance. Requires explicit account-usage authorization.
   Run against an isolated empty WORKBENCH_DATA_DIR, never production data. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const BASE = 'http://127.0.0.1:8766';
(async () => {
 const browser = await chromium.launch({channel:'msedge', headless:true});
 const context = await browser.newContext({viewport:{width:1440,height:1000}});
 await context.addInitScript(() => {
   const originalFetch = window.fetch;
   window.fetch = async (...args) => {
     const response = await originalFetch(...args);
     if (!(response.headers.get('content-type') || '').includes('application/x-ndjson')) return response;
     const decoder = new TextDecoder(); let text = '';
     sessionStorage.setItem('acceptanceStream', '');
     const tap = new TransformStream({transform(chunk, controller) {
       text += decoder.decode(chunk, {stream:true});
       sessionStorage.setItem('acceptanceStream',text);
       controller.enqueue(chunk);
     }});
     return new Response(response.body.pipeThrough(tap), {status:response.status,headers:response.headers});
   };
 });
 const page = await context.newPage();
 page.setDefaultTimeout(15000);
 const errors = [], calls = [];
 page.on('pageerror', e => errors.push(e.message));
 const report = {mock:false, sdk:'openai-codex 0.162.1', model:'gpt-5.6-luna', started:new Date().toISOString(), calls};
 async function submit(name, url) {
   await Promise.all([page.waitForURL(url, {timeout:30000}), page.getByRole('button',{name,exact:true}).click()]);
 }
 async function analyze(label) {
   await page.locator('#id_confirmed_send').check();
   await page.getByRole('button',{name:'同意并生成建议',exact:true}).click();
   await Promise.race([
     page.waitForURL(/\/suggestions\/\d+\/$/, {timeout:180000}),
     page.locator('[data-stream-error]:not([hidden])').waitFor({timeout:180000}).then(async()=>{throw new Error(await page.locator('[data-stream-error-message]').innerText());})
   ]);
   const stream = await page.evaluate(()=>sessionStorage.getItem('acceptanceStream'));
   const events = stream.trim().split('\n').filter(Boolean).map(JSON.parse);
   const failure = events.find(e=>e.type==='error');
   assert.equal(failure, undefined, failure && JSON.stringify(failure));
   assert.ok(events.some(e=>e.type==='delta'), 'Real streamed deltas required');
   assert.equal(events.at(-1).type,'complete');
   const text=await page.locator('main').innerText();
   assert.match(text,/来源：Codex/);
   assert.doesNotMatch(text,/演示数据 \/ 非真实模型输出/);
   calls.push({task:label,suggestionUrl:page.url(),deltaCount:events.filter(e=>e.type==='delta').length});
   console.log(label+': real stream completed, '+calls.at(-1).deltaCount+' deltas');
 }
 try {
   await page.goto(BASE+'/settings/');
   assert.equal(await page.locator('#id_ai_provider').inputValue(),'codex');
   await page.getByRole('button',{name:'刷新状态',exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('[data-ai-status-label]').textContent==='已连接',{timeout:45000});
   report.status=await page.locator('[data-ai-status-message]').innerText();
   await page.screenshot({path:'artifacts/codex-settings-desktop.png',fullPage:true});
   console.log('ChatGPT account connection verified from settings');
   await page.goto(BASE+'/sources/new/');
   await page.locator('#id_text').fill('【真实连接验收用的虚构素材，非个人经历】我希望阅读是自由探索，不一定每次都要产出总结；当家人急需帮助时，我愿意临时调整阅读安排。请保留这个价值取向以及它的边界，不强制制定计划。');
   await page.locator('#id_title').fill('Codex 真实连接验收（虚构素材）');
   await submit('保存素材',/\/sources\/\d+\/$/);
   report.sourceUrl=page.url();
   await page.getByRole('link',{name:'提炼原则',exact:true}).click();
   await analyze('extract');
   report.generatedTitle=await page.locator('#id_title').inputValue();
   report.generatedStatement=await page.locator('#id_statement').inputValue();
   assert.ok(report.generatedStatement.length>10);
   await page.screenshot({path:'artifacts/codex-candidate-desktop.png',fullPage:true});
   // Before explicit confirmation, only a suggestion exists.
   const principleList = await page.request.get(BASE+'/principles/');
   assert.doesNotMatch(await principleList.text(), new RegExp(report.generatedTitle.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
   await page.locator('#id_title').fill('阅读可以保留自由探索（虚构验收）');
   await page.locator('#id_status').selectOption('candidate');
   await page.locator('#id_confirmed').check();
   await submit('确认保存',/\/principles\/\d+\/$/);
   report.principleUrl=page.url();
   assert.match(await page.locator('main').innerText(),/候选/);
   await page.reload();
   assert.match(await page.locator('main').innerText(),/阅读可以保留自由探索/);
   console.log('User-confirmation boundary and persisted candidate verified');
   await page.goto(BASE+'/cases/new/');
   await page.locator('#id_situation').fill('【虚构验收情境】我读小说只是为了享受，有朋友建议每次阅读后都写笔记。我不希望兴趣都被任务化。');
   await submit('保存情境',/\/cases\/\d+\/$/);
   report.caseUrl=page.url();
   await page.getByRole('link',{name:'预览 AI 整理',exact:true}).click();
   await analyze('chat-first');
   await page.goto(report.caseUrl);
   await page.locator('#id_content').fill('接着刚才的讨论，请给出一个反例：什么时候简短笔记可能帮助享受阅读？不要把它变成必须完成的计划。');
   await submit('保存补充',/\/cases\/\d+\/$/);
   await page.getByRole('link',{name:'预览 AI 整理',exact:true}).click();
   const preview=JSON.parse(await page.locator('.json-preview').innerText());
   assert.ok(preview.messages.some(m=>m.role==='assistant'),'SQLite prior AI message replayed');
   assert.ok(preview.messages.some(m=>m.role==='user'&&m.content.includes('反例')));
   report.secondTurnRoles=preview.messages.map(m=>m.role);
   await analyze('chat-second');
   report.secondAnswer=await page.locator('.answer-block p').innerText();
   await page.screenshot({path:'artifacts/codex-multiturn-desktop.png',fullPage:true});
   await page.setViewportSize({width:390,height:844});
   await page.goto(BASE+'/settings/');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth+1));
   await page.screenshot({path:'artifacts/codex-settings-mobile.png',fullPage:true});
   report.errors=errors;
   assert.deepEqual(errors,[]);
   report.completed=new Date().toISOString();
   fs.writeFileSync('artifacts/codex-browser-acceptance.json',JSON.stringify(report,null,2));
   console.log('PASS real browser → Django → Codex SDK → editable suggestion → confirm → SQLite; two-turn chat; responsive UI');
 } catch(error) {
   report.failure=error.message;
   fs.writeFileSync('artifacts/codex-browser-failure.json',JSON.stringify(report,null,2));
   await page.screenshot({path:'artifacts/codex-browser-failure.png',fullPage:true});
   throw error;
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
