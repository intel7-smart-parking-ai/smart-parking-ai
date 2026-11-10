import test from 'node:test';
import assert from 'node:assert/strict';
import {createV3Session} from '../src/chatV3Session.js';
const config={session_id:'s',runtime_id:'r',model_mode:'fixture',supported_languages:['ko']};
const reply={text:'확인',messages:[{text:'확인'}],runtime_id:'r'};
test('timeout retry retains exactly the same request; new text cannot overwrite pending',async()=>{
 const posts=[];let fails=true;
 const chat=createV3Session({makeId:()=> 'id',request:async(path,opts)=>{if(path)return config;posts.push({...opts.body});if(fails){fails=false;throw new Error('timeout');}return reply;}});
 await chat.start();chat.edit('부산역');await chat.send();chat.edit('다른 질문');await chat.send();
 assert.equal(posts.length,1);await chat.retry();assert.deepEqual(posts[0],posts[1]);assert.equal(chat.getSnapshot().pending,null);
});
test('expired session requires reset; config creates new lease',async()=>{
 let seq=0;const chat=createV3Session({makeId:()=> 'id',request:async path=>{if(path)return {...config,session_id:'s'+(++seq)};const e=new Error('expired');e.code='SESSION_EXPIRED';throw e;}});
 await chat.start();chat.edit('질문');await chat.send();assert.equal(chat.getSnapshot().resetRequired,true);await chat.start();assert.equal(chat.getSnapshot().config.session_id,'s2');assert.equal(chat.getSnapshot().pending,null);
});
test('input over team 200 character limit is not submitted',async()=>{
 let posts=0;const chat=createV3Session({request:async path=>{if(path)return config;posts++;return reply;}});await chat.start();chat.edit('가'.repeat(201));await chat.send();assert.equal(posts,0);
});
