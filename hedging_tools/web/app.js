'use strict';
let data=null, selected='scanner', view='overview', busy=false;
const $=id=>document.getElementById(id);
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>v==null?'—':Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const elapsed=v=>v==null?'Unknown':v<60?Math.round(v)+'s ago':v<3600?Math.floor(v/60)+'m ago':Math.floor(v/3600)+'h ago';
const stamp=v=>v?new Date(v).toLocaleString('en-GB',{timeZone:'Asia/Jakarta',hour:'2-digit',minute:'2-digit',second:'2-digit'})+' WIB':'—';
const short=p=>String(p??'—').replace('-USDT-SWAP',' / USDT');
const badge=s=>`<span class="state ${['OFFLINE','STALE','NOT_CHECKED'].includes(s)?'off':['COOLDOWN','BLOCKED','ERROR','RETRY_READY'].includes(s)?'warn':''}">${esc(s)}</span>`;
const table=(heads,rows)=>rows.length?'<table><thead><tr>'+heads.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>':'<div class="empty"><strong>No records yet</strong>New engine observations will appear here automatically.</div>';
function office(){
 let floor=`<defs><pattern id="grain" width="42" height="24" patternUnits="userSpaceOnUse"><path d="M0 12L21 0L42 12L21 24Z" fill="none" stroke="#637b77" stroke-opacity=".12"/></pattern><linearGradient id="wall" x2="0" y2="1"><stop stop-color="#47626a"/><stop offset="1" stop-color="#293e47"/></linearGradient><linearGradient id="glass" x2="0" y2="1"><stop stop-color="#213d59"/><stop offset="1" stop-color="#577c83"/></linearGradient></defs>
 <ellipse cx="510" cy="423" rx="385" ry="99" fill="#060f17" opacity=".5"/>
 <path d="M100 263L465 75L922 306L548 507Z" fill="#243e43"/><path d="M100 263V284L548 526V507Z" fill="#1b2e35"/><path d="M548 507L922 306V326L548 526Z" fill="#142832"/>
 <path d="M110 260L465 88L907 307L548 495Z" fill="#536760"/><path d="M110 260L465 88L907 307L548 495Z" fill="url(#grain)"/>
 <path d="M100 263V125L465 -4V75Z" fill="url(#wall)"/><path d="M465 -4L922 207V306L465 75Z" fill="#344c57"/>
 <path d="M114 127L465 0L911 210" fill="none" stroke="#6c8587" stroke-width="7"/>
 <path d="M145 135L278 87V180L145 242Z" fill="#152a37" stroke="#789493" stroke-width="5"/><path d="M154 140L270 99V176L154 227Z" fill="url(#glass)"/>
 <path d="M170 204V163L190 156V193M211 183V133L230 126V173M247 168V137L260 132V161" fill="#182c41" stroke="#273d50" stroke-width="5"/>
 <path d="M613 85L771 159V232L613 158Z" fill="#172c38" stroke="#79918e" stroke-width="5"/><path d="M622 99L760 163V218L622 152Z" fill="url(#glass)"/>
 <path d="M635 155V124L654 132V162M674 171V133L694 142V180M715 190V161L735 169V199" fill="#213c50" stroke="#273d50" stroke-width="5"/>
 <path d="M320 88L423 51V100L320 144Z" fill="#1b3438" stroke="#778f7a" stroke-width="2"/><text x="333" y="110" transform="rotate(-22 333 110)" font-size="15" fill="#b2e4bb" letter-spacing="2">AVAR4ISE</text>
 <path d="M395 310L505 254L683 345L569 408Z" fill="#263d3b" stroke="#769383" stroke-width="2"/><path d="M411 311L505 265L663 345L567 397Z" fill="none" stroke="#4b6658"/><text x="492" y="338" transform="rotate(27 492 338)" fill="#668478" font-size="16" letter-spacing="5">A V A R 4 I S E</text>
 <g transform="translate(171 249)"><path d="M-13 0L0-7L13 0V21L0 29L-13 21Z" fill="#a19175"/><path d="M0 4V-36M0-17L-22-30M0-10L22-24" stroke="#87a57b" stroke-width="5"/><ellipse cx="-15" cy="-31" rx="16" ry="7" fill="#53866e" transform="rotate(30 -15 -31)"/><ellipse cx="14" cy="-27" rx="16" ry="7" fill="#74a286" transform="rotate(-28 14 -27)"/><ellipse cx="0" cy="-42" rx="8" ry="17" fill="#8ab795"/></g>
 <g transform="translate(834 315)"><path d="M-15 0L0-8L15 0V23L0 31L-15 23Z" fill="#afa182"/><path d="M0 0V-51" stroke="#568a71" stroke-width="4"/><ellipse cx="-12" cy="-32" rx="19" ry="9" fill="#63977b" transform="rotate(28 -12 -32)"/><ellipse cx="12" cy="-45" rx="19" ry="9" fill="#87b18d" transform="rotate(-28 12 -45)"/></g>
 <path d="M744 375L809 342L848 362L780 397Z" fill="#8b8069"/><path d="M744 375V404L780 424V397Z" fill="#5e655b"/><path d="M780 397L848 362V390L780 424Z" fill="#414f48"/><path d="M789 401L839 375M789 411L839 385" stroke="#a09b7b"/>
 `;
 const desks=[['scanner',325,205,'#8fcbaf'],['research',485,159,'#c7b38a'],['risk',659,246,'#82abc9'],['execution',370,358,'#89b6b2'],['signals',573,425,'#ba9abf'],['alerts',743,340,'#c8ad73']];
 for(const [id,x,y,color] of desks){const m=data?.modules.find(m=>m.id===id);const working=m?.state==='WORKING';
 const bubble=working?`<g transform="translate(-55 -85)"><rect width="130" height="25" rx="6" fill="#a3e8bd"/><text x="10" y="16" fill="#18352c" font-size="10">${id==='scanner'?'Scanning · '+Math.round(data.engine.phase_age||0)+'s':'Reviewing evidence'}</text></g>`:'';
 floor+=`<g class="station ${working?'working':''} ${selected===id?'selected':''}" data-module="${id}" transform="translate(${x} ${y})">${bubble}<ellipse cx="0" cy="30" rx="69" ry="20" fill="#122c32" opacity=".35"/><path d="M-55-5L0-31L61 0L6 30Z" fill="#a7ad92" class="desk-edge"/><path d="M-55-5V9L6 44V30Z" fill="#6b8175"/><path d="M6 30L61 0V15L6 44Z" fill="#4b6765"/><path d="M-43 14V35M48 22V44" stroke="#243e45" stroke-width="6"/>
 <path d="M-29-13V-49L15-26V8Z" fill="#162d37" stroke="#87968a" stroke-width="3"/><path d="M-23-39L9-22V-1L-23-18Z" fill="#163e40"/><path class="screen-light" d="M-19-31L5-19M-19-24L-3-16M-19-18L5-6" stroke="${color}" stroke-width="2"/><path d="M-8 9L16-2L35 8L11 20Z" fill="#344e50"/>
 <path d="M35-12V0L43 4L46 1V-9Z" fill="${color}"/>
 <g class="robot"><ellipse cx="-31" cy="38" rx="18" ry="9" fill="#172e36"/><rect x="-42" y="16" width="24" height="24" rx="5" fill="#283f4b"/><path d="M-38 36V49M-22 37V49" stroke="#243c45" stroke-width="7"/><rect x="-41" y="2" width="25" height="27" rx="4" fill="${color}"/><rect x="-40" y="-19" width="25" height="24" rx="4" fill="#d0c8a5"/><path d="M-41-13V-22L-23-27L-14-20V-10L-24-14Z" fill="#34444a"/><rect x="-32" y="-9" width="4" height="4" fill="#24363a"/><rect x="-22" y="-5" width="3" height="4" fill="#24363a"/><path d="M-19 8L-6 16M-39 12L-47 18" stroke="${color}" stroke-width="8"/></g>
 <g transform="translate(-58 61)"><rect width="130" height="21" rx="4" fill="#0b1d27" stroke="#3b5559"/><circle cx="10" cy="10" r="3" fill="${m?.state==='WORKING'?'#9cf1c1':m?.state==='OFFLINE'?'#75838b':'#c7b58b'}"/><text x="19" y="14" font-size="9" fill="#d0dfd8">${esc(m?.name||id)}</text></g></g>`;
 }
 $('office').innerHTML=floor;$('office').classList.toggle('offline',data?.engine.status!=='RUNNING');
 $('office').querySelectorAll('[data-module]').forEach(el=>el.addEventListener('click',()=>choose(el.dataset.module)));
}
function choose(id){selected=id;office();inspector();modules();}
function modules(){$('module-buttons').innerHTML=data.modules.map(m=>`<button data-module="${esc(m.id)}" class="${selected===m.id?'selected':''}">${esc(m.name)}<small>${esc(m.state)}</small></button>`).join('');$('module-buttons').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>choose(b.dataset.module)));}
function inspector(){const m=data.modules.find(m=>m.id===selected);$('inspector').innerHTML=`<div class="avatar">${{scanner:'⌕',research:'✦',risk:'◇',execution:'▥',signals:'⚑',alerts:'↗'}[m.id]}</div>${badge(m.state)}<h2>${esc(m.name)}</h2><span class="tiny">${esc(m.role)}</span><p>${esc(m.detail)}</p>`;
 const p=data.provider;const health=[['Engine heartbeat',elapsed(data.engine.heartbeat_age)],['Latest scan',elapsed(data.scanner.age)],['AI availability',p.state],['Last AI result',p.reason||'No record'],['Retry timer',p.remaining_seconds?p.remaining_seconds+'s':'Not active'],['Exchange checks',data.portfolio.errors?'Needs attention':data.engine.status==='RUNNING'?'No recorded error':'Not live']];
 $('health').innerHTML=health.map(([a,b])=>`<div><dt>${esc(a)}</dt><dd>${esc(b)}</dd></div>`).join('');}
