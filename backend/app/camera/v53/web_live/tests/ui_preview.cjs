const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const requests = [], timers = new Map(), turns = new Map(), drawn = [];
let timerId = 0, blockDecode, lastBitmap, wallTime=1000;
class WallClock extends Date {static now(){return wallTime;}}
const settle = () => new Promise(setImmediate);
const context = vm.createContext({
  performance, AbortController, Date:WallClock,
  setTimeout(fn, ms) { timers.set(++timerId, {fn, ms}); return timerId; },
  clearTimeout(id) { timers.delete(id); },
  localStorage: {getItem:k=>turns.get(k),setItem:(k,v)=>turns.set(k,v)},
  fetch(url) { return new Promise(resolve=>requests.push({url,resolve})); },
  async createImageBitmap() {
    if (blockDecode) await blockDecode;
    return lastBitmap = {width:960, height:540, closed:false, close(){this.closed=true;}};
  }
});
const script = fs.readFileSync(path.join(__dirname, '../static/preview.js'), 'utf8');
vm.runInContext(script, context);
const canvas = {getContext:()=>({setTransform(){},rotate(){},drawImage(){drawn.push(1);}})};
const placeholder = {}, turn = {};
const setRunning = context.createParkingPreview('1', canvas, placeholder, turn);
function respond(status, state, epoch=1, sequence=8) {
  const req = requests.shift();
  req.resolve({status,ok:true,blob:async()=>null,headers:{get:k=>({
    'X-Preview-State':state,'X-Frame-Epoch':epoch,'X-Frame-Sequence':sequence
  })[k]}});
  return req.url;
}
function tick(predicate) {
  const [id,{fn}] = [...timers].find(([,t])=>predicate(t.ms));
  timers.delete(id);fn();
}
(async()=>{
  setRunning(true);setRunning(true);assert.equal(requests.length,1);
  respond(200,'frame');await settle();
  assert.equal(canvas.hidden,false);assert.equal(canvas.width,960);
  turn.onclick();assert.equal(canvas.width,540);assert.equal(canvas.height,960);
  assert.equal(turns.get('v53-preview-quarter-turns-1'),'1');
  tick(ms=>ms<70);
  assert.match(respond(204,'unchanged'), /epoch=1&sequence=8/);
  await settle();assert.equal(canvas.hidden,false);
  tick(ms=>ms===1250);assert.equal(canvas.hidden,true);
  tick(ms=>ms<70);assert.match(respond(204,'offline'),/epoch=-1&sequence=-1/);
  await settle();assert.equal(canvas.hidden,true);
  tick(ms=>ms<=350);respond(200,'frame',2,1);await settle();
  assert.equal(canvas.hidden,false);
  console.log('PASS preview orientation, one request, unchanged, expiry, offline and fresh reconnection');
  tick(ms=>ms<70);
  const priorDraws=drawn.length;
  wallTime+=2000;
  respond(200,'frame',2,2);await settle();
  assert.equal(drawn.length,priorDraws);assert.equal(canvas.hidden,true);assert.equal(lastBitmap.closed,true);
  console.log('PASS late preview response cannot redraw an expired image');
  let resume;
  blockDecode = new Promise(r=>resume=r);
  tick(ms=>ms<70);respond(200,'frame',2,2);await settle();
  setRunning(false);const count=drawn.length;resume();await settle();
  assert.equal(drawn.length,count);assert.equal(canvas.hidden,true);
  assert.equal(lastBitmap.closed,true);assert.equal(timers.size,0);
  console.log('PASS frame decoded after stop is discarded and bitmap released');
})().catch(error=>{console.error(error);process.exitCode=1;});
