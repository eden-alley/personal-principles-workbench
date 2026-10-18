// Recheck the previously generated synthetic records without repeating model work.
const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const prior=JSON.parse(fs.readFileSync('artifacts/library-organization-acceptance.json'));
 const report={checks:[],errors:[]};
 try{
  assert.equal(prior.sources.length,3);const second=prior.sources[1],img=prior.sources[2];
  const page=await browser.newPage({viewport:{width:1440,height:1000}});let bearer='';
  page.on('request',r=>{if(r.url().startsWith('http://127.0.0.1:3081/api/')){const h=r.headers().authorization;if(h)bearer=h;}});
  page.on('pageerror',e=>report.errors.push(e.message));
  const credentials=JSON.parse(fs.readFileSync('artifacts/librechat-runtime/test-account.json'));
  await page.goto('http://127.0.0.1:3081/login');await page.locator('[name=email]').fill(credentials.email);
  await page.locator('[name=password]').fill(credentials.password);await page.locator('button[type=submit]').click();
  await page.locator('#prompt-textarea').waitFor();
  async function api(path){const r=await page.request.get('http://127.0.0.1:3081/api/'+path,{headers:{Authorization:bearer}});assert.ok(r.ok());return r.json();}
  await page.getByTestId('header-library-button').click();const dialog=page.getByRole('dialog');
  await dialog.getByRole('button',{name:'素材',exact:true}).click();
  await dialog.getByLabel('按主题查看',{exact:true}).selectOption(second.organization.topics[0]);
  await dialog.getByRole('button').filter({hasText:second.organization.title}).first().click();
  await dialog.getByText(second.organization.summary,{exact:true}).waitFor();
  await dialog.getByText('查看两份原文依据',{exact:true}).first().click();
  report.checks.push('Topic filter, persisted summary, relation and quoted evidence displayed');
  const exported=await api('library/export');const ex=exported.entries.find(e=>e.id===img.id);
  assert.ok(ex.organization.summary);assert.equal(crypto.createHash('sha256').update(Buffer.from(ex.assets[0].data_base64,'base64')).digest('hex'),ex.assets[0].sha256);
  report.checks.push('Export includes organization and matching original image hash');
  const started=prior.sources[0].organization.updated_at;
  assert.equal(exported.entries.filter(e=>e.kind!=='source'&&e.created_at>=started).length,0);
  report.checks.push('No personal principles/experiences created by organization');
  await page.screenshot({path:'artifacts/library-organization-desktop.png'});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/library-organization-mobile.png'});
  assert.ok(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  report.checks.push('390px dialog no horizontal overflow');assert.deepEqual(report.errors,[]);report.pass=true;
 }catch(e){report.failure=e.message;console.error(e);process.exitCode=1;}
 finally{fs.writeFileSync('artifacts/library-organization-ui-finish.json',JSON.stringify(report,null,2));await browser.close();}
})();
