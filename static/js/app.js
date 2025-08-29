// static/js/app.js

// ---------- DOM ----------
const $ = (s) => document.querySelector(s);

// ---------- 언어 선택 헬퍼 ----------
function collectLangs(cls){
  const arr=[...document.querySelectorAll(cls+':checked')].map(i=>i.value.trim());
  const uniq=[...new Set(arr)].filter(Boolean);
  return uniq.join(',')||'en';
}
function syncHidden(group){
  const id = group==='A'?'langs':'langs2';
  const cls= group==='A'?'.langOptA':'.langOptB';
  const el=$('#'+id); if(el) el.value=collectLangs(cls);
}
document.addEventListener('change',(e)=>{
  if(e.target.classList.contains('langOptA')) syncHidden('A');
  if(e.target.classList.contains('langOptB')) syncHidden('B');
});

// ---------- 상태/프로그레스 ----------
function setStatus(m){const el=$('#statusMsg'); if(el) el.textContent=m||'';}
function setProgress(p){const f=$('#progressFill'); if(!f) return; const t=Math.max(0,Math.min(1,Number(p||0))); f.style.width=`${Math.round(t*100)}%`;}

// ---------- 전역 ----------
let CURRENT={taskId:null,vttMap:{},videoReady:false,activeLang:null,polling:null};
let LAST={cueKey:"",ts:0};
const DEBOUNCE_MS=250;

// ---------- 업로드(ASR) ----------
$('#startBtn')?.addEventListener('click', async ()=>{
  const file=$('#fileInput')?.files?.[0]; if(!file) return alert('Select a video file first.');
  const langs=$('#langs')?.value||'en';
  const form=new FormData(); form.append('file', file);
  form.append('target_langs', langs);
  form.append('asr_model', 'medium');
  form.append('src_lang', 'ko');

  const res=await fetch('/upload',{method:'POST',body:form});
  const data=await res.json();
  CURRENT.taskId=data.task_id; setStatus('Queued…'); setProgress(0.02);
  if(CURRENT.polling) clearInterval(CURRENT.polling);
  CURRENT.polling=setInterval(()=>pollStatus(CURRENT.taskId), 2000);
});

// ---------- 업로드(SRT만) ----------
$('#startBtn2')?.addEventListener('click', async ()=>{
  const v=$('#fileInput2Video')?.files?.[0];
  const s=$('#fileInput2Srt')?.files?.[0];
  if(!v||!s) return alert('Select both video and SRT.');
  const langs=$('#langs2')?.value||'en';
  const srtLang=$('#srtLang')?.value||'ko';

  const form=new FormData();
  form.append('video', v);
  form.append('srt', s);
  form.append('srt_lang', srtLang);
  form.append('target_langs', langs);

  const res=await fetch('/upload_with_srt',{method:'POST',body:form});
  const data=await res.json();
  CURRENT.taskId=data.task_id; setStatus('Queued…'); setProgress(0.02);
  if(CURRENT.polling) clearInterval(CURRENT.polling);
  CURRENT.polling=setInterval(()=>pollStatus(CURRENT.taskId), 2000);
});

// ---------- 상태 폴링 ----------
async function pollStatus(taskId){
  if(!taskId) return;
  try{
    const r=await fetch(`/status/${taskId}`);
    const d=await r.json();
    setProgress(d.progress||0); setStatus(`${d.state} – ${d.message||''}`);

    if(d.outputs?.vtt && Object.keys(d.outputs.vtt).length){
      CURRENT.vttMap=d.outputs.vtt;
      if(!CURRENT.videoReady){
        await setupPlayer(taskId, CURRENT.vttMap);
        CURRENT.videoReady=true;
      }
      updateTrackOptions(CURRENT.vttMap);
    }

    if(d.state==='SUCCESS'||d.state==='FAILURE'){
      clearInterval(CURRENT.polling); CURRENT.polling=null;
    }
  }catch(e){ console.error(e); }
}