function draw(){
 const p=data.portfolio;const live=data.engine.status==='RUNNING';
 $('connection').textContent='● Connected locally';$('floor-state').textContent=data.engine.status;
 $('banner').classList.toggle('good',live&&!p.errors&&!p.halted);
 $('banner').textContent=!live?`${data.engine.status} · Engine heartbeat ${elapsed(data.engine.heartbeat_age)}. Showing saved observations; no activity is simulated.`:p.halted?'DAILY HALT · New entries are blocked. Monitoring continues.':p.errors?'ATTENTION · Exchange reconciliation needs attention. Check the Telegram remote.':`ENGINE ONLINE · ${data.engine.phase||'MONITORING'} · Watching the market. WAIT is normal when no setup qualifies.`;
 if(data.warnings.length)$('banner').textContent+=' Data unavailable: '+data.warnings.join(', ');
 $('equity').textContent=p.equity==null?'—':'$'+money(p.equity);$('capacity').innerHTML=p.active+` <em>/ ${esc(p.max_positions)}</em>`;
 $('margin').textContent=p.margin?`Margin ≤ $${p.margin} · ${p.min_leverage}–${p.max_leverage}x`:'Risk settings unavailable';
 const delta=p.equity!=null&&Number(p.day_start)>0?(Number(p.equity)/Number(p.day_start)-1)*100:null;
 $('daily').textContent=delta==null?'—':(delta>=0?'+':'')+delta.toFixed(2)+'%';$('daily').className=delta<0?'negative':'positive';
 $('daily-note').textContent='Saved day: '+(p.day||'unknown')+' · loss limit −10%';
 $('tokens').textContent=data.tokens_24h.toLocaleString('en-US');$('updated').textContent='Snapshot '+stamp(data.time);$('scan-age').textContent=elapsed(data.scanner.age);
 const open=data.positions.filter(p=>p.status!=='CLOSED');
 $('positions-mini').innerHTML=open.length?open.slice(0,5).map(p=>`<div class="mini-row"><div>${esc(short(p.pair))}<br><small>${esc(p.direction)} · ${esc(p.leverage)}x</small></div>${badge(p.status)}<span>${esc(money(p.upl))} USDT</span></div>`).join(''):'<div class="empty"><strong>No open positions</strong>Available slots stay empty until a setup clears AI review and risk checks.</div>';
 $('scan-mini').innerHTML=data.scanner.rows.slice(0,4).map(r=>`<div class="mini-row"><span>${esc(short(r.pair))}</span><span class="reason">${esc((r.reasons[0]||r.status||'No observation').replaceAll('_',' '))}</span></div>`).join('')||'<div class="empty">No scan recorded yet.</div>';
 scanner();
 $('portfolio-table').innerHTML=table(['PAIR / SIDE','STATUS','ENTRY','CURRENT SL','TP STAGE','REALIZED PNL','UNREALIZED PNL'],data.positions.slice().reverse().map(p=>[esc(short(p.pair))+'<br><span class="tiny">'+esc(p.direction)+'</span>',badge(p.status),esc(p.entry||p.limit_price),esc(p.current_stop||p.stop),esc(p.stage??'—'),esc(money(p.pnl))+' USDT',esc(money(p.upl))+' USDT']));
 $('signals-table').innerHTML=table(['PAIR','DIRECTION','STATUS','MILESTONES','QUALITY','ISSUED'],data.signals.map(s=>[esc(short(s.pair)),esc(s.direction),badge(s.status),[1,2,3].map(i=>(Number(s.stage)>=i?'✓':'□')+' TP'+i).join(' · '),esc(s.quality),stamp(s.issued_ms)]));
 $('activity-table').innerHTML=table(['TIME (WIB)','DESTINATION','DELIVERY'],data.activity.map(a=>[stamp(a.time),esc(a.channel),badge(a.status)]));
 modules();inspector();office();
}
function scanner(){if(!data)return;const q=$('pair-search').value.toUpperCase();$('scanner-table').innerHTML=table(['PAIR','SCREEN','AI REVIEW','DECISION / REASON'],data.scanner.rows.filter(r=>String(r.pair).includes(q)).map(r=>[esc(short(r.pair)),badge(r.status),esc(r.review||'Not reviewed'),'<span class="reason-cell">'+esc(r.reasons.join(' · ')||r.action||'—')+'</span>']));}
function switchView(name){view=name;document.querySelectorAll('.view').forEach(v=>v.classList.toggle('hidden',v.id!=='view-'+name));document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('selected',b.dataset.view===name));document.querySelector('h1').innerHTML=({overview:'Trading office',scanner:'Market scanner',portfolio:'Portfolio',signals:'Signal journal',activity:'Delivery activity'}[name])+ '<span class="dot">.</span>';}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));$('pair-search').addEventListener('input',scanner);
async function refresh(){if(busy)return;busy=true;try{const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok)throw new Error();data=await response.json();draw();}catch(e){$('connection').textContent='● Disconnected';$('banner').classList.remove('good');$('banner').textContent='CONNECTION LOST · Data on this page is stale. Reconnecting automatically.';$('office').classList.add('offline');if(data){data.modules.forEach(m=>m.state='STALE');modules();inspector();office();$('office').classList.add('offline');}}finally{busy=false;}}
$('refresh').addEventListener('click',refresh);office();refresh();setInterval(refresh,5000);
