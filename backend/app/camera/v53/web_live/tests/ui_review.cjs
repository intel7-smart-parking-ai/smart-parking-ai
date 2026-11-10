// Tests the shipped UI script with deterministic DOM, clock and HTTP stand-ins.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements=new Map(),calls=[],bodies=[];let failed=false,clock=1000,watchdog,replyDelay=0;
const get=id=>{if(!elements.has(id))elements.set(id,{hidden:false,textContent:'',innerHTML:'',style:{setProperty(){}},
  before(){},replaceChildren(){},appendChild(){},decode(){return Promise.resolve()},insertAdjacentHTML(){},removeAttribute(key){delete this[key]},click(){if(!this.disabled)return this.onclick?.()}});return elements.get(id)};
const result={lot_id:'A',stream_epoch:3,frame_sequence:55,observed_at:'2026-11-07T03:00:00+00:00',total_ms:350,registration_ok:true,roi_confirmed:true,empty_reference:true,note:'',
  image_size:[1920,1080],annotated_image:'data:image/jpeg;base64,mock',spaces:[{space_id:'A-01',zone_type:'general',observation:'occupied',
  car_id:'23',vehicle_type:'ev',confidence:.9,sendable:true}],detections:[]};
const reply=value=>({ok:true,json:async()=>value});
const document={getElementById:get,createElement:()=>({style:{}}),
  addEventListener(type,handler,capture){assert.equal(capture,true);this['on'+type]=handler}};
