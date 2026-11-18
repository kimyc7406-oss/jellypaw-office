(function(){
  const KEY="office_selftest_last";
  const need=()=>{const have={findPath:typeof findPath,blockedAt:typeof blockedAt,ZONES:typeof ZONES,walkers:typeof walkers,els:typeof els,psAt:typeof psAt,FW:typeof FW,FH:typeof FH};
    const miss=Object.keys(have).filter(n=>have[n]==="undefined");if(miss.length)throw new Error("selftest: index.html 전역 없음 · "+miss.join(", "))};

  const rng=seed=>()=>{seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296};

  const clipRun=(x1,y1,x2,y2)=>{const L=Math.hypot(x2-x1,y2-y1);let run=0,best=0;for(let s=1;s<L/3;s++){const t=s*3/L;if(blockedAt(x1+(x2-x1)*t,y1+(y2-y1)*t)){run+=3;if(run>best)best=run}else run=0}return best};

  function staticTest(){
    const spots=[];for(const [k,z] of Object.entries(ZONES))(z.spots||[]).forEach((s,i)=>spots.push({id:k+i,x:s[0],y:s[1],seat:!!s[3]}));
    const R=rng(424242),seats={n:0,miss:0},floor={n:0,miss:0,inFurniture:0,graze3px:0,grazeMaxPx:0,blocked:0};
    const graze=pts=>{for(let i=1;i<pts.length-2;i++){const r=clipRun(...pts[i],...pts[i+1]);if(r){floor.graze3px++;floor.grazeMaxPx=Math.max(floor.grazeMaxPx,r)}}};
    const saved={...walkers};for(const k in walkers)delete walkers[k];
    try{
      for(const a of spots)for(const b of spots){if(a===b)continue;const p=findPath("selftest",a.x,a.y,b.x,b.y,b.seat),e=p[p.length-1];
        if(b.seat){seats.n++;if(Math.hypot(e[0]-b.x,e[1]-b.y)>1)seats.miss++}graze([[a.x,a.y],...p])}
      let tries=0;while(floor.n<500&&tries++<50000){const x=16+R()*(FW-32),y=R()*FH;if(blockedAt(x,y))continue;const a=spots[floor.n%spots.length];floor.n++;
        const p=findPath("selftest",a.x,a.y,x,y,false),e=p[p.length-1];if(Math.hypot(e[0]-x,e[1]-y)>1)floor.miss++;graze([[a.x,a.y],...p])}
      tries=0;while(floor.blocked<500&&tries++<50000){const x=16+R()*(FW-32),y=R()*FH;if(!blockedAt(x,y))continue;const a=spots[floor.blocked%spots.length];floor.blocked++;
        const p=findPath("selftest",a.x,a.y,x,y,false),e=p[p.length-1];if(blockedAt(e[0],e[1]))floor.inFurniture++}
    }finally{Object.assign(walkers,saved)}
    return {seats,floor};
  }

  async function liveTest(ms){
    const gap0=typeof GAP!=="undefined"?GAP:60,prev={},dir={};let jitter=0,overlap=0;const pairs=new Set();
    for(let t=0;t<ms/100;t++){
      const ids=Object.keys(walkers).filter(id=>els[id]&&!els[id].classList.contains("gone"));
      for(const id of ids){const w=walkers[id],walk=els[id].classList.contains("walk"),p=prev[id];
        if(p){const dx=w.x-p[0],dy=w.y-p[1],m=Math.hypot(dx,dy);
          if(!walk&&m>.5)jitter++;
          else if(walk&&m>.3){const d=[Math.sign(dx),Math.sign(dy)],o=dir[id];if(o&&m<6&&((d[0]&&o[0]&&d[0]!==o[0])||(d[1]&&o[1]&&d[1]!==o[1])))jitter++;dir[id]=d}}
        prev[id]=[w.x,w.y]}
      for(let i=0;i<ids.length;i++)for(let j=i+1;j<ids.length;j++){const A=ids[i],B=ids[j];if(els[A].classList.contains("walk")||els[B].classList.contains("walk"))continue;
        if(els[A].classList.contains("small")||els[B].classList.contains("small"))continue;
        const a=walkers[A],b=walkers[B],gap=gap0*(psAt(a.y)+psAt(b.y))/2;if(Math.hypot(b.x-a.x,(b.y-a.y)*1.6)<gap*.5){overlap++;pairs.add(A<B?A+"|"+B:B+"|"+A)}}
      await new Promise(r=>setTimeout(r,100))}
    return {jitter,overlap,overlapPairs:pairs.size,sec:ms/1000};
  }

  window.officeSelfTest=async function(opt={}){
    need();const t0=performance.now(),{seats,floor}=staticTest(),ms=Math.round(performance.now()-t0);
    const live=await liveTest(opt.liveMs??5000);
    return {seats,floor,live,ms,t:Date.now()};
  };

  const ROWS=[["앉는 자리 빗나감","seats","miss"],["빈 바닥 못 감(의도)","floor","miss"],["가구 안 끝점","floor","inFurniture"],["가운데 마디 스침","floor","graze3px"],["스침 최대 px","floor","grazeMaxPx"],["떨림","live","jitter"],["겹침","live","overlap"]];
  const load=()=>{try{return JSON.parse(localStorage.getItem(KEY)||"null")}catch(e){return null}};
  const save=r=>{try{localStorage.setItem(KEY,JSON.stringify(r))}catch(e){}};
  const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  function box(){let b=document.getElementById("selftest");if(b)return b;
    b=document.createElement("div");b.id="selftest";
    b.style.cssText="position:fixed;right:16px;bottom:128px;z-index:19;width:min(280px,calc(100vw - 32px));background:var(--panel,#fffaf1ee);color:var(--ink,#33281f);border:1px solid var(--line,#e2d4bf);border-radius:14px;padding:8px 12px 10px;font:13px/1.55 \"Gowun Dodum\",system-ui,sans-serif;box-shadow:0 6px 20px #0003";
    document.body.appendChild(b);b.addEventListener("click",e=>{if(e.target.closest("[data-x]"))b.remove();else if(e.target.closest("[data-again]"))window.officeSelfTestShow()});return b}
  window.officeSelfTestShow=async function(){
    const b=box();b.innerHTML=`<b style="font:14px Jua,sans-serif">🧪 길 자가 시험</b><div style="color:var(--soft,#85715d)">도는 중… 5초 지켜봄</div>`;
    let r;try{r=await window.officeSelfTest()}catch(e){if(typeof jerr==="function")jerr("자가 시험 · "+e.message);b.innerHTML=`<b>🧪 길 자가 시험</b><div style="color:#c0462f">${esc(e.message)}</div><button data-x style="margin-top:6px">닫기</button>`;return}
    const last=load();save(r);
    const cell=(sec,k)=>{const v=r[sec][k],p=last&&last[sec]?last[sec][k]:null,up=p!=null&&v>p;
      return `<td style="text-align:right;font-variant-numeric:tabular-nums;${up?"color:#c0462f;font-weight:700":""}">${v}</td><td style="text-align:right;color:var(--soft,#85715d);font-variant-numeric:tabular-nums">${p==null?"—":p}</td>`};
    b.innerHTML=`<div style="display:flex;align-items:center;gap:8px"><b style="font:14px Jua,sans-serif;flex:1">🧪 길 자가 시험</b><button data-again title="다시" style="border:0;background:none;cursor:pointer;font-size:14px">↻</button><button data-x title="닫기" style="border:0;background:none;cursor:pointer;font-size:14px">✕</button></div>
      <table style="border-collapse:collapse;width:100%"><tr style="color:var(--soft,#85715d);font-size:12px"><td></td><td style="text-align:right">지금</td><td style="text-align:right;padding-left:10px">지난번</td></tr>
      ${ROWS.map(([l,s,k])=>`<tr><td style="padding-right:10px">${l}</td>${cell(s,k)}</tr>`).join("")}</table>
      <div style="color:var(--soft,#85715d);font-size:12px">자리 ${r.seats.n}쌍 · 빈 바닥 ${r.floor.n} · 가구 안 ${r.floor.blocked} · ${r.ms}ms · 실시간 ${r.live.sec}초 · 늘면 빨강</div>`};
  document.addEventListener("click",e=>{if(e.target.closest("[data-selftest]"))window.officeSelfTestShow()});
})();
