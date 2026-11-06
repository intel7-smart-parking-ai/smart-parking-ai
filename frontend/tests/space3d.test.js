import test from 'node:test';
import assert from 'node:assert/strict';
import {spaceGeometry3d} from '../src/spaceGeometry3d.js';
for(const [kind,count,zones] of [['basic',12,2],['angled',16,2],['loop',12,2],['l_shape',10,2],['mall',64,4]]){
 test(`3D ${kind} preserves every space and fits the source drawing`,()=>{
  const spaces=Array.from({length:count},(_,i)=>({space_id:`${String.fromCharCode(65+Math.floor(i/(count/zones)))}-${i%(count/zones)+1}`,zone:String.fromCharCode(65+Math.floor(i/(count/zones))),status:i%2?'empty':'occupied',car_id:i,space_type:'general'}));
  const result=spaceGeometry3d({layout_type:kind,spaces});
  assert.equal(result.spaces.length,count);
  for(const [i,s] of result.spaces.entries()){
   assert.equal(s.space_id,spaces[i].space_id);assert.equal(s.status,spaces[i].status);assert.equal(s.car_id,spaces[i].car_id);
   assert.ok(s.x>=0&&s.y>=0&&s.x+s.w<=result.width&&s.y+s.h<=result.height,`${s.space_id} is within the drawing`);
  }
 });
}
test('3D geometry keeps uncertain and unavailable states distinct from empty',()=>{
 const result=spaceGeometry3d({layout_type:'basic',spaces:['uncertain','unavailable','empty'].map((status,i)=>({zone:'A',space_id:`A-${i}`,status}))});
 assert.deepEqual(result.spaces.map(s=>s.status),['uncertain','unavailable','empty']);
});
test('unknown layouts do not invent a 3D arrangement',()=>assert.equal(spaceGeometry3d({layout_type:'unknown',spaces:[]}),null));
