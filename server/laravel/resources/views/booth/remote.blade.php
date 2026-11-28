<!doctype html>
{{-- NEO PHOTO remote: the guest's phone shoots the booth. Opened from the QR on the booth screen. --}}
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="csrf-token" content="{{ csrf_token() }}">
<meta name="robots" content="noindex">
<meta name="theme-color" content="#F6F2EC">
<title>NEO PHOTO · Remote</title>
<style>
  :root { --bg:#F6F2EC; --card:#FFFDFA; --ink:#2B2523; --soft:#7A6E68; --line:#E5DCD3;
          --accent:#C85A32; --accent-dk:#A4431F; --ok:#2F6B4F; }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--ink);
               font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  body { display:flex; flex-direction:column; min-height:100dvh;
         padding: max(14px, env(safe-area-inset-top)) 16px max(18px, env(safe-area-inset-bottom)); }
  header { display:flex; align-items:baseline; justify-content:space-between; }
  .brand { font-weight:800; letter-spacing:.18em; font-size:13px; color:var(--accent); }
  .count { font-weight:800; font-size:15px; }
  .msg { margin:10px 0 12px; font-size:18px; font-weight:700; line-height:1.35; min-height:2.7em; }
  .stage { position:relative; flex:1; min-height:200px; background:#E9E2D9; border-radius:14px;
           overflow:hidden; display:flex; align-items:center; justify-content:center; }
  .stage img { width:100%; height:100%; object-fit:contain; background:#1d1918; }
  .stage .empty { color:var(--soft); font-weight:700; text-align:center; padding:24px; line-height:1.5; }
  .big { position:absolute; inset:0; display:none; align-items:center; justify-content:center;
         background:rgba(29,25,24,.55); color:#fff; font-size:120px; font-weight:900; }
  .thumbs { display:flex; gap:8px; overflow-x:auto; padding:10px 2px 2px; min-height:70px; }
  .thumbs img { height:60px; border-radius:8px; border:2px solid transparent; flex:none; }
  .thumbs img.on { border-color:var(--accent); }
  .controls { display:flex; flex-direction:column; align-items:center; gap:12px; padding-top:14px; }
  .shoot { width:150px; height:150px; border-radius:50%; border:6px solid #fff;
           box-shadow:0 0 0 3px var(--accent); background:var(--accent); color:#fff;
           font-size:28px; font-weight:900; letter-spacing:.06em; }
  .shoot:active { background:var(--accent-dk); }
  .shoot:disabled { background:#D8CEC5; box-shadow:0 0 0 3px #D8CEC5; color:#fff; }
  .finish { width:100%; max-width:420px; height:52px; border-radius:10px; border:1px solid var(--line);
            background:var(--card); color:var(--ink); font-size:17px; font-weight:800; }
  .finish:disabled { color:#C2B8AF; }
  .hint { font-size:13px; color:var(--soft); text-align:center; }
  .end { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center;
         text-align:center; gap:10px; }
  .end h1 { font-size:26px; margin:0; } .end p { color:var(--soft); font-size:16px; margin:0; max-width:320px; }
</style>
</head>
<body>
@if (!$key)
  <div class="end">
    <div class="brand">NEO PHOTO</div>
    @if ($reason === 'taken')
      <h1>Remote sudah dipakai</h1>
      <p>Remote sesi ini sudah tersambung ke HP lain. Pakai HP yang pertama scan ya, Kak.</p>
    @else
      <h1>Sesi sudah selesai</h1>
      <p>Link remote ini sudah tidak berlaku. Silakan keluar ruangan untuk memilih foto.</p>
    @endif
  </div>
@else
  <header><span class="brand">NEO PHOTO · REMOTE</span><span class="count" id="count"></span></header>
  <div class="msg" id="msg">Menyambung ke mesin…</div>
  <div class="stage" id="stage">
    <div class="empty" id="empty">Masuk ke ruangan, atur posisi, lalu tekan <b>FOTO</b>.<br>
      Hasil fotonya muncul di sini.</div>
    <img id="photo" alt="" style="display:none">
    <div class="big" id="big"></div>
  </div>
  <div class="thumbs" id="thumbs"></div>
  <div class="controls">
    <button class="shoot" id="shoot" disabled>FOTO</button>
    <button class="finish" id="finish" disabled>SELESAI</button>
    <div class="hint" id="hint"></div>
  </div>
  <div class="end" id="done" style="display:none">
    <div class="brand">NEO PHOTO</div>
    <h1>Foto selesai!</h1>
    <p>Silakan keluar ruangan untuk memilih foto favorit di layar mesin.</p>
  </div>
<script>
(function () {
  const base = @json(url('/r/' . $key));
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const $ = (id) => document.getElementById(id);
  let photos = 0, shown = 0, pendingUntil = 0, ended = false, fails = 0, phase = '', sentAt = {phase: '', taken: -1};

  function end() {
    if (ended) return; ended = true;
    ['msg', 'stage', 'thumbs'].forEach(id => $(id).style.display = 'none');
    document.querySelector('.controls').style.display = 'none';
    document.querySelector('header').style.display = 'none';
    $('done').style.display = 'flex';
  }

  function show(n) {
    shown = n;
    $('empty').style.display = 'none';
    $('photo').style.display = 'block';
    $('photo').src = base + '/photo/' + n + '?v=' + n;
    document.querySelectorAll('#thumbs img').forEach(t => t.classList.toggle('on', +t.dataset.n === n));
  }

  function addThumbs(upto) {
    for (let n = photos + 1; n <= upto; n++) {
      const t = document.createElement('img');
      t.src = base + '/photo/' + n + '?v=' + n; t.dataset.n = n;
      t.onclick = () => show(n);
      $('thumbs').appendChild(t);
    }
    if (upto > photos) { photos = upto; show(upto); $('thumbs').scrollLeft = 1e6; }
  }

  function render(s) {
    if (s.phase === 'done' || s.phase === 'closed') { end(); return; }
    phase = s.phase;
    // A command was sent: buttons stay off until the booth has moved on.
    if (Date.now() < pendingUntil && (s.phase !== sentAt.phase || s.taken !== sentAt.taken)) pendingUntil = 0;
    $('msg').textContent = s.message || '';
    $('count').textContent = (s.taken || 0) + ' / ' + (s.max || 0) + ' foto';
    const pending = Date.now() < pendingUntil;
    $('shoot').disabled = !s.can_shoot || pending;
    $('finish').disabled = !s.can_finish || pending;
    const big = $('big');
    if (s.phase === 'countdown' && s.count > 0) { big.textContent = s.count; big.style.display = 'flex'; }
    else if (s.phase === 'saving') { big.textContent = '📸'; big.style.display = 'flex'; }
    else big.style.display = 'none';
    $('hint').textContent = (s.taken || 0) < (s.min || 1)
      ? 'Minimal ' + s.min + ' foto sebelum SELESAI'
      : 'Foto sampai puas, tekan SELESAI kalau sudah cukup';
    if ((s.photos || 0) > photos) addThumbs(s.photos);
    lastTaken = s.taken || 0;
  }
  let lastTaken = 0;

  async function poll() {
    if (ended) return;
    try {
      const r = await fetch(base + '/state', {credentials: 'same-origin', cache: 'no-store',
                                              headers: {'Accept': 'application/json'}});
      if (r.status === 404) { end(); return; }
      if (r.ok) { fails = 0; render(await r.json()); }
      else if (++fails > 5) $('msg').textContent = 'Koneksi bermasalah, mencoba lagi…';
    } catch (e) {
      if (++fails > 3) $('msg').textContent = 'Koneksi internet HP terputus, mencoba lagi…';
    }
    setTimeout(poll, 1000);
  }

  async function send(cmd) {
    if (navigator.vibrate) navigator.vibrate(30);
    pendingUntil = Date.now() + 6000;
    sentAt = {phase: phase, taken: lastTaken};
    $('shoot').disabled = true; $('finish').disabled = true;
    try {
      const r = await fetch(base + '/cmd', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json',
                  'X-CSRF-TOKEN': csrf, 'X-Requested-With': 'XMLHttpRequest'},
        body: JSON.stringify({cmd})});
      if (r.status === 404) end();
      else if (r.status === 419) location.reload();
    } catch (e) { pendingUntil = 0; }
  }

  $('shoot').onclick = () => send('shoot');
  $('finish').onclick = () => { if (confirm('Sudah cukup fotonya? Setelah ini tidak bisa foto lagi.')) send('finish'); };
  poll();
})();
</script>
@endif
</body>
</html>
