const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const context=await browser.newContext({viewport:{width:390,height:844}}),base=process.env.TEST_BASE_URL,api=base+'/api/v1/chat';
 const page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const calls=async()=>((await(await context.request.get(api+'/admin/calls')).json()).items);
 try{
  await context.request.post(api+'/demo/reset');
  await page.goto(base);await page.waitForFunction(()=>!busy&&!!runtimeId);
  await page.evaluate(()=>clearInterval(pollTimer));
  await page.evaluate(()=>send({text:'자동차 사용료를 산출해 줘'},'자동차 사용료를 산출해 줘'));
  let ids=await page.locator('#chips [data-chip]').evaluateAll(nodes=>nodes.map(node=>node.dataset.chip));
  assert.deepEqual(ids.slice(0,3),['fee','pay','fee_rule']);
  assert.equal((await calls()).length,0);
  await page.locator('#chips [data-chip="pay"]').click();await page.waitForFunction(()=>!busy);
  assert.ok((await page.locator('.row.bot').last().innerText()).includes('차량 번호'));
  await page.evaluate(()=>send({text:'33'},'33'));
  await page.locator('#chips [data-chip="confirm_yes"]').click();await page.waitForFunction(()=>!busy);
  assert.equal(await page.locator('#chips [data-chip="confirm_yes"]').innerText(),'모의 결제하기');
  assert.equal((await calls()).length,0);
  assert.deepEqual(errors,[]);
  console.log('PASS optional hints render existing fee/payment chips and retain vehicle/amount confirmation');
 }finally{await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
