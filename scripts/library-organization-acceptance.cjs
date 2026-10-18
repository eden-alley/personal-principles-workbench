// Synthetic test-account only. Uses current Codex quota for three source summaries and one chat.
const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const report={checks:[],errors:[],sources:[]};
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage();let bearer='';
  page.on('request',r=>{if(r.url().startsWith('http://127.0.0.1:3081/api/')){const h=r.headers().authorization;if(h)bearer=h;}});
  page.on('pageerror',e=>report.errors.push(e.message));
  const credentials=JSON.parse(fs.readFileSync('artifacts/librechat-runtime/test-account.json'));
  await page.goto('http://127.0.0.1:3081/login');
  await page.locator('[name=email]').fill(credentials.email);await page.locator('[name=password]').fill(credentials.password);
  await page.locator('button[type=submit]').click();await page.locator('#prompt-textarea').waitFor();
  async function api(path){const r=await page.request.get('http://127.0.0.1:3081/api/'+path,{headers:{Authorization:bearer}});assert.ok(r.ok(),`${path}: ${r.status()}`);return r.json();}
  const before=(await api('library')).entries.filter(e=>e.kind!=='source').map(e=>[e.id,e.revision]);
  await page.getByTestId('header-library-button').click();const dialog=page.getByRole('dialog');
  const stamp=Date.now();
  async function add(text,title,file){
   await dialog.getByRole('button',{name:'新增素材',exact:true}).click();
   await dialog.getByLabel('标题（可选）',{exact:true}).fill(title);
   if(text)await dialog.getByLabel('素材原文',{exact:true}).fill(text);
   if(file)await dialog.getByLabel('上传图片或文本',{exact:true}).setInputFiles(file);
   await dialog.getByRole('button',{name:'保存并自动整理',exact:true}).click();
   await dialog.getByRole('heading',{name:title,exact:true}).waitFor({timeout:30000});
   const entry=(await api('library')).entries.find(e=>e.title===title);assert.ok(entry);
   assert.ok(['pending','running','ready'].includes(entry.organization.state));
   return entry;
  }
  async function ready(id){
   const start=Date.now();let value;
   while(Date.now()-start<180000){
    value=await api('library/entries/'+id);
    if(['failed','blocked'].includes(value.organization.state))throw new Error(JSON.stringify(value.organization));
    if(value.organization.state==='ready')break;
    await page.waitForTimeout(1000);
   }
   assert.equal(value.organization.state,'ready');
   assert.ok(value.organization.summary);assert.ok(value.organization.key_points.length);assert.ok(value.organization.topics.length);
   report.sources.push({id,elapsedMs:Date.now()-start,organization:value.organization});return value;
  }
  const firstText='验收虚构素材A：学习一个概念时，先合上书，尝试用自己的话向别人解释。如果解释不清楚，就回到材料补齐理解中的缺口。只反复阅读容易把熟悉感误当成真正理解。';
  const first=await add(firstText,`整理验收A-${stamp}`);
  const chat=await context.newPage();await chat.goto('http://127.0.0.1:3081/c/new');await chat.locator('#prompt-textarea').waitFor();
  await chat.locator('#prompt-textarea').fill('请只回复：聊天未被后台整理阻塞。');await chat.getByTestId('send-button').click();
  await chat.waitForFunction(()=>{const es=document.querySelectorAll('[data-testid="message-body"]');return es.length>=2&&es[es.length-1].innerText.includes('聊天未被后台整理阻塞')},null,{timeout:150000});
  report.checks.push('Foreground chat succeeds while source organization is queued/running');await chat.close();
  const a=await ready(first.id);assert.equal(a.text,firstText);report.checks.push('Direct text add automatically creates grounded summary, points and topics');
  const secondText='验收虚构素材B：学习时用自己的话解释概念，能暴露理解缺口，但会解释不代表一定会应用。完成解释之后，还应尝试解决一道新的相关问题；若无法应用，应回到材料补齐缺口。复述和实际应用相互补充。';
  const second=await add(secondText,`整理验收B-${stamp}`);const b=await ready(second.id);
  assert.ok(b.organization.relations.some(r=>r.entry_id===first.id),'Related material not connected');
  for(const rel of b.organization.relations){assert.ok(secondText.includes(rel.quote));}
  report.checks.push('New material links to existing source with quoted evidence');
  const image=await add('',`整理验收图片-${stamp}`,'artifacts/library-ocr-fixture.png');const c=await ready(image.id);
  assert.equal(c.parse_state,'ready');assert.equal(c.index_state,'ready');assert.match(c.transcript,/独处/);
  report.checks.push('Direct image add performs OCR then automatic organization');
  await page.reload();await page.locator('#prompt-textarea').waitFor();await page.getByTestId('header-library-button').click();
  await dialog.getByRole('button',{name:'素材',exact:true}).click();
  await dialog.getByLabel('按主题查看',{exact:true}).selectOption(b.organization.topics[0]);
  await dialog.getByRole('button').filter({hasText:b.organization.title}).first().click();
  await dialog.getByText(b.organization.summary,{exact:true}).waitFor();
  assert.deepEqual((await api('library')).entries.filter(e=>e.kind!=='source').map(e=>[e.id,e.revision]),before);
  const exported=await api('library/export');const ex=exported.entries.find(e=>e.id===image.id);
  assert.ok(ex.organization.summary);assert.equal(crypto.createHash('sha256').update(Buffer.from(ex.assets[0].data_base64,'base64')).digest('hex'),ex.assets[0].sha256);
  report.checks.push('Refresh, topic filter, export and unchanged personal principles/experiences');
  await page.screenshot({path:'artifacts/library-organization-desktop.png'});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/library-organization-mobile.png'});
  assert.ok(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  assert.deepEqual(report.errors,[]);report.pass=true;
 }catch(e){report.failure=e.message;console.error(e);process.exitCode=1;}
 finally{fs.writeFileSync('artifacts/library-organization-acceptance.json',JSON.stringify(report,null,2));await browser.close();}
})();
