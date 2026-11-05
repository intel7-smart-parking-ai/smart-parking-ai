const{chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const ctx=await browser.newContext({viewport:{width:390,height:844}}),base=process.env.TEST_BASE_URL,api=base+'/api/v1/chat',errors=[];
 async function user(){const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));await p.goto(base);await p.waitForFunction(()=>!busy&&!!runtimeId);await p.evaluate(()=>clearInterval(pollTimer));return p;}
 try{
  await ctx.request.post(api+'/demo/reset');const p=await user();
  await p.evaluate(()=>send({chip_id:'call_admin'}));
  await p.evaluate(()=>send({chip_id:'call_status'}));
  assert.ok((await p.locator('.row.bot').last().innerText()).includes('현장 관리자에게 확인'));
  const calls=(await(await ctx.request.get(api+'/admin/calls')).json()).items;
  assert.equal(calls.length,1);assert.equal(calls[0].status,'접수');
  console.log('PASS receipt lookup returns card without creating a new call');
  const p2=await user();
  await Promise.all([p.evaluate(async()=>{await send({text:'33번 차 위치'});await send({text:'네'});}),p2.evaluate(async()=>{await send({text:'43번 차 위치'});await send({text:'네'});})]);
  await Promise.all([p.evaluate(()=>send({chip_id:'find_car'})),p2.evaluate(()=>send({chip_id:'find_car'}))]);
  assert.ok((await p.locator('#vehicleContext').innerText()).includes('33번 차'));
  assert.ok((await p2.locator('#vehicleContext').innerText()).includes('43번 차'));
  assert.ok((await p.locator('.row.bot').last().innerText()).includes('개발원주차장 B-01'));
  assert.ok((await p2.locator('.row.bot').last().innerText()).includes('개발원주차장 B-02'));
  console.log('PASS independent users keep their own vehicle cards during parallel reads');
  assert.deepEqual(errors,[]);console.log('PASS no browser runtime errors in optimization checks');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
