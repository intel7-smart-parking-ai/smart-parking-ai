const{chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const ctx=await browser.newContext({viewport:{width:390,height:844}}),base=process.env.TEST_BASE_URL,api=base+'/api/v1/chat',errors=[];
 const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
 async function send(text){await p.evaluate(t=>send({text:t}),text);}
 try{
  await ctx.request.post(api+'/demo/reset');await p.goto(base);await p.waitForFunction(()=>!busy&&!!runtimeId);await p.evaluate(()=>clearInterval(pollTimer));
  await send('빈자리');
  const grid=p.locator('.card .lots').last();assert.equal(await grid.locator('.lot').count(),4);
  const positions=await grid.locator('.lot').evaluateAll(nodes=>nodes.map(x=>({x:x.offsetLeft,y:x.offsetTop})));
  assert.equal(positions[0].y,positions[1].y);assert.equal(positions[2].y,positions[3].y);assert.ok(positions[2].y>positions[0].y);
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.ok((await grid.innerText()).includes('신세계백화점주차장 · 가상'));
  console.log('PASS four lots render 2 by 2 with virtual labels and no mobile overflow');
  await send('D 충전 구역 자리');
  assert.ok((await p.locator('.row.bot').last().innerText()).includes('충전'));
  assert.ok((await p.locator('.zone-counts').last().innerText()).includes('충전'));
  console.log('PASS zone counts and requested zone are visible');
  await p.locator('#chips [data-chip="go:D:ev"]').click();await p.waitForFunction(()=>!busy);
  assert.ok((await p.locator('#destinationContext').innerText()).includes('신세계백화점주차장'));
  assert.ok((await p.locator('#destinationContext').innerText()).includes('충전'));
  console.log('PASS destination selection exposes lot zone and virtual kind in pinned context');
  await p.locator('#destinationContext [data-chip="destination_cancel"]').click();await p.waitForFunction(()=>!busy);
  assert.equal(await p.locator('#destinationContext').isHidden(),true);
  await send('A로 갈게');
  await ctx.request.post(api+'/demo/alert',{data:{level:2,lot_id:'A',spot_no:6}});await p.evaluate(()=>poll());
  const destination=p.locator('.row.bot').filter({hasText:'경보가 발생했어요'}).last();await destination.waitFor();
  assert.ok((await destination.innerText()).includes('경보'));
  await destination.locator('.event-actions [data-chip="go:D"]').click();await p.waitForFunction(()=>!busy);
  assert.ok((await p.locator('#destinationContext').innerText()).includes('신세계백화점주차장'));
  assert.ok((await p.locator('#banner').innerText()).includes('개발원주차장'));
  console.log('PASS alarm alternative notice keeps evacuation banner and D selection works');
  const n=await p.locator('.row.bot').filter({hasText:'경보가 발생했어요'}).count();await p.evaluate(()=>poll());
  assert.equal(await p.locator('.row.bot').filter({hasText:'경보가 발생했어요'}).count(),n);
  console.log('PASS unchanged destination problem does not duplicate notification');
  await ctx.request.post(api+'/demo/alert',{data:{level:0}});await p.evaluate(()=>poll());
  await send('17번 차 위치');await send('네');
  assert.equal(await p.locator('#destinationContext').isHidden(),true);
  console.log('PASS parked vehicle ends destination monitoring');
  assert.deepEqual(errors,[]);console.log('PASS no browser runtime errors in new flows');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
