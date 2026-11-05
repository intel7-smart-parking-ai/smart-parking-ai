// 정리본 화면 검사 (기본 설정: 문의 상태 추적 꺼짐)
// 테스트 패널 버튼만으로 최종 수정 지시서의 시연 순서가 끝까지 진행되는지 확인한다.
const{chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const ctx=await browser.newContext({viewport:{width:390,height:844}}),base=process.env.TEST_BASE_URL,api=base+'/api/v1/chat',errors=[];
 const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));
 const chipIds=async()=>p.locator('#chips [data-chip]').evaluateAll(n=>n.map(x=>x.dataset.chip));
 async function send(text){await p.evaluate(t=>send({text:t}),text);await p.waitForFunction(()=>!busy);}
 async function poll(){await p.evaluate(()=>poll());await p.waitForTimeout(150);}
 async function panel(){if(!(await p.locator('#panel.open').count())){await p.click('#panelBtn');}await p.waitForTimeout(200);}
 async function closePanel(){if(await p.locator('#panel.open').count())await p.click('#closePanel');}
 try{
  await ctx.request.post(api+'/demo/reset');await p.goto(base);await p.waitForFunction(()=>!busy&&!!runtimeId);await p.evaluate(()=>clearInterval(pollTimer));

  // 칩 정리
  await send('부산역주차장 자리 있어?');
  let ids=await chipIds();
  assert.ok(ids.some(x=>x.startsWith('go:')),ids);assert.ok(!ids.some(x=>x.startsWith('wait')),ids);assert.ok(!ids.includes('destination_cancel'),ids);
  console.log('PASS full lot offers alternatives only (no wait chip, no destination cancel)');

  // 시연 순서: 목적지 D → 패널에서 D 만차 → 대안 C → 패널에서 C 운영 제한 → 대안 → 선택 차량 입차로 종료
  await send('D로 안내해 줘');
  assert.ok((await p.locator('#destinationContext').innerText()).includes('신세계백화점주차장'));
  await panel();await p.selectOption('#dLot','D');await p.click('#fillLot');await p.waitForTimeout(250);await closePanel();await poll();
  const full=p.locator('.row.bot').filter({hasText:'신세계백화점주차장이 만차가 됐어요'}).last();await full.waitFor();
  assert.ok(!(await full.locator('[data-chip^="wait"]').count()));
  await full.locator('.event-actions [data-chip="go:C"]').click();await p.waitForFunction(()=>!busy);
  assert.ok((await p.locator('#destinationContext').innerText()).includes('북항주차장'));
  console.log('PASS panel fill makes destination full and alternative C can be chosen');
  await panel();await p.selectOption('#dLot','C');await p.fill('#restrictReason','점검 중');await p.click('#restrictOn');await p.waitForTimeout(250);
  assert.ok((await p.locator('#demoState').innerText()).includes('C: 운영 제한 (점검 중)'));await closePanel();await poll();
  const restricted=p.locator('.row.bot').filter({hasText:'북항주차장을 지금 이용할 수 없게 됐어요. (점검 중)'}).last();await restricted.waitFor();
  console.log('PASS panel restriction notifies with plain wording');
  await panel();await p.click('#restrictOff');await p.fill('#enterVehicle','14');await p.selectOption('#dLot','A');
  await send('14번 차 위치');await send('네');
  await p.click('#enterVehicleBtn');await p.waitForTimeout(250);await closePanel();await poll();
  assert.equal(await p.locator('#destinationContext').isHidden(),true);
  console.log('PASS selected vehicle entry ends destination monitoring');
  await send('내 이용 현황 보여줘');assert.ok((await p.locator('.row.bot').last().innerText()).includes('14번 차(장애인 차량)는 개발원주차장 A-04'));
  console.log('PASS usage question returns car location');

  // 관리자 호출: 상태 버튼 없음, 중복 호출 통합, 상태 질문은 새 호출 없음
  await send('관리자 불러줘');await send('관리자 불러줘');
  assert.ok((await p.locator('.row.bot').last().innerText()).includes('이미 접수된 문의'));
  await send('관리자 언제 오나요');
  assert.ok((await p.locator('.row.bot').last().innerText()).includes('현장 관리자에게 확인'));
  ids=await chipIds();assert.ok(!ids.includes('call_status'),ids);
  const admin=await ctx.newPage();admin.on('pageerror',e=>errors.push('admin:'+e.message));
  await admin.goto(base+'/admin.html');await admin.waitForSelector('#calls .call');
  assert.equal(await admin.locator('#calls .call').count(),1);
  assert.equal(await admin.locator('[data-call]').count(),0);
  console.log('PASS admin call merges, status question creates no call, admin page has no status buttons');

  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);console.log('PASS no browser runtime errors in final cleanup scenarios');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
