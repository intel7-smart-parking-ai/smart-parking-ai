const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');

(async()=>{
 const payload=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
 const results=[],errors=[];
 const step=message=>console.log('STEP '+message);
 async function check(id,fn){step(id);const start=Date.now();try{const actual=await fn();results.push({id,status:'PASS',actual,ms:Date.now()-start});}
  catch(error){results.push({id,status:error.name==='TimeoutError'?'ERROR':'FAIL',detail:error.message,ms:Date.now()-start});}}
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),
  args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const context=await browser.newContext();context.setDefaultTimeout(5000);
 try{
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  step('chat load');await page.goto(process.env.TEST_BASE_URL+'/');
  await page.waitForFunction(()=>!busy&&!!runtimeId);step('chat ready');
  await page.evaluate(()=>clearInterval(pollTimer));
  async function value(row,kind){await page.evaluate(r=>addBot(r),row);return await page.locator('#log .row.bot').last().locator('.kv').filter({hasText:kind}).innerText();}
  await check('UI-01',async()=>{const text=await value(payload.alert,'신뢰도');assert.match(text,/미확인/);return text;});
  await check('UI-05',async()=>{const row=structuredClone(payload.alert);row.card.data.confidence=.73;
   const text=await value(row,'신뢰도');assert.match(text,/73%/);return text;});
  await check('UI-02',async()=>{const text=await value(payload.car,'들어온 시간');assert.doesNotMatch(text,/09:00|Invalid Date/);assert.match(text,/미확인|없음|[-—]/);return text;});
  await check('UI-06',async()=>{const row=structuredClone(payload.car);row.card.data.entry_at='2026-11-04T12:34:00+09:00';
   const text=await value(row,'들어온 시간');assert.match(text,/12:34/);return text;});
  await check('UI-07',async()=>{const ids=await page.evaluate(p=>{applyState({...p,runtime_id:runtimeId,state_version:stateVersion+1});
   return [...document.querySelectorAll('#chips [data-chip]')].map(x=>x.dataset.chip);},payload.pending);
   assert.ok(ids.includes('confirm_yes')&&ids.includes('confirm_no'));assert.equal(payload.payment_records_before_confirm,0);return ids;});
  await check('UI-04',async()=>{const result=await page.evaluate(p=>{const id=runtimeId;
   applyState({...p.pending,runtime_id:id,state_version:stateVersion+1});
   applyState({...p.busy,runtime_id:id,state_version:stateVersion});
   applyState({...p.poll,runtime_id:id,state_version:stateVersion+1});
   return [...document.querySelectorAll('#chips [data-chip]')].map(x=>x.dataset.chip);},payload);
   assert.ok(result.includes('confirm_yes'),'서버 확인 대기 중 혼잡 뒤 확인 버튼 보존: '+JSON.stringify(result));return result;});
  await context.request.post(process.env.TEST_BASE_URL+'/api/v1/chat/demo/alert',{data:{level:2,lot_id:'A',spot_no:5}});
  step('admin load');const admin=await context.newPage();admin.on('pageerror',error=>errors.push(error.message));
  await admin.goto(process.env.TEST_BASE_URL+'/admin.html');await admin.locator('#notes').waitFor();
  step('admin ready');await admin.locator('#notes').fill('인수검사 미저장 초안');await admin.waitForFunction(()=>!loading&&!saving);
  const snapshot=await (await context.request.get(process.env.TEST_BASE_URL+'/api/v1/chat/incidents')).json(),report=snapshot.items[0];
  const changed=await context.request.post(process.env.TEST_BASE_URL+`/api/v1/chat/incidents/${report.id}/check`,
   {data:{runtime_id:snapshot.runtime_id,expected_revision:report.revision,index:0,checked:true}});
  assert.equal(changed.status(),200);await admin.evaluate(()=>load());
  await check('UI-08',async()=>{const text=await admin.locator('#notes').inputValue();assert.equal(text,'인수검사 미저장 초안');return text;});
  await check('UI-03',async()=>{const model=await admin.evaluate(()=>!!items.find(x=>x.id===selected).checklist[0].done_at);
   assert.ok(model);const checked=await admin.locator('input[data-i="0"]').isChecked();assert.equal(checked,true,'서버 체크 완료를 화면에도 반영');return {model,checked};});
 }finally{await context.close();await browser.close();}
 fs.writeFileSync(process.argv[3],JSON.stringify({results,page_errors:errors},null,2),'utf8');
 console.log(JSON.stringify({results,page_errors:errors},null,2));
 process.exitCode=results.some(x=>x.status!=='PASS')||errors.length?1:0;
})().catch(error=>{console.error(error.message);process.exit(2);});
