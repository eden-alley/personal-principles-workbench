const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const report={model:'gpt-6.1-sol',effort:'medium',checks:[],errors:[]};
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  let bearer='';
  page.on('request',r=>{if(r.url().startsWith('http://127.0.0.1:3081/api/')){const h=r.headers().authorization;if(h)bearer=h;}});
  page.on('pageerror',e=>report.errors.push(e.message));
  const credentials=JSON.parse(fs.readFileSync('artifacts/librechat-runtime/test-account.json'));
  await page.goto('http://127.0.0.1:3081/login');
  await page.locator('[name=email]').fill(credentials.email);
  await page.locator('[name=password]').fill(credentials.password);
  await page.locator('button[type=submit]').click();
  await page.locator('#prompt-textarea').waitFor();
  async function api(path,body){
   const response=await page.request[body?'post':'get']('http://127.0.0.1:3081/api/'+path,{headers:{Authorization:bearer},...(body?{data:body}:{})});
   assert.ok(response.ok(),`${path}: ${response.status()}`);return response.json();
  }
  async function send(prompt){
   const old=await page.getByTestId('message-body').count();
   await page.locator('#prompt-textarea').fill(prompt);
   const start=Date.now();await page.getByTestId('send-button').click();
   await page.waitForFunction(n=>{const es=document.querySelectorAll('[data-testid="message-body"]');return es.length>=n+2&&es[es.length-1].innerText.trim().length>0},old,{timeout:150000});
   const firstMs=Date.now()-start;
   await page.getByTestId('regenerate-generation-button').last().waitFor({state:'visible',timeout:150000});
   const answer=await page.getByTestId('message-body').last().innerText();
   const conversation_id=page.url().split('/').pop();
   const messages=(await api('messages?conversationId='+conversation_id)).messages;
   const last=messages.filter(m=>!m.isCreatedByUser).sort((a,b)=>new Date(b.createdAt)-new Date(a.createdAt))[0];
   const turn=await api(`library/turn?conversation_id=${conversation_id}&message_id=${last.messageId}`);
   const item={prompt,answer,firstMs,totalMs:Date.now()-start,conversation_id,turn};
   report.checks.push(item);console.log(JSON.stringify(item));return item;
  }
  await page.locator('input[type=file]').setInputFiles('artifacts/library-ocr-fixture.png');
  await page.waitForTimeout(1500);
  const collected=await send('先收着');
  assert.match(collected.answer,/已保存为素材/);
  let source;
  for(let i=0;i<30;i++){
   const entries=(await api('library')).entries;
   source=entries.find(e=>e.kind==='source'&&e.conversation_id===collected.conversation_id);
   if(source?.index_state==='ready')break;
   await page.waitForTimeout(500);
  }
  assert.ok(source&&source.parse_state==='ready'&&source.index_state==='ready','Image parsing/indexing did not finish');
  assert.match(source.transcript,/独处/);
  report.image=source;
  const experience=await api('library/experiences',{facts:'验收示例：我上周参加一次小范围同学聚餐，交流共同话题后心情很好，感到恢复了精力。',confirmed:true,operation_key:crypto.randomUUID()});
  await page.goto('http://127.0.0.1:3081/c/new');await page.locator('#prompt-textarea').waitFor();
  const social=await send('最近有人约我吃饭，我想一个人待着，又怕显得不合群。你怎么看？简短聊聊，不用制定行动计划。');
  assert.ok(social.turn.references.some(r=>r.id===source.id),'Unlinked source not recalled');
  assert.ok(social.turn.references.some(r=>r.kind==='principle'),'Confirmed principle not recalled');
  const countBefore=(await api('library')).entries.length;
  const agreement=await send('这个说法挺有道理。');
  assert.equal((await api('library')).entries.length,countBefore,'Agreement silently saved');
  await page.goto('http://127.0.0.1:3081/c/new');await page.locator('#prompt-textarea').waitFor();
  const unrelated=await send('Python 如何把列表里的每个数字乘二？只给一行示例代码。');
  assert.equal(unrelated.turn.references.length,0,'Unrelated personal references');
  await page.goto('http://127.0.0.1:3081/c/new');await page.locator('#prompt-textarea').waitFor();
  const kickoff=await send('请只回复：开始筛选。');
  await api('library/conversation',{conversation_id:kickoff.conversation_id,selected_ids:[source.id,experience.id]});
  const proposal=await send('请根据我本次指定的素材和经历，帮我拟定一条原则，既尊重独处，也为有质量的交流留空间。显示具体草稿，先不要保存。');
  assert.ok(proposal.turn.drafts.length,'Requested draft missing');
  const draft=proposal.turn.drafts[0];
  assert.ok(draft.payload.source_ids.includes(source.id));
  assert.ok(draft.payload.experience_ids.includes(experience.id));
  const confirmation=await send('确认保存');
  assert.match(confirmation.answer,/已保存为原则/);
  const saved=(await api(`library/turn?conversation_id=${proposal.conversation_id}&message_id=${draft.message_id}`)).drafts.find(d=>d.id===draft.id);
  assert.equal(saved.status,'saved');
  const v1=await api('library/entries/'+saved.entry_id);
  const v2=await api('library/principles',{entry_id:v1.id,expected_revision:v1.revision,statement:v1.text+' 我也会根据当时的精力调整。',reason:'验收示例：加入精力边界',source_ids:[source.id],experience_ids:[experience.id],confirmed:true,operation_key:crypto.randomUUID()});
  assert.equal(v2.versions.length,v1.versions.length+1);assert.equal(v2.versions.find(v=>v.version===v1.version).text,v1.text);
  const exported=await api('library/export');
  const exportedAsset=exported.entries.find(e=>e.id===source.id).assets[0];
  assert.equal(crypto.createHash('sha256').update(Buffer.from(exportedAsset.data_base64,'base64')).digest('hex'),exportedAsset.sha256);
  report.saved={id:v2.id,versions:v2.versions,source_ids:v2.source_ids,experience_ids:v2.experience_ids};
  report.exportOriginalHash=true;report.pass=true;
  await page.screenshot({path:'artifacts/library-flow-acceptance.png'});
 }catch(error){report.failure=error.message;console.error(error.message);process.exitCode=1;}
 finally{fs.writeFileSync('artifacts/library-flow-acceptance.json',JSON.stringify(report,null,2));await browser.close();}
})();
