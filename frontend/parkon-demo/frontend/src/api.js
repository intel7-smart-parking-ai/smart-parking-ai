export async function api(path,options={}){
 const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),10000);
 try{
  const response=await fetch('/api/v1'+path,{method:options.method||'GET',headers:{'Content-Type':'application/json'},signal:controller.signal,...(options.body?{body:JSON.stringify(options.body)}:{})});
  const data=await response.json();
  if(!response.ok){const error=new Error(data.message||(typeof data.detail==='string'?data.detail:'API 요청을 처리하지 못했습니다.'));error.code=data.code;throw error;}
  return data;
 }catch(e){if(e.name==='AbortError')throw new Error('응답 시간이 초과되었습니다.');throw e;}
 finally{clearTimeout(timeout);}
}
