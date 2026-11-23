const $=(s,r=document)=>r.querySelector(s);
const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>'\u20A6'+Number(n||0).toLocaleString('en-NG');
const LABEL={paid:'Paid',processing:'Preparing',dispatched:'Dispatched',out_for_delivery:'Out for delivery',delivered:'Delivered',cancelled:'Cancelled',refunded:'Refunded'};
async function api(path,opts={}){opts.credentials='same-origin';const r=await fetch(path,opts);let j=null;try{j=await r.json()}catch(e){}
  if(!r.ok)throw new Error((j&&j.error)||('Server error '+r.status));return j}
const post=(p,b)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
const put=(p,b)=>api(p,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
function toast(m){const t=document.createElement('div');t.className='toast';t.textContent=m;document.body.appendChild(t);setTimeout(()=>t.remove(),2800)}
try{if(localStorage.getItem('mandilas_theme')==='light')document.documentElement.setAttribute('data-theme','light')}catch(e){}
function cartCount(){try{const c=JSON.parse(localStorage.getItem('mandilas_cart')||'{}');return Object.values(c).reduce((a,b)=>a+b,0)}catch(e){return 0}}
function header(){const n=cartCount();document.body.insertAdjacentHTML('afterbegin',
 `<header class="top"><a href="/"><img src="/assets/logo-white.png" alt="Mandilas Online"></a><nav>
 <a href="/#shop">SHOP</a><a href="/track">TRACK ORDER</a><a href="/account">MY ACCOUNT</a><a href="/#cart">CART<span class="count">${n}</span></a></nav></header>`)}
function addToCartLocal(id,size,qty=1){let c={};try{c=JSON.parse(localStorage.getItem('mandilas_cart')||'{}')}catch(e){}
  const k=id+'::'+size;c[k]=(c[k]||0)+qty;localStorage.setItem('mandilas_cart',JSON.stringify(c))}
const STEPS=['paid','processing','dispatched','out_for_delivery','delivered'];
function timelineHTML(o){
  if(o.status==='cancelled'||o.status==='refunded')return `<span class="chip ${o.status}">${LABEL[o.status]}</span>`+histHTML(o);
  const reached=STEPS.indexOf(o.status);const when={};(o.history||[]).forEach(h=>when[h.status]=h);
  let h='<ul class="tl">'+STEPS.map((s,i)=>`<li class="${i<=reached?'done':''}">${LABEL[s]}${when[s]?`<small>${esc(when[s].at)}${when[s].note?' \u2014 '+esc(when[s].note):''}</small>`:''}</li>`).join('')+'</ul>';
  if(o.rider_name||o.rider_phone||o.eta||o.tracking_note)h+=`<div class="muted">${o.rider_name||o.rider_phone?'Rider: '+esc(o.rider_name)+' '+esc(o.rider_phone)+'<br>':''}${o.eta?'Expected: '+esc(o.eta)+'<br>':''}${o.tracking_note?esc(o.tracking_note):''}</div>`;
  return h}
function histHTML(o){return '<ul class="tl">'+(o.history||[]).map(h=>`<li class="done">${LABEL[h.status]||h.status}<small>${esc(h.at)} ${esc(h.note||'')}</small></li>`).join('')+'</ul>'}
function itemsHTML(o){return (o.items||[]).map(i=>`${esc(i.name)} (${esc(i.size)}) \u00d7${i.qty}`).join('<br>')}

function recentIds(){try{return JSON.parse(localStorage.getItem('mandilas_recent')||'[]')}catch(e){return[]}}
function trackView(id){try{const r=recentIds().filter(x=>x!==id);r.unshift(id);localStorage.setItem('mandilas_recent',JSON.stringify(r.slice(0,12)))}catch(e){}}