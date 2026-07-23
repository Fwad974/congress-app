/* Schedule reminder scheduler.
 *
 * Loaded on every page via base.html. On page load it asks the server for the
 * current user's bookmarked items + notification prefs, then sets timeouts to
 * fire reminders at each chosen lead time. Notifications go through:
 *   1. Web Notifications API (if permission granted)
 *   2. An in-page toast (always)
 *   3. A short synthesized tone via Web Audio (if not "off")
 *
 * De-dup across tabs / page navigations uses localStorage keyed by
 * (item_id, lead_minutes, day) so reminders only fire once per occurrence.
 */
(function(){
  'use strict';

  const STORAGE_KEY = 'sched_notif_fired';
  const FEED_KEY = 'sched_feed_shown';      // de-dup delivered feed notifications
  const POLL_INTERVAL_MS = 5 * 60 * 1000;   // refetch every 5 min in case admin edited
  const MAX_TIMEOUT_MS = 24 * 3600 * 1000;  // don't schedule more than a day out

  let _timers = [];
  let _audioCtx = null;
  let _prefs = null;

  function clearTimers(){
    _timers.forEach(t=>clearTimeout(t));
    _timers=[];
  }

  function audioCtx(){
    if(_audioCtx)return _audioCtx;
    const A=window.AudioContext||window.webkitAudioContext;
    if(!A)return null;
    _audioCtx=new A();
    return _audioCtx;
  }

  function playSound(name, volume){
    if(!name||name==='off')return;
    const ctx=audioCtx();if(!ctx)return;
    if(ctx.state==='suspended'){try{ctx.resume();}catch(e){}}
    const v=Math.max(0,Math.min(1,Number(volume)||0.7));
    const now=ctx.currentTime;
    const out=ctx.createGain();
    out.gain.value=v;
    out.connect(ctx.destination);
    function tone(freq,start,dur,type){
      const o=ctx.createOscillator();
      const g=ctx.createGain();
      o.type=type||'sine';
      o.frequency.value=freq;
      g.gain.setValueAtTime(0,start);
      g.gain.linearRampToValueAtTime(1,start+0.02);
      g.gain.exponentialRampToValueAtTime(0.001,start+dur);
      o.connect(g);g.connect(out);
      o.start(start);o.stop(start+dur+0.05);
    }
    if(name==='bell'){
      tone(880,now,1.0,'sine');
      tone(1320,now,0.8,'sine');
    }else if(name==='chime'){
      tone(880,now,0.5,'sine');
      tone(1320,now+0.18,0.6,'sine');
      tone(1760,now+0.36,0.7,'sine');
    }else if(name==='buzz'){
      tone(220,now,0.15,'square');
      tone(220,now+0.2,0.15,'square');
      tone(220,now+0.4,0.15,'square');
    }
  }

  function showInPageToast(item, leadMin){
    const phrase = leadMin===0 ? 'starting now' : 'starts in '+leadMin+' min';
    const detail = item.location ? ' — '+item.location : '';
    if(typeof showToast==='function'){
      showToast(item.title+' '+phrase+detail, 'success');
    }
  }

  function showWebNotification(item, leadMin){
    if(!('Notification' in window))return;
    if(Notification.permission!=='granted')return;
    const phrase = leadMin===0 ? 'starting now' : 'in '+leadMin+' min';
    const body = (item.location?item.location+' • ':'')+phrase;
    try{
      const n=new Notification(item.title, {
        body: body,
        tag: 'sched-'+item.id+'-'+leadMin,
        icon: '/static/favicon.png',
      });
      n.onclick=function(){
        window.focus();
        try{window.location.assign('/schedule');}catch(e){}
        n.close();
      };
    }catch(e){/* iOS Safari etc. — fall back silently */}
  }

  function fireKey(itemId, leadMin, startTimeIso){
    const day = startTimeIso.slice(0,10);
    return itemId+':'+leadMin+':'+day;
  }
  function alreadyFired(key){
    try{
      const map=JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}');
      return !!map[key];
    }catch(e){return false;}
  }
  function markFired(key){
    try{
      const map=JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}');
      map[key]=Date.now();
      // Garbage-collect entries older than 7 days
      const cutoff=Date.now()-7*24*3600*1000;
      Object.keys(map).forEach(k=>{if(map[k]<cutoff)delete map[k];});
      localStorage.setItem(STORAGE_KEY,JSON.stringify(map));
    }catch(e){}
  }

  function fireReminder(item, leadMin, prefs){
    if(isQuietNow(prefs))return;   // reminders are held during quiet hours
    const key=fireKey(item.id, leadMin, item.start_time);
    if(alreadyFired(key))return;
    markFired(key);
    showInPageToast(item, leadMin);
    showWebNotification(item, leadMin);
    playSound(prefs.sound, prefs.volume);
  }

  function scheduleAll(data){
    clearTimers();
    if(!data || !data.prefs || !data.prefs.enabled) return;
    const prefs=data.prefs;
    const leads=Array.isArray(prefs.lead_times)?prefs.lead_times:[];
    if(!leads.length||!data.items)return;
    const serverNow=new Date(data.server_time).getTime();
    const clientNow=Date.now();
    const skew=clientNow-serverNow;  // positive = client clock ahead of server
    data.items.forEach(item=>{
      const startMs=new Date(item.start_time).getTime();
      leads.forEach(min=>{
        const fireAt=startMs - min*60*1000 + skew;
        const delay=fireAt-clientNow;
        if(delay<0)return;            // already passed
        if(delay>MAX_TIMEOUT_MS)return; // beyond the day
        const key=fireKey(item.id, min, item.start_time);
        if(alreadyFired(key))return;
        const t=setTimeout(()=>fireReminder(item, min, prefs), delay);
        _timers.push(t);
      });
    });
  }

  // ── Delivered feed (admin changes to bookmarked items) ──────────
  function feedAlreadyShown(id){
    try{
      const map=JSON.parse(localStorage.getItem(FEED_KEY)||'{}');
      return !!map[id];
    }catch(e){return false;}
  }
  function feedMarkShown(id){
    try{
      const map=JSON.parse(localStorage.getItem(FEED_KEY)||'{}');
      map[id]=Date.now();
      const cutoff=Date.now()-30*24*3600*1000;  // keep 30 days
      Object.keys(map).forEach(k=>{if(map[k]<cutoff)delete map[k];});
      localStorage.setItem(FEED_KEY,JSON.stringify(map));
    }catch(e){}
  }

  function showFeedWebNotification(n){
    if(!('Notification' in window))return;
    if(Notification.permission!=='granted')return;
    try{
      const x=new Notification(n.title, {
        body: n.body,
        tag: 'feed-'+n.id,
        icon: '/static/favicon.png',
      });
      x.onclick=function(){
        window.focus();
        try{window.location.assign('/schedule');}catch(e){}
        x.close();
      };
    }catch(e){/* iOS Safari etc. */}
  }

  // ── Quiet hours + emergency handling ────────────────────────────
  function pad2(n){return String(n).padStart(2,'0');}
  function isQuietNow(prefs){
    const qh=prefs&&prefs.quiet_hours;
    if(!qh||!qh.enabled)return false;
    const now=new Date();
    const cur=pad2(now.getHours())+':'+pad2(now.getMinutes());
    const s=qh.start||'22:00', e=qh.end||'07:00';
    // String compare works for HH:MM. start<=end is a same-day window; else overnight.
    return (s<=e) ? (cur>=s && cur<e) : (cur>=s || cur<e);
  }
  function escHtml(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
  function markFeedRead(id){
    fetch('/api/notifications/feed/'+id+'/read',{method:'POST',credentials:'same-origin'}).catch(()=>{});
  }
  function showEmergency(n){
    const el=document.createElement('div');
    el.style.cssText='position:fixed;top:0;left:0;right:0;z-index:3000;background:linear-gradient(90deg,#b91c1c,#ef4444);color:#fff;padding:16px 20px;box-shadow:0 6px 28px rgba(0,0,0,.45);font-family:Inter,system-ui,sans-serif;display:flex;gap:14px;align-items:flex-start';
    el.innerHTML='<span style="font-size:1.5rem;line-height:1">🚨</span>'+
      '<div style="flex:1"><div style="font-weight:700;font-size:.98rem;margin-bottom:3px">'+escHtml(n.title)+'</div>'+
      '<div style="font-weight:400;font-size:.9rem;opacity:.96;line-height:1.4">'+escHtml(n.body)+'</div></div>'+
      '<button style="background:rgba(0,0,0,.22);border:none;color:#fff;border-radius:7px;padding:7px 12px;cursor:pointer;font-weight:600;flex-shrink:0">Dismiss</button>';
    el.querySelector('button').onclick=function(){el.remove();markFeedRead(n.id);};
    document.body.appendChild(el);
  }

  function handleFeed(data){
    if(!data || !Array.isArray(data.items))return;
    const quiet=isQuietNow(_prefs);
    const enabled=!(_prefs && _prefs.enabled===false);
    // Surface unread notifications we haven't already shown in this browser.
    const fresh=data.items.filter(n=>!n.read && !feedAlreadyShown(n.id));
    if(!fresh.length)return;
    fresh.sort((a,b)=>new Date(a.created_at)-new Date(b.created_at));  // chronological
    let played=false;
    fresh.forEach(n=>{
      const emg=(n.kind==='emergency');
      // Hold non-emergency notifications during quiet hours — leave them unread
      // so they surface once quiet hours end.
      if(!emg && quiet)return;
      // Note: we deliberately do NOT mark the item read here — the toast is a
      // transient surface; unread state belongs to the nav bell inbox, where
      // the user reads or clears it explicitly.
      feedMarkShown(n.id);
      if(emg){
        showEmergency(n);
        showFeedWebNotification(n);
        if(!played){playSound('buzz',1);played=true;}   // emergencies always sound
      }else{
        if(typeof showToast==='function'){
          showToast(n.title+' — '+n.body, n.kind==='cancelled'?'error':'success');
        }
        if(enabled){
          showFeedWebNotification(n);
          if(!played){playSound(_prefs.sound,_prefs.volume);played=true;}
        }
      }
    });
  }

  async function refreshFeed(){
    try{
      const r=await fetch('/api/notifications/feed',{credentials:'same-origin'});
      if(!r.ok)return;
      handleFeed(await r.json());
      // Keep the nav bell badge in step with what just arrived.
      if(typeof refreshBell==='function')refreshBell();
    }catch(e){/* network blip */}
  }

  // Tell the server this browser's timezone once, so it can apply quiet hours
  // to web push (sent when the tab is closed). Client-side quiet hours use the
  // local clock directly and don't need this.
  let _tzSynced=false;
  function maybeSyncTz(prefs){
    if(_tzSynced || !prefs)return;
    _tzSynced=true;
    let tz='';try{tz=Intl.DateTimeFormat().resolvedOptions().timeZone||'';}catch(e){}
    if(!tz || prefs.tz===tz)return;
    fetch('/api/notifications/settings',{
      method:'PUT',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(Object.assign({},prefs,{tz:tz})),
    }).catch(()=>{});
  }

  async function refresh(){
    try{
      const r=await fetch('/api/notifications/upcoming',{credentials:'same-origin'});
      if(r.status===401||r.status===403)return; // not logged in — silently bail
      if(!r.ok)return;
      const data=await r.json();
      _prefs=data.prefs||null;
      scheduleAll(data);
      maybeSyncTz(_prefs);
    }catch(e){/* network blip — try again next interval */}
    refreshFeed();
  }

  // ── Web Push registration (delivery when the tab is closed) ─────
  function urlB64ToUint8Array(base64){
    const padding='='.repeat((4-base64.length%4)%4);
    const b64=(base64+padding).replace(/-/g,'+').replace(/_/g,'/');
    const raw=atob(b64);
    const out=new Uint8Array(raw.length);
    for(let i=0;i<raw.length;i++)out[i]=raw.charCodeAt(i);
    return out;
  }

  async function ensurePush(){
    if(!('serviceWorker' in navigator)||!('PushManager' in window))return;
    if(!('Notification' in window)||Notification.permission!=='granted')return;
    try{
      const r=await fetch('/api/notifications/push/key',{credentials:'same-origin'});
      if(!r.ok)return;
      const cfg=await r.json();
      if(!cfg.enabled||!cfg.public_key)return;   // push not configured server-side
      const reg=await navigator.serviceWorker.register('/static/sw.js');
      await navigator.serviceWorker.ready;
      let sub=await reg.pushManager.getSubscription();
      if(!sub){
        sub=await reg.pushManager.subscribe({
          userVisibleOnly:true,
          applicationServerKey:urlB64ToUint8Array(cfg.public_key),
        });
      }
      await fetch('/api/notifications/push/subscribe',{
        method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(sub),
      });
    }catch(e){/* push unavailable — feed still works */}
  }

  // Expose a hook so the schedule + settings pages can re-trigger after changes.
  window.__schedNotif = {
    refresh: refresh,
    requestPermission: function(){
      if(!('Notification' in window))return Promise.resolve('unsupported');
      if(Notification.permission==='granted'){ensurePush();return Promise.resolve('granted');}
      if(Notification.permission==='denied')return Promise.resolve('denied');
      return Notification.requestPermission().then(function(p){
        if(p==='granted')ensurePush();
        return p;
      });
    },
    enablePush: ensurePush,
    testSound: function(name, volume){playSound(name, volume);},
  };

  document.addEventListener('DOMContentLoaded', refresh);
  document.addEventListener('DOMContentLoaded', ensurePush);
  setInterval(refresh, POLL_INTERVAL_MS);
  // Re-pull when the tab regains focus (clock may have drifted while asleep).
  window.addEventListener('focus', refresh);
})();
