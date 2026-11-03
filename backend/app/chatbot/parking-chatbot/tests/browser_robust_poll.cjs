const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage({viewport:{width:360,height:800}});
  await page.clock.install();
  await page.goto(process.env.TEST_BASE_URL);
  await page.locator('.row.bot').first().waitFor();
  await page.waitForFunction(()=>!busy && !!runtimeId);
  await page.clock.pauseAt(new Date(Date.now()+1000));
  const client=await page.context().newCDPSession(page);
  await client.send('Performance.enable');
  await client.send('HeapProfiler.collectGarbage');
  const heap=async()=>((await client.send('Performance.getMetrics')).metrics.find(x=>x.name==='JSHeapUsedSize').value);
  const before=await heap();
  let requests=0;page.on('request',r=>{if(r.url().includes('/updates?'))requests++;});
  for(let i=0;i<900;i++){
   await page.clock.runFor(2000);
   await page.evaluate(async()=>{if(pollTask)await pollTask;});
  }
  await client.send('HeapProfiler.collectGarbage');
  const after=await heap();
  assert.ok(requests>=899 && requests<=902,String(requests));
  assert.equal(await page.evaluate(()=>pollTask!==null),false);
  assert.ok(after-before<2000000,String(after-before));
  fs.mkdirSync('.validation/robustness',{recursive:true});
  fs.writeFileSync('.validation/robustness/browser_poll.json',JSON.stringify({virtual_minutes:30,requests,heap_before:before,heap_after:after,real_30_minutes:false},null,2),{encoding:'utf-8'});
  console.log('PASS 900 virtual polling intervals keep one in-flight request and bounded observed heap');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
