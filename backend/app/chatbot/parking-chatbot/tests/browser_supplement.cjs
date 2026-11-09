const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const base=process.env.TEST_BASE_URL,api=base+'/api/v1/chat';
 const ctx=await browser.newContext({viewport:{width:390,height:844},permissions:['clipboard-read','clipboard-write']}),errors=[];
 const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
 async function send(text){await p.evaluate(text=>send({text}),text);}
 try{
  await ctx.request.post(api+'/demo/reset');await p.goto(base);await p.waitForFunction(()=>!busy&&!!runtimeId);await p.evaluate(()=>clearInterval(pollTimer));
  await p.click('#panelBtn');await p.selectOption('#dLot','D');await p.fill('#uncertainSpot','1');await p.click('#uncertainOn');
  await p.waitForTimeout(150);await p.click('#closePanel');await send('D 빈자리');
  assert.ok((await p.locator('.row.bot').last().innerText()).includes('확인 중 1'));
  assert.equal((await(await ctx.request.get(api+'/config')).json()).lots.length,4);
  console.log('PASS uncertain demo button and separate count render');
  await ctx.request.post(api+'/demo/uncertain',{data:{lot_id:'D',spot_no:1,uncertain:false}});
  await p.click('#panelBtn');await p.click('#unrecognizedEnter');await p.waitForTimeout(150);await p.click('#closePanel');
  await send('99번 차 위치');
  const candidate=p.locator('#chips [data-chip^="candidate:"]');assert.equal(await candidate.count(),1);await candidate.click();await p.waitForFunction(()=>!busy);
  assert.ok((await p.locator('.row.bot').last().innerText()).includes('후보 위치'));
  await send('결제할게요');assert.ok((await p.locator('.row.bot').last().innerText()).includes('관리자 확인 후'));
  assert.equal(await p.locator('#chips [data-chip="confirm_yes"]').count(),0);
  console.log('PASS unrecognized location candidate cannot enter payment confirmation');
  await p.evaluate(()=>send({chip_id:'candidate_admin'}));
  const calls=(await(await ctx.request.get(api+'/admin/calls')).json()).items;
  assert.ok(calls[0].summary.includes('번호 미인식 후보 확인 요청(신세계백화점주차장 '));
  console.log('PASS candidate context is attached to manager receipt');
  await ctx.request.post(api+'/demo/alert',{data:{level:2,lot_id:'D',spot_no:1}});await p.evaluate(()=>poll());
  assert.ok((await p.locator('#log').innerText()).includes('밖이면 들어오지'));
  for(const id of ['find_car','fee','pay'])assert.equal(await p.locator(`#chips [data-chip="${id}"]`).count(),0);
  console.log('PASS Lv2 guidance carries no reentry and no car retrieval actions');
  const admin=await ctx.newPage();admin.on('pageerror',e=>errors.push(e.message));await admin.goto(base+'/admin.html');await admin.waitForSelector('#copyBriefing');
  await admin.click('#copyBriefing');await admin.waitForFunction(()=>document.querySelector('#briefingCopyState').textContent.includes('복사됨'));
  const copied=await admin.evaluate(()=>navigator.clipboard.readText());
  assert.ok(copied.includes('시연용 모의 상황이며 실제 화재가 아님'));assert.ok(copied.includes('번호 미인식'));assert.ok(copied.includes('미확인'));
  console.log('PASS briefing card copies actual plain text to clipboard');
  assert.equal(await admin.locator('[data-call]').count(),0);assert.deepEqual(errors,[]);
  console.log('PASS no browser runtime errors in supplement scenarios');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
