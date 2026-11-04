// 느린 패널 갱신·응답 역전·빈 차량 입력에 대한 브라우저 회귀 검사.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const ctx=await browser.newContext({viewport:{width:390,height:844}}),p=await ctx.newPage(),base=process.env.TEST_BASE_URL,api=base+'/api/v1/chat',errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 try{
  await ctx.request.post(api+'/demo/reset');await p.goto(base);await p.waitForFunction(()=>!busy&&runtimeId);
  await p.evaluate(()=>clearInterval(pollTimer));await p.click('#panelBtn');await p.evaluate(()=>refreshPanel());
  await p.selectOption('#exitCar','17');
  async function delayedRefresh(){
   let release,started;const gate=new Promise(r=>release=r),ready=new Promise(r=>started=r);
   await p.route('**/api/v1/chat/demo/cars',async route=>{
    const response=await route.fetch();started();await gate;await route.fulfill({response});
   },{times:1});
   const task=p.evaluate(()=>refreshPanel());await ready;return {release,task};
  }
  let pending=await delayedRefresh();pending.release();await pending.task;
  assert.equal(await p.locator('#exitCar').inputValue(),'17');
  console.log('PASS delayed refresh preserves selected vehicle');

  pending=await delayedRefresh();await p.selectOption('#exitCar','18');pending.release();await pending.task;
  assert.equal(await p.locator('#exitCar').inputValue(),'18');
  console.log('PASS selection made during refresh is preserved');

  await p.selectOption('#exitCar','17');await p.click('#exitBtn');
  await p.waitForFunction(()=>!document.querySelector('#exitCar option[value="17"]'));
  const cars=(await (await ctx.request.get(api+'/demo/cars')).json()).items;
  assert.ok(cars.some(c=>c.car_id==='11'));assert.ok(!cars.some(c=>c.car_id==='17'));
  assert.equal(await p.locator('#exitCar').inputValue(),'');assert.equal(await p.locator('#exitBtn').isDisabled(),true);
  console.log('PASS exit removes only selected car and requires new selection');

  await p.selectOption('#exitCar','18');pending=await delayedRefresh();
  await ctx.request.post(api+'/demo/exit',{data:{car_id:'18'}});await p.evaluate(()=>refreshPanel());
  pending.release();await pending.task;
  assert.equal(await p.locator('#exitCar option[value="18"]').count(),0);
  assert.equal(await p.locator('#exitCar').inputValue(),'');assert.equal(await p.locator('#exitBtn').isDisabled(),true);
  console.log('PASS old response cannot restore removed car or overwrite newer list');

  let entries=0;p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/demo/enter'))entries++;});
  await p.selectOption('#dLot','D');await p.fill('#enterVehicle','');await p.click('#enterVehicleBtn');
  assert.ok((await p.locator('#demoState').innerText()).includes('차량 번호를 입력'));
  assert.equal(entries,0);assert.deepEqual((await (await ctx.request.get(api+'/demo/cars')).json()).demo_occupied,{});
  await p.click('#enterOne');await p.waitForFunction(()=>document.querySelector('#demoState').textContent.includes('D: 시연용 점유 1칸'));
  assert.equal(entries,1);
  console.log('PASS empty selected entry changes no data while anonymous entry still works');
  assert.deepEqual(errors,[]);console.log('PASS no browser runtime errors in audit regression scenarios');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
