// static/js/app.js

const $ = (s) => document.querySelector(s);

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
  // vttMap의 항목이 video에 없으면 추가
  const existing = new Set([...video.querySelectorAll('track')].map(t => t.srclang));
  Object.entries(vttMap || {}).forEach(([lang, href]) => {
    if (existing.has(lang)) return;
    const tr = document.createElement('track');
    tr.kind = 'subtitles';
    tr.srclang = lang;
    tr.label = lang;
    tr.src = href;
    tr.addEventListener('load', () => {
      // TextTrack 로드된 뒤 cuechange 바인딩
      const tt = tr.track;
      tt.mode = (lang === $('#trackLang').value) ? 'showing' : 'hidden';
      tt.oncuechange = () => {
        const cue = tt.activeCues && tt.activeCues[0];
        if (!cue) return;
        streamGlossary(cue.text, $('#trackLang').value);
      };
    });
    video.appendChild(tr);
  });

  // 드롭다운 갱신
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

  // 선택 변경 시 자막 트랙 표시/용어집 갱신
  const sel = $('#trackLang');
  sel.onchange = () => {
    const want = sel.value;
    [...video.textTracks].forEach(tt => {
      tt.mode = (tt.language === want || tt.label === want) ? 'showing' : 'hidden';
    });
    loadSrt(taskId, want);
  };

  // 메타데이터 로드 후 기본 선택
  video.addEventListener('loadedmetadata', () => {
    const sel = $('#trackLang');
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
  const file = $('#fileInput').files[0];
  if (!file) { alert('영상 파일을 선택하세요.'); return; }
  syncHidden('A');
  const langs = $('#langs').value;

  const fd = new FormData();
  fd.append('file', file);
  fd.append('target_langs', langs);
  fd.append('asr_model', 'medium');
  fd.append('src_lang', 'ko');

  setStatus('Uploading…');
  setProgress(0.01);
  const res = await fetch('/upload', { method: 'POST', body: fd });
  const js = await res.json();
  const taskId = js.task_id;

  // 초기 mount (비디오 소스만 올림)
  mountPlayerOnce(taskId);

  // 진행 중에도 vtt 추가되면 즉시 붙임
  while (true) {
    const st = await pollStatus(taskId);
    if (st.outputs && st.outputs.vtt) {
      ensureTracks($('#video'), taskId, st.outputs.vtt);
      // 선택된 자막의 SRT를 중앙에 표시
      const sel = $('#trackLang');
      if (sel.value) loadSrt(taskId, sel.value);
    }
    if (st.state === 'SUCCESS') break;
    if (st.state === 'FAILURE') { alert(st.message || 'Task failed'); break; }
    await new Promise(r => setTimeout(r, 1000));
  }
}

// ---------- 시작(Skip Whisper) ----------
async function startSkip() {
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
}

window.addEventListener('DOMContentLoaded', () => {
  // 체크박스 변경 시 숨은 필드 동기화
  document.querySelectorAll('.langOptA').forEach(el => el.addEventListener('change', () => syncHidden('A')));
  document.querySelectorAll('.langOptB').forEach(el => el.addEventListener('change', () => syncHidden('B')));
  syncHidden('A'); syncHidden('B');

  $('#startBtn')?.addEventListener('click', startWhisper);
  $('#startBtn2')?.addEventListener('click', startSkip);
});
