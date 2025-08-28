// static/js/app.js


const $ = (s) => document.querySelector(s);

// (A) Glossary 결과 캐시: task+lang 묶음으로 1회 로드
const GLOSS_CACHE = {};
const cacheKey = (taskId, lang) => `${taskId}:${lang}`;

async function ensureGlossaryMap(taskId, lang) {
  const key = cacheKey(taskId, lang);
  if (GLOSS_CACHE[key]) return GLOSS_CACHE[key];
  const r = await fetch(`/glossary_srt/${taskId}/${lang}`);
  const js = await r.json();
  GLOSS_CACHE[key] = js.items_by_id || {};
  return GLOSS_CACHE[key];
}

function renderGlossaryItems(items) {
  const ul = $('#termList');
  if (!ul) return;
  if (!items || !items.length) {
    ul.innerHTML = '<li class="muted">No terms detected.</li>';
    return;
  }
  ul.innerHTML = '';
  items.forEach(it => {
    const li = document.createElement('li');
    // 표시는 대상 언어 기준 (API가 이미 번역/표현 맞춰줌)
    li.innerHTML = `<span class="term"><span class="orig">[${it.term_original}]</span> ${it.term}</span><div class="def">${it.definition || ''}</div>`;
    ul.appendChild(li);
  });
}

// ---------- 언어 체크박스 동기화 ----------
function collectLangs(group) {
  const cls = group === 'A' ? '.langOptA' : '.langOptB';
  const vals = [...document.querySelectorAll(cls + ':checked')].map(i => i.value.trim());
  return [...new Set(vals)].filter(Boolean).join(',') || 'en';
}
function syncHidden(group) {
  const id = group === 'A' ? 'langs' : 'langs2';
  const el = document.getElementById(id);
  if (el) el.value = collectLangs(group);
}

function setStatus(msg) {
  const el = $('#statusMsg');
  if (el) el.textContent = msg;
}
function setProgress(p) {
  const fill = $('#progressFill');
  if (fill) fill.style.width = `${Math.round((p || 0) * 100)}%`;
}

// ---------- 상태 폴링 ----------
async function pollStatus(taskId) {
  const r = await fetch(`/status/${taskId}`);
  const js = await r.json();
  if (js.progress != null) setProgress(js.progress);
  if (js.message) setStatus(js.message);
  return js;
}

// ---------- 플레이어/트랙 ----------
function ensureTracks(video, taskId, vttMap) {
  const existing = new Set([...video.querySelectorAll('track')].map(t => t.srclang));
  Object.entries(vttMap || {}).forEach(([lang, href]) => {
    if (existing.has(lang)) return;
    const tr = document.createElement('track');
    tr.kind = 'subtitles';
    tr.srclang = lang;
    tr.label = lang;
    tr.src = href;
    tr.addEventListener('load', async () => {
      const tt = tr.track;
      tt.mode = (lang === $('#trackLang').value) ? 'showing' : 'hidden';

      // (B) 이 언어의 glossary 맵을 한 번만 로드 (캐시)
      await ensureGlossaryMap(taskId, lang);

      tt.oncuechange = () => {
        const cue = tt.activeCues && tt.activeCues[0];
        if (!cue) return;

        // (C) 네트워크 호출 없이 캐시에서 바로 찾아 그리기
        const key = cacheKey(taskId, lang);
        // cue.id(=SRT 인덱스)가 우선, 없는 경우 timing을 키로 fallback
        const id = (cue.id && String(cue.id).trim()) || `${cue.startTime.toFixed(3)}-->${cue.endTime.toFixed(3)}`;
        const items = GLOSS_CACHE[key][id] || [];
        renderGlossaryItems(items);
      };
    });
    video.appendChild(tr);
  });

  // 드롭다운 갱신 (기존 로직 그대로)
  const sel = $('#trackLang');
  const has = new Set([...sel.options].map(o => o.value));
  Object.keys(vttMap || {}).forEach(l => {
    if (!has.has(l)) {
      const opt = document.createElement('option');
      opt.value = l; opt.textContent = l;
      sel.appendChild(opt);
    }
  });
}

