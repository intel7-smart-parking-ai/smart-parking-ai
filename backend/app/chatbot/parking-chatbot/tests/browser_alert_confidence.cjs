const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const context=await browser.newContext(),base=process.env.TEST_BASE_URL,errors=[];
 let confidence=null;
 try{
  await context.request.post(base+'/api/v1/chat/demo/reset');
  await context.request.post(base+'/api/v1/chat/demo/alert',{data:{level:2,lot_id:'A',spot_no:5}});
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/v1/chat/incidents',async route=>{
   const response=await route.fetch(),data=await response.json();
   data.items.forEach(item=>{item.max_confidence=confidence;});
   await route.fulfill({response,json:data});
  });
  await page.goto(base+'/admin.html');
  const display=page.locator('.kpi').filter({hasText:'최대 신뢰도'}).locator('b');
  await display.waitFor();
  assert.equal(await display.innerText(),'미확인');
  confidence=0.73;
  await page.evaluate(()=>load());
  assert.equal(await display.innerText(),'73%');
  assert.deepEqual(errors,[]);
  console.log('PASS null confidence renders as unknown, then updates to 73% without runtime errors');
 }finally{await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