class Clock extends Date {static now(){return clock}}
const context=vm.createContext({document,console,Image:class {decode(){return Promise.resolve();}},window:{addEventListener(){}},Date:Clock,AbortController,structuredClone,
  setTimeout:()=>0,clearTimeout(){},setInterval:fn=>{watchdog=fn;return 0},
  fetch:async(url,options)=>{
    calls.push(url);
    bodies.push({url,body:options?.body&&JSON.parse(options.body)});
    if(url==='/health')return reply({ready:true,api_configured:true});
    if(url.startsWith('/live/state?images=')){
      if(failed)throw new Error('mock offline');
      clock+=replyDelay;
      const entry={key:'session.3.55',saved:1,failed:0};
      if(!url.includes('seen1=session.3.55'))entry.result=result;
      return reply({running:true,sending:true,results:{'1':result},collection:{session:'session',channels:{1:entry}},cameras:{'1':'live','2':'live'},errors:{},ocr_status:'ready',
        transport:{last_status:'accepted',counts:{accepted:28}}});
    }
    if(url==='/replay/A')return new Promise((resolve,reject)=>{
      options.signal.addEventListener('abort',()=>{const e=new Error('aborted');e.name='AbortError';reject(e)},{once:true});
    });
    if(url==='/live/stop'||url==='/live/send'||url==='/live/confirm-roi/1'||url==='/live/empty/1')return reply({ok:true});
    throw new Error('Unexpected request '+url);
  }
});
(async()=>{
  vm.runInContext(script,context);await new Promise(setImmediate);
  assert.doesNotMatch(html,/<canvas|\/preview\.js|\/live\/preview\//);
  assert.match(get('plane1').style.transform,/rotate\(0deg\)/);
  get('turn1').click();
  assert.match(get('plane1').style.transform,/rotate\(90deg\)/);
  assert.equal(get('plane1').style.width,'56.25%');
  console.log('PASS collected image rotates without a raw camera view or preview request');
  context.fullLot={lot_id:'B',spaces:['A','B'].flatMap(group=>Array.from({length:8},(_,i)=>({
    ...result.spaces[0],space_id:group+'-0'+(i+1),reason:'긴 검토 사유 <가림> '+group+(i+1)
  })))};
  const allRows=vm.runInContext('table(fullLot)',context);
  assert.equal((allRows.match(/<tbody>/g)||[]).length,2);
  assert.equal((allRows.match(/data-field="사유"/g)||[]).length,16);
  assert.match(allRows,/B-08[\s\S]*긴 검토 사유 &lt;가림&gt; B8/);
  console.log('PASS both bay groups keep every numbered row and complete escaped review reason');
  assert.ok(html.indexOf('id="cameras"')<html.indexOf('id="sheets"'));
  const parkingText=html=>html.match(/<td data-field="주차 상태"[^>]*>([\s\S]*?)<\/td>/)[1].replace(/<[^>]*>/g,'');
  context.approved={lot_id:'A',registration_ok:true,roi_confirmed:true};
  context.beforeApproval={...context.approved,roi_confirmed:false};
  const paper={general:'white',ev:'green',accessible:'blue'};
  for(const zone of ['general','ev','accessible']) for(const type of ['general','ev','accessible']){
    context.bay={space_id:'A-01',observation:'occupied',zone_type:zone,vehicle_type:type,
      sticker_color:paper[type],sticker_bbox:[5,5,25,25],car_id:null,confidence:0.9};
    const allowed={general:['general','ev','accessible'],ev:['ev'],accessible:['accessible']}[zone].includes(type);
    const label=allowed?'정상 주차':'비정상 주차';
    for(const registered of [true,false]){
      context.board=registered?context.approved:context.beforeApproval;
      context.bay.parking_rule_status=registered?(allowed?'matched':'mismatch'):'unverified';
      context.bay.sendable=registered;
      for(const [fire,suffix] of [[{state:'confirmed',on_vehicle:true},'(모의 화재)'],
          [{state:'confirmed',on_vehicle:false},''],[{state:'candidate',on_vehicle:true},''],[{state:'candidate',method:'red_emission',on_vehicle:true},'(모의 화재)'],[{state:'off'},''],[undefined,'']]){
        context.bay.fire_led=fire;
        const row=vm.runInContext('bayRow(bay,board)',context);
        assert.equal(parkingText(row),label+suffix);
        assert.doesNotMatch(row,/소등|미판정|오주차 의심/);
      }
    }
  }
  for(const [row,expected] of [
    [{observation:'empty',sendable:true,empty_evidence_ok:true},'빈자리'],
    [{observation:'empty',sendable:false,empty_evidence_ok:true},'—'],
    [{observation:'unknown',model_observation:'empty'},'—'],
    [{observation:'unknown',model_observation:'empty',display_occupancy:'empty'},'빈자리'],
    [{observation:'unknown',model_observation:'occupied',display_occupancy:'occupied'},'차량 있음'],
    [{observation:'unknown',model_observation:'occupied',display_occupancy:'empty'},'—'],
    [{observation:'unknown',model_observation:'empty',display_occupancy:'empty',quality_reason:'가림'},'—']]){
    context.bay=row;const out=vm.runInContext('bayRow(bay,approved)',context);
    assert.equal(parkingText(out),'-');
    assert.equal(out.match(/<td data-field="차량 유무"[^>]*><span[^>]*>([^<]+)<\/span>/)[1],expected);
  }
  vm.runInContext("showTotal({1:{...beforeApproval,spaces:[{observation:'unknown',model_observation:'empty',display_occupancy:'empty'}]}})",context);
  assert.equal(get('emptyCount').textContent,'1칸');
  assert.equal(get('unknownCount').textContent,'0칸');
  console.log('PASS 9 parking combinations, unapproved location and held vacancy');
  context.previewRow={space_id:'B-06',observation:'occupied',car_id:'42',sticker_color:'green',vehicle_type:'ev',
    zone_type:'accessible',sticker_bbox:[1,2,11,22],confidence:.91,parking_rule_status:'unverified'};
  const preview=vm.runInContext("bayRow(previewRow,beforeApproval,'camera2')",context);
  assert.equal(parkingText(preview),'비정상 주차');assert.match(preview,/role="tooltip" id="reason-camera2-B-06"/);
  for(const extra of [{confidence:.79},{quality_reason:'가림'},{sticker_color:'blue'},{sticker_bbox:null},{vehicle_type:null}]){
    context.changed={...context.previewRow,...extra,fire_led:{state:'confirmed',on_vehicle:true}};
    const row=vm.runInContext('bayRow(changed,beforeApproval)',context);
    assert.equal(parkingText(row),'-');assert.match(row,/모의 화재/);
  }
  assert.equal(parkingText(vm.runInContext("bayRow(previewRow,{...beforeApproval,candidates:[{kind:'sticker_multiple_spaces',spaces:['B-06']}]})",context)),'-');
  assert.equal(parkingText(vm.runInContext('bayRow(previewRow,null)',context)),'-');
  assert.equal(parkingText(vm.runInContext('table({...beforeApproval,spaces:[previewRow]})',context)),'비정상 주차');
  context.provisional={lot_id:'A',registration_ok:true,roi_confirmed:false,spaces:[context.previewRow]};
  vm.runInContext('showTotal({2:provisional})',context);
  assert.equal(get('wrongCount').textContent,'1칸');assert.equal(get('parkingIssues').hidden,true);
  vm.runInContext('showTotal({1:provisional,2:provisional})',context);assert.equal(get('wrongCount').textContent,'-');
  console.log('PASS three labels, nine zone/type combinations, vehicle fire suffix, replay and approved-only warning');
  const ledRow={space_id:'A-02',fire_led:{state:'confirmed'}};
  context.fireRows={1:{lot_id:'A',roi_confirmed:true,spaces:[ledRow]},2:{lot_id:'B',roi_confirmed:true,spaces:[ledRow]}};
  vm.runInContext('showTotal(fireRows)',context);
  assert.equal(get('fireAlert').hidden,false);
  assert.equal(get('fireAlert').textContent,'모의 화재 · A 주차장 A-02, B 주차장 A-02');
  context.fireRows[2].lot_id='A';
  vm.runInContext('showTotal(fireRows)',context);
  assert.equal(get('fireAlert').hidden,true);
  console.log('PASS LED banner disambiguates two lots and hides ambiguous registrations');
  context.aisle={lot_id:'A',roi_confirmed:true,spaces:[],fire_lights:[{state:'confirmed',location:'aisle',space_id:null}]};
  vm.runInContext("showTotal({'2':aisle})",context);
  assert.match(get('fireAlert').textContent,/A 주차장 통로/);
  context.aisle.roi_confirmed=false;
  vm.runInContext("showTotal({'2':aisle})",context);
  assert.equal(get('fireAlert').textContent,'모의 화재 · 카메라 2 · 위치 미확인');
  context.aisle.fire_lights[0].method='red_emission';
  for(const [state,text] of [['candidate','모의 화재 · 카메라 2 · 위치 미확인'],['off',''],[undefined,'']]){
    context.aisle.fire_lights[0].state=state;
    vm.runInContext("showTotal({'2':aisle})",context);
    assert.equal(get('fireAlert').textContent,text);assert.equal(get('fireAlert').hidden,!text);
  }
  context.aisle.roi_confirmed=true;
  context.aisle.spaces=[{space_id:'A-02',car_id:'11',fire_led:{state:'confirmed',on_vehicle:true}}];
  context.aisle.fire_lights=[{state:'confirmed',space_id:'A-02'}];
  vm.runInContext("showTotal({'2':aisle})",context);
  assert.equal(get('fireAlert').textContent,'모의 화재 · A 주차장 A-02 11번');
  console.log('PASS first visible emission and confirmed fire share one caption, hidden off state, no duplicate locations');
  const heldRow=vm.runInContext("table({spaces:[{space_id:'A-02',observation:'occupied',sendable:true,confidence:.9,car_id:null,number_candidate:'23',reason:'점유 확인',number_reason:'장면 내 번호 중복'}]})",context);
  assert.match(heldRow,/미확인.*후보 23/);
  assert.match(heldRow,/점유 확인 · 장면 내 번호 중복/);
  console.log('PASS occupied slot shows the withheld identity and both reasons');
  const eligible={lot_id:'A',roi_confirmed:true,spaces:[{observation:'occupied',sendable:true,car_id:'23',vehicle_type:'ev'}]};
  for(const [patch,expected] of [[{},1],[{roi_confirmed:false},0],[{spaces:[{...eligible.spaces[0],observation:'unknown'}]},0],
    [{spaces:[{...eligible.spaces[0],sendable:false}]},0],[{spaces:[{...eligible.spaces[0],car_id:null}]},0]]){
    context.testRows={...eligible,...patch};
    assert.match(vm.runInContext("showTotal({'1':testRows});total.textContent",context),new RegExp(expected+'대 /'));
  }
  console.log('PASS total excludes unapproved ROI, uncertain bays and withheld IDs');
  context.overlappingBoards={1:eligible,2:{...eligible,roi_confirmed:false}};
  assert.match(vm.runInContext('showTotal(overlappingBoards);total.textContent',context),/0대 \//);
  assert.ok(calls.some(url=>url.includes('images=false&collected=true')));
  context.legacyGeneral={lot_id:'B',roi_confirmed:true,spaces:[{space_id:'A-04',observation:'occupied',vehicle_type:'ev',zone_type:'general',parking_rule_status:'mismatch'}]};
  vm.runInContext('showTotal({1:legacyGeneral})',context);
  assert.equal(get('parkingIssues').hidden,true);
  assert.equal(get('wrongCount').textContent,'0칸');
  assert.equal(parkingText(vm.runInContext('bayRow(legacyGeneral.spaces[0],legacyGeneral)',context)),'정상 주차');
  context.wrong={lot_id:'B',roi_confirmed:true,spaces:[{space_id:'B-07',observation:'occupied',vehicle_type:'ev',zone_type:'accessible',parking_rule_status:'mismatch'}]};
  vm.runInContext('showTotal({1:wrong})',context);
  assert.match(get('parkingIssues').textContent,/B 주차장 B-07.*전기차 차량 · 장애인 칸/);
  vm.runInContext('showTotal({1:wrong,2:wrong})',context);assert.equal(get('parkingIssues').hidden,true);
  assert.equal(get('image1').hidden,false);
  await vm.runInContext('poll()',context);await new Promise(setImmediate);
  assert.ok(calls.some(url=>url.includes('seen1=session.3.55')));
  get('confirm1').click();await new Promise(setImmediate);
  get('empty1').click();await new Promise(setImmediate);
  const token={epoch:3,sequence:55,observed_at:result.observed_at,lot_id:'A'};
  assert.deepEqual(bodies.find(r=>r.url==='/live/confirm-roi/1').body,{reviewed:token});
  assert.deepEqual(bodies.find(r=>r.url==='/live/empty/1').body,{visually_empty:true,reviewed:token});
  console.log('PASS ROI and empty approvals carry the displayed frame identity');
  const pending=get('sampleA').click();
  document.onkeydown({code:'Escape',repeat:false,preventDefault(){},target:{tagName:'BODY'}});
  await new Promise(setImmediate);await pending;
  assert.equal(calls.filter(p=>p==='/live/stop').length,1);
  console.log('PASS Escape dispatches stop while another request is pending');
  const second=get('sampleA').click();await get('sendOff').click();await second;
  assert.equal(calls.filter(p=>p==='/live/send').length,1);
  console.log('PASS send-off cancels a pending request and dispatches independently');
  failed=true;await vm.runInContext('poll()',context);
  assert.equal(get('image1').hidden,false);assert.match(get('frameStatus1').textContent,/마지막 저장 기록/);
  assert.match(get('rows1').innerHTML,/A-01/);assert.equal(get('confirm1').disabled,true);
  assert.equal(get('empty1').disabled,true);assert.equal(vm.runInContext('state',context),null);
  console.log('PASS poll failure preserves last saved photo and blocks approvals');
  failed=false;await vm.runInContext('poll()',context);assert.equal(get('image1').hidden,false);
  clock+=3000;watchdog();assert.equal(get('image1').hidden,false);assert.equal(vm.runInContext('state',context),null);
  console.log('PASS stalled polling expires live display without waiting for HTTP completion');
  replyDelay=3000;await vm.runInContext('poll()',context);assert.equal(vm.runInContext('state',context),null);
  replyDelay=0;await vm.runInContext('poll()',context);clock+=3000;
  let saved=[];
  context.Blob=Blob;
  context.URL={createObjectURL:blob=>{saved.push(blob);return 'blob:fixture'},revokeObjectURL(){}};
  document.createElement=()=>({style:{},click(){}});
  get('save').click();
  const historic=JSON.parse(await saved.pop().text());
  assert.equal(historic.live,null);assert.equal(historic.collected['1'].state,'last_saved');
  assert.equal(vm.runInContext('state',context),null);
  console.log('PASS expired observations export only as timestamped last-saved records');
  await vm.runInContext('poll()',context);
  vm.runInContext("collectedFrames[2]={key:'session.2.12',r:{...collectedFrames[1].r,lot_id:'B',frame_sequence:12}}",context);
  get('save').click();
  const both=JSON.parse(await saved.pop().text());
  assert.equal(both.collected['1'].state,'current');assert.equal(both.collected['2'].state,'last_saved');
  assert.equal(both.collected['2'].result.lot_id,'B');assert.deepEqual(Object.keys(both.live.results),['1']);
  assert.ok(!JSON.stringify(both).includes('data:image'));
  console.log('PASS two-channel download distinguishes current and saved records without image data');
  for(const [s,label] of [
    [{running:false,watchdog_stopped:true},/F5/],
    [{running:true,sending:false,results:{1:{roi_confirmed:false}}},/칸 위치 확인 후 전송 켜기/],
    [{running:true,sending:false,results:{1:{roi_confirmed:true}}},/서버 전송 꺼짐/],
    [{running:true,sending:true,results:{}},/새 카메라/],
    [{running:true,sending:true,last_pause_seconds:242,results:{1:{roi_confirmed:false}}},/재확인/],
    [{running:true,sending:true,results:{1:result,2:result}},/전송 가능한 칸 없음/],
    [{running:true,sending:true,results:{1:result}},/전송 켜짐/]]){
    context.statusInput=s;assert.match(vm.runInContext('liveStatus(statusInput)',context),label);
  }
  console.log('PASS watchdog stop, camera wait, resume review, duplicate lot and enabled delivery status');
  // With no cameras a successful poll still returns an object, but no capture.
  vm.runInContext('state={running:false,results:{}}; lastStateAt=Date.now(); replay=null',context);
  get('save').click();assert.equal(saved.length,0,'idle status must not download a measurement');
  context.fetch=async url=>url==='/replay/A'?reply(result):{ok:false,json:async()=>({detail:'bad photo'})};
  await get('sampleA').click();get('save').click();
  assert.equal(saved.length,1);
  const exported=JSON.parse(await saved[0].text());
  assert.equal(exported.live,null);assert.equal(exported.replay.input_name,'A 주차장 예제');
  assert.ok(!('annotated_image' in exported.replay));
  await get('sampleB').click();get('save').click();
  assert.equal(saved.length,1,'failed photo must not export previous result');
  assert.equal(vm.runInContext('replay',context),null);
  let finish;
  context.fetch=async url=>url==='/replay/A'?new Promise(resolve=>{finish=resolve}):reply({ok:true});
  const pendingPhoto=get('sampleA').click();await get('stop').click();finish(reply(result));await pendingPhoto;
  assert.equal(vm.runInContext('replay',context),null,'cancelled completion must not restore photo');
  console.log('PASS camera-free save, named photo, failed replacement and cancelled late result');
  const ordered=[];
  let release;
  context.fetch=url=>{ordered.push(url);return new Promise(resolve=>{
    release=()=>resolve(reply({running:url.endsWith('/start'),results:{}}));
  })};
  function key(key,repeat=false){
    let prevented=false;
    document.onkeydown({key,repeat,target:{tagName:'SELECT'},preventDefault(){prevented=true}});
    assert.ok(prevented);
  }
  key('Escape');key('F5');key('F5',true);
  assert.deepEqual(ordered,['/live/stop']);release();await new Promise(setImmediate);
  assert.deepEqual(ordered,['/live/stop','/live/start']);release();await new Promise(setImmediate);
  assert.equal(vm.runInContext('state.running',context),true);
  key('F5');key('Escape');key('Escape',true);release();await new Promise(setImmediate);
  assert.deepEqual(ordered.slice(2),['/live/start','/live/stop']);release();await new Promise(setImmediate);
  assert.equal(vm.runInContext('state.running',context),false);
  console.log('PASS delayed Esc/F5 and F5/Esc both honor the final key with a focused selector and key repeat');
  const decodes=[];
  context.Image=class {decode(){return new Promise((ok,fail)=>decodes.push({key:this.src,ok,fail}))}};
  context.entry=(n)=>({key:String(n),result:{...result,frame_sequence:n,annotated_image:'frame'+n}});
  vm.runInContext("for(let n=1;n<=24;n++){updateReview('1',entry(n));updateReview('2',entry(n+100))}",context);
  assert.deepEqual(decodes.map(d=>d.key),['frame1','frame101']);
  decodes[0].ok();await new Promise(setImmediate);
  assert.equal(get('image1').src,'frame1');assert.equal(decodes[2].key,'frame24');
  decodes[1].fail();await new Promise(setImmediate);
  assert.equal(decodes[3].key,'frame124');
  decodes[2].ok();decodes[3].ok();await new Promise(setImmediate);
  assert.equal(get('image1').src,'frame24');assert.equal(get('image2').src,'frame124');
  vm.runInContext("updateReview('1',entry(25));updateReview('1',entry(26));clearReview('1')",context);
  decodes[4].ok();await new Promise(setImmediate);
  assert.equal(get('image1').hidden,true);assert.equal(decodes.length,5);
  console.log('PASS dual 24-frame burst, failed decode recovery and late frame after session change');
  let deadline;
  context.setTimeout=fn=>{deadline=fn;return 1};
  vm.runInContext("updateReview('1',entry(27));updateReview('1',entry(28))",context);
  deadline();assert.equal(decodes[6].key,'frame28');
  decodes[6].ok();await new Promise(setImmediate);
  decodes[5].ok();await new Promise(setImmediate);
  assert.equal(get('image1').src,'frame28');
  console.log('PASS a stuck decoder expires and its late image cannot replace a newer frame');
  context.fireExample={...result,roi_confirmed:false,spaces:[{...context.previewRow,fire_led:{state:'confirmed',on_vehicle:true}}]};
  vm.runInContext("state={running:true,results:{1:fireExample}}; collectedFrames[1]={key:'test.1',r:fireExample}; delete collectedFrames[2]; reviewStatus('1')",context);
  assert.equal(parkingText(get('rows1').innerHTML),'비정상 주차(모의 화재)');
  vm.runInContext("state={running:false,results:{},collection:{channels:{1:{key:'test.1'}}}}; reviewStatus('1'); showTotal({})",context);
  assert.match(get('sheetTime1').textContent,/마지막 저장 기록/);
  assert.equal(get('wrongCount').textContent,'1칸');assert.match(get('scope').textContent,/마지막 저장 기록/);
  assert.equal(parkingText(get('rows1').innerHTML),'비정상 주차(모의 화재)');
  vm.runInContext("fireExample.spaces[0].fire_led={state:'off'}; state={running:true,results:{1:fireExample}}; reviewStatus('1')",context);
  assert.equal(parkingText(get('rows1').innerHTML),'비정상 주차');
  assert.doesNotMatch(get('rows1').innerHTML,/모의 화재|소등/);
  console.log('PASS stable live/saved fire labels and no off indicator on the next reading');
})().catch(error=>{console.error(error);process.exitCode=1});
