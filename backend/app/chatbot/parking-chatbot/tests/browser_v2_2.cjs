const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const base=process.env.TEST_BASE_URL,api=base+'/api/v1/chat';
 const context=await browser.newContext({viewport:{width:390,height:844}});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const calls=async()=>((await(await context.request.get(api+'/admin/calls')).json()).items);
 async function ready(){await context.request.post(api+'/demo/reset');await page.goto(base);await page.waitForFunction(()=>!busy&&!!runtimeId);await page.evaluate(()=>clearInterval(pollTimer));}
 try{
  await ready();await page.evaluate(()=>send({text:'뿌잉'},'뿌잉'));
  assert.equal((await calls()).length,0);
  assert.ok((await page.locator('.row.bot').last().innerText()).includes('관리자를 부를까요?'));
  assert.equal(await page.locator('#chips [data-chip="call_admin"]').count(),1);
  assert.equal(await page.locator('#chips [data-chip="confirm_yes"]').count(),0);
  console.log('PASS LLM call proposal renders existing manager chip with zero receipts');
  await page.locator('#chips [data-chip="call_admin"]').click();await page.waitForFunction(()=>!busy);
  assert.equal((await calls()).length,1);
  assert.ok((await page.locator('.row.bot').last().innerText()).includes('등록했어요'));
  console.log('PASS explicit manager chip creates one receipt');
  await ready();await page.evaluate(()=>send({text:'관리자 불러줘'},'관리자 불러줘'));
  assert.equal((await calls()).length,1);
  assert.ok(!(await page.locator('.row.bot').last().innerText()).includes('관리자를 부를까요?'));
  console.log('PASS rule-based manager request still registers immediately');
  assert.deepEqual(errors,[]);console.log('PASS no browser runtime errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
