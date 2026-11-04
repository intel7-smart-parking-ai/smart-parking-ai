const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 try{
  const page=await browser.newPage({viewport:{width:360,height:800}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.TEST_BASE_URL);
  await page.waitForFunction(()=>!busy && !!runtimeId);
  for(const lot of ['A',null]){
   const text=await page.evaluate(lot=>{
    addBot({text:'위치 미상 경보',updated_at:'2026-11-03T14:02:00+09:00',card:{type:'alert',data:{api_result:true,level:2,lot_id:lot,spot_no:null,started_at:'broken'}}});
    return document.querySelector('.row.bot:last-child').innerText;
   },lot);
   assert.ok(text.includes(lot===null?'주차장 위치 확인 중':'정확한 칸 확인 중'));
   assert.ok(!text.includes('null')&&!text.includes('번 칸')&&!text.includes('Invalid Date'),text);
  }
  console.log('PASS nullable API alarm cards render without invented spots or invalid dates');
  const titles=await page.evaluate(()=>({api:renderCard({type:'alert',data:{api_result:true,level:1,lot_id:'A',spot_no:1}}),mock:renderCard({type:'alert',data:{level:1,lot_id:'A',spot_no:1}})}));
  assert.ok(titles.api.includes('<h3>경보'));assert.ok(titles.mock.includes('<h3>확인 중'));
  console.log('PASS API Lv1 alarm wording and existing mock Lv1 wording stay separate');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  console.log('PASS nullable alarm cards fit mobile viewport');
  await page.goto(process.env.TEST_BASE_URL+'/admin.html');
  await page.waitForFunction(()=>typeof incidentLocation==='function');
  assert.equal(await page.evaluate(()=>incidentLocation({lot_id:null,spot_no:null})),'주차장 위치 확인 중');
  assert.equal(await page.evaluate(()=>incidentLocation({lot_id:'A',spot_no:null})),'개발원주차장(정확한 칸 확인 중)');
  console.log('PASS admin incident locations handle unknown lots and spots');
  assert.deepEqual(errors,[]);console.log('PASS no JavaScript runtime errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
