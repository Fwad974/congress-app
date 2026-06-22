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

  function handleFeed(data){
    if(!data || !Array.isArray(data.items))return;
    // Surface unread notifications we haven't already shown in this browser.
    const fresh=data.items.filter(n=>!n.read && !feedAlreadyShown(n.id));
    if(!fresh.length)return;
    // Oldest first so toasts read in chronological order.
    fresh.sort((a,b)=>new Date(a.created_at)-new Date(b.created_at));
    fresh.forEach(n=>{
      feedMarkShown(n.id);
      if(typeof showToast==='function'){
        showToast(n.title+' — '+n.body, n.kind==='cancelled'?'error':'success');
      }
      if(_prefs && _prefs.enabled!==false){
        showFeedWebNotification(n);
      }
    });
    if(_prefs && _prefs.enabled!==false){
      playSound(_prefs.sound, _prefs.volume);
    }
    // Clear the server-side unread state now that we've surfaced them.
    fetch('/api/notifications/feed/read-all',{method:'POST',credentials:'same-origin'})
      .catch(()=>{});
  }

  async function refreshFeed(){
    try{
      const r=await fetch('/api/notifications/feed',{credentials:'same-origin'});
      if(!r.ok)return;
      handleFeed(await r.json());
    }catch(e){/* network blip */}
  }

  async function refresh(){
    try{
      const r=await fetch('/api/notifications/upcoming',{credentials:'same-origin'});
      if(r.status===401||r.status===403)return; // not logged in — silently bail
      if(!r.ok)return;
      const data=await r.json();
      _prefs=data.prefs||null;
      scheduleAll(data);
    }catch(e){/* network blip — try again next interval */}
    refreshFeed();
  }

  // Expose a hook so the schedule + settings pages can re-trigger after changes.
  window.__schedNotif = {
    refresh: refresh,
    requestPermission: function(){
      if(!('Notification' in window))return Promise.resolve('unsupported');
      if(Notification.permission==='granted')return Promise.resolve('granted');
      if(Notification.permission==='denied')return Promise.resolve('denied');
      return Notification.requestPermission();
    },
    testSound: function(name, volume){playSound(name, volume);},
  };

  document.addEventListener('DOMContentLoaded', refresh);
  setInterval(refresh, POLL_INTERVAL_MS);
  // Re-pull when the tab regains focus (clock may have drifted while asleep).
  window.addEventListener('focus', refresh);
})();
