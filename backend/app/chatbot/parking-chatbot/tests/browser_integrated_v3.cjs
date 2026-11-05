const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const base=process.env.TEST_BASE_URL,api=base+'/api/v1/chat';
 const ctx=await browser.newContext({viewport:{width:390,height:844}}),p=await ctx.newPage(),errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 async function say(text){
  const next=p.waitForResponse(r=>r.url()===api&&r.request().method()==='POST');
  await p.evaluate(text=>send({text},text),text);
  return (await next).json();
 }
 try{
  await ctx.request.post(api+'/demo/reset');await p.goto(base);await p.waitForFunction(()=>!busy&&!!runtimeId);await p.evaluate(()=>clearInterval(pollTimer));
  const declined=await say('직원분 안 오셔도 될 것 같아요');
  assert.equal(declined.meta.intent,'cancel_action');assert.equal(declined.meta.source,'rule');
  assert.equal((await(await ctx.request.get(api+'/admin/calls')).json()).items.length,0);
  assert.ok((await p.locator('.row.bot').last().innerText()).includes('새 관리자 호출은 하지 않을게요'));
  console.log('PASS admin refusal creates no receipt and renders refusal notice');
  await say('33번 결제');const quote=await say('네');assert.equal(quote.flow,'confirm_action');
  const later=await say('돈은 이따 낼게요');assert.equal(later.flow,'idle');assert.equal(later.confirmation_token,null);
  assert.equal(await p.locator('#chips [data-chip="confirm_yes"]').count(),0);
  await say('네');const fee=await say('요금 얼마야?');assert.equal(fee.card.data.paid,false);
  console.log('PASS later payment cancels quote and never charges on a later yes');
  const issue=await say('돈이 두 번 빠져나간 것 같아');assert.equal(issue.meta.intent,'payment_issue');
  assert.equal(issue.confirmation_token,null);assert.ok((await p.locator('.row.bot').last().innerText()).includes('결제 오류 문의'));
  console.log('PASS duplicate debit question renders payment issue guidance');
  const empty=await say('휠체어 마크 있는 칸 비었어?');assert.equal(empty.card.data.zone,'accessible');
  assert.ok((await p.locator('.row.bot').last().innerText()).includes('장애인'));
  console.log('PASS wheelchair marker filters disabled spaces in actual card');
  assert.deepEqual(errors,[]);console.log('PASS no browser runtime errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