// ---------- 플레이어/자막 ----------
async function setupPlayer(taskId, vttMap){
  const video=$('#video'); if(!video) return;
  video.src=`/video/${taskId}`;
  // 기존 track 제거
  [...video.querySelectorAll('track')].forEach(t=>t.remove());

  // 자막 트랙 추가
  Object.entries(vttMap).forEach(([lang,url])=>{
    const tr=document.createElement('track');
    tr.kind='subtitles';
    tr.label=lang.toUpperCase();
    tr.srclang=lang;
    tr.src=url;
    tr.default=(lang==='ko');
    tr.addEventListener('load', ()=>bindCueStreaming(video));
    video.appendChild(tr);
  });

  video.addEventListener('loadedmetadata', ()=>bindCueStreaming(video));

  const hasKo=Object.keys(vttMap).includes('ko');
  CURRENT.activeLang = hasKo ? 'ko' : (Object.keys(vttMap)[0]||null);
  if(CURRENT.activeLang) await loadSrtToCenter(taskId, CURRENT.activeLang);
}

function updateTrackOptions(vttMap){
  const sel=$('#trackLang'); if(!sel) return;
  const prev=sel.value; sel.innerHTML='';
  Object.keys(vttMap).forEach(lang=>{
    const o=document.createElement('option');
    o.value=lang; o.textContent=lang.toUpperCase();
    sel.appendChild(o);
  });
  sel.value = prev && vttMap[prev] ? prev : (CURRENT.activeLang || Object.keys(vttMap)[0] || '');
}

$('#trackLang')?.addEventListener('change', async (e)=>{
  const lang=e.target.value; CURRENT.activeLang=lang;
  const video=$('#video'); const tracks=video?.textTracks||[];
  for(let i=0;i<tracks.length;i++){
    const t=tracks[i];
    const match=(t.language?.toLowerCase()===lang || t.label?.toLowerCase()===lang);
    t.mode = match ? 'showing' : 'disabled';
  }
  await loadSrtToCenter(CURRENT.taskId, lang);
});

// 중앙 SRT 표시(디버그용)
async function loadSrtToCenter(taskId, lang){
  const pre=$('#srtView'); if(!pre) return;
  try{
    const r=await fetch(`/srt/${taskId}/${lang}`);
    if(!r.ok) return;
    pre.textContent=await r.text();
  }catch(e){ console.error(e); }
}

// ---------- Glossary 스트리밍 (표시 언어로 번역) ----------
function bindCueStreaming(video){
  let tries=0;
  const ensure=()=>{
    const tracks=video.textTracks||[];
    if(!tracks.length && tries<10){ tries++; return setTimeout(ensure, 500); }
    for(let i=0;i<tracks.length;i++){
      const tr=tracks[i];
      const active=(tr.language?.toLowerCase()===(CURRENT.activeLang||'ko'))||(tr.label?.toLowerCase()===(CURRENT.activeLang||'ko'));
      tr.mode = active ? 'showing' : 'disabled';

      tr.oncuechange = async ()=>{
        if(tr.mode!=='showing') return;
        const cues=tr.activeCues||[];
        if(!cues.length) return;
        const cue=cues[cues.length-1];
        const text=(cue?.text||'').replace(/\r?\n/g,' ').trim();
        if(!text) return;

        // 디바운스 + 중복요청 방지
        const lang=(CURRENT.activeLang||'ko');   // 표시 언어
        const src_lang=lang;                     // 매칭 언어(현재 트랙 언어 = 표시 언어)
        const key = lang + '|' + (cue.startTime.toFixed(2)) + '|' + text.slice(0,64);
        const now = Date.now();
        if (key===LAST.cueKey && (now-LAST.ts)<DEBOUNCE_MS) return;
        LAST={cueKey:key, ts:now};

        try{
          const res=await fetch(`/glossary?text=${encodeURIComponent(text)}&lang=${encodeURIComponent(lang)}&src_lang=${encodeURIComponent(src_lang)}`);
          const data=await res.json();
          renderGlossaryItems(data.items||[]);
        }catch(e){ console.error(e); }
      };
    }
  };
  ensure();
}

function renderGlossaryItems(items){
  const list=$('#termList'); if(!list) return;
  list.innerHTML='';
  if(!items.length){
    list.innerHTML='<li class="empty">No terms detected.</li>';
    return;
  }
  for(const g of items){
    const li=document.createElement('li');
    li.innerHTML=`
      <div class="term">${esc(g.term)} <span class="orig">(${esc(g.term_original)})</span></div>
      <div class="def">${esc(g.definition||'')}</div>
    `;
    list.appendChild(li);
  }
}
function esc(s){return String(s||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');}