function mountPlayerOnce(taskId) {
  const video = $('#video');
  if (video.dataset.mounted === '1') return;

  video.innerHTML = '';
  const src = document.createElement('source');
  src.src = `/video/${taskId}`;
  src.type = 'video/mp4';
  video.appendChild(src);

  video.dataset.mounted = '1';
  video.load();

  const sel = $('#trackLang');
  sel.onchange = async () => {
    const want = sel.value;
    // 트랙 표시 토글
    [...video.textTracks].forEach(tt => {
      tt.mode = (tt.language === want || tt.label === want) ? 'showing' : 'hidden';
    });
    // 선택 바꿀 때 SRT 중앙 표시
    loadSrt(taskId, want);
    // 선택 언어의 glossary 맵을 미리 준비 (한 번만)
    await ensureGlossaryMap(taskId, want);
  };

  video.addEventListener('loadedmetadata', () => {
    if (!sel.value && sel.options.length) {
      sel.value = [...sel.options].find(o => o.value !== 'ko')?.value || 'ko';
    }
  });
}
async function loadSrt(taskId, lang) {
  if (!lang) return;
  const r = await fetch(`/srt/${taskId}/${lang}`);
  const txt = await r.text();
  $('#srtView').textContent = txt;
}

async function streamGlossary(text, lang) {
  try {
    const r = await fetch(`/glossary?lang=${encodeURIComponent(lang)}&text=${encodeURIComponent(text)}`);
    const js = await r.json();
    const ul = $('#termList');
    if (!ul) return;
    if (!js.items || !js.items.length) {
      ul.innerHTML = '<li class="muted">No terms detected.</li>';
      return;
    }
    ul.innerHTML = '';
    js.items.forEach(it => {
      const li = document.createElement('li');
      li.innerHTML = `<strong>${it.term}</strong> — ${it.definition}`;
      ul.appendChild(li);
    });
  } catch {}
}

// ---------- 시작(Whisper) ----------
async function startWhisper() {
  const btn = $('#startBtn');
  if (btn) btn.disabled = true;   // ✅ 시작 시 비활성화
  try {
    const file = $('#fileInput').files[0];
    if (!file) { alert('영상 파일을 선택하세요.'); return; }
    syncHidden('A');
    const langs = $('#langs').value;

    const fd = new FormData();
    fd.append('file', file);
    fd.append('target_langs', langs);
    fd.append('asr_model', 'base');
    fd.append('src_lang', 'ko');

    setStatus('Uploading…');
    setProgress(0.01);
    const res = await fetch('/upload', { method: 'POST', body: fd });
    const js = await res.json();
    const taskId = js.task_id;

    mountPlayerOnce(taskId);

    while (true) {
      const st = await pollStatus(taskId);
      if (st.outputs && st.outputs.vtt) {
        ensureTracks($('#video'), taskId, st.outputs.vtt);
        const sel = $('#trackLang');
        if (sel.value) loadSrt(taskId, sel.value);
      }
      if (st.state === 'SUCCESS') break;
      if (st.state === 'FAILURE') { alert(st.message || 'Task failed'); break; }
      await new Promise(r => setTimeout(r, 1000));
    }
  } finally {
    if (btn) btn.disabled = false;  // ✅ 끝나면 복구
  }
}

// ---------- 시작(Skip Whisper) ----------
async function startSkip() {
  const btn = $('#startBtn2');
  if (btn) btn.disabled = true;
  try {
    const v = $('#fileInput2Video').files[0];
    const s = $('#fileInput2Srt').files[0];
    if (!v || !s) { alert('영상과 SRT 파일을 모두 선택하세요.'); return; }
    syncHidden('B');
    const langs = $('#langs2').value;

    const fd = new FormData();
    fd.append('video', v);
    fd.append('srt', s);
    fd.append('srt_lang', $('#srtLang').value);
    fd.append('target_langs', langs);

    setStatus('Uploading (skip whisper)…');
    setProgress(0.01);
    const res = await fetch('/upload_with_srt', { method: 'POST', body: fd });
    const js = await res.json();
    const taskId = js.task_id;

    mountPlayerOnce(taskId);

    while (true) {
      const st = await pollStatus(taskId);
      if (st.outputs && st.outputs.vtt) {
        ensureTracks($('#video'), taskId, st.outputs.vtt);
        const sel = $('#trackLang');
        if (sel.value) loadSrt(taskId, sel.value);
      }
      if (st.state === 'SUCCESS') break;
      if (st.state === 'FAILURE') { alert(st.message || 'Task failed'); break; }
      await new Promise(r => setTimeout(r, 1000));
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}


window.addEventListener('DOMContentLoaded', () => {
  // 체크박스 변경 시 숨은 필드 동기화
  document.querySelectorAll('.langOptA').forEach(el => el.addEventListener('change', () => syncHidden('A')));
  document.querySelectorAll('.langOptB').forEach(el => el.addEventListener('change', () => syncHidden('B')));
  syncHidden('A'); syncHidden('B');

  $('#startBtn')?.addEventListener('click', startWhisper);
  $('#startBtn2')?.addEventListener('click', startSkip);
});
