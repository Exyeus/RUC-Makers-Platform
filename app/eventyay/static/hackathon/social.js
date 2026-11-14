const log = document.querySelector('#chat-log'), form = document.querySelector('#chat-send'), status = document.querySelector('#chat-status');
let stopped = false, busy = false;
let cursor = Math.max(0,...Array.from(log.querySelectorAll('[data-message-id]'),x=>Number(x.dataset.messageId)));
async function poll(){
 if(stopped || document.hidden) return;
 try {
  const url = new URL(location.href); url.searchParams.set('poll','1');url.searchParams.set('after',cursor);
  const r=await fetch(url,{cache:'no-store'});
  if(r.status===403 || r.status===404 || r.redirected){stopped=true;form.hidden=true;log.replaceChildren();document.querySelector('#chat-contacts').replaceChildren();status.textContent='会话权限已变更，请返回消息列表。';return;}
  if(!r.ok) throw Error(); const data=await r.json();
  for(const m of data.messages){const p=document.createElement('p');p.dataset.messageId=m.id;p.textContent=(m.author_id===log.dataset.me?'我':'对方')+'：'+m.body;log.append(p);cursor=m.id;}
  const contacts=document.querySelector('#chat-contacts');contacts.replaceChildren();
  for(const [key,value] of Object.entries(data.contacts)){const p=document.createElement('p');p.textContent=key+'：'+value;contacts.append(p);}
  status.textContent='消息已更新';
 }catch{status.textContent='连接暂时中断，正在重试。';}
}
form.addEventListener('submit',async e=>{e.preventDefault();if(busy||stopped)return;busy=true;form.querySelector('button').disabled=true;
 try{const r=await fetch(location.href,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'fetch'}});if(r.redirected)throw Error();if(!r.ok){let d=await r.json();status.textContent=d.error||'发送失败';return;}form.elements.body.value='';form.elements.client_id.value=crypto.randomUUID();await poll();}catch{status.textContent='发送状态未知，可重试同一条消息，不会重复发送。';}finally{busy=false;form.querySelector('button').disabled=false;}});
async function loop(){await poll();if(!stopped)setTimeout(loop,3000);}loop();
