// soundlab tool bar: the same menu on the Galaxy, Voice Pads, Mix Stage and Mix Check pages.
// Put <div id="hpNav"></div> where it should sit; without one it floats at the top center.
(function () {
  const me = document.currentScript, base = me ? me.src.replace(/[^/]*$/, "") : "";
  const here = location.pathname.split("/").pop() || "index.html";
  const css = `
  .hpnav { display:flex; gap:4px; align-items:center; font:13px system-ui, -apple-system, "Segoe UI", sans-serif; z-index:40; }
  .hpnav.float { position:fixed; top:8px; left:max(50%, 800px); transform:translateX(-50%); background:#0f141cee; border:1px solid #232b38; border-radius:9px; padding:4px; }
  .hpnav a, .hpnav button { color:#dde3ec; text-decoration:none; background:#1b2533; border:1px solid #232b38; border-radius:6px; padding:3px 10px; cursor:pointer; font:inherit; }
  .hpnav a:hover, .hpnav button:hover { border-color:#7aa2ff; }
  .hpnav .on { background:#22304a; border-color:#7aa2ff; }
  .hpmix { position:fixed; top:46px; left:max(50%, 800px); transform:translateX(-50%); width:min(460px, 94vw); max-height:70vh; overflow:auto; z-index:41;
    background:#0f141c; border:1px solid #232b38; border-radius:10px; padding:10px 12px; color:#dde3ec; font:13px system-ui, sans-serif; display:none; box-shadow:0 8px 30px #000a; }
  .hpmix h3 { font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:#8794a6; margin:8px 0 4px; }
  .hpmix a { display:flex; justify-content:space-between; gap:10px; color:#dde3ec; text-decoration:none; padding:4px 6px; border-radius:6px; }
  .hpmix a:hover { background:#18202c; } .hpmix small { color:#8794a6; white-space:nowrap; }
  .hpmix .hint { color:#8794a6; font-size:12px; margin-top:8px; } .hpmix code { color:#f2a93b; }`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  function build() {
    const slot = document.getElementById("hpNav"), nav = document.createElement("nav");
    nav.className = "hpnav" + (slot ? "" : " float");
    const link = (href, label, page) => `<a href="${base}${href}" class="${here === page ? "on" : ""}">${label}</a>`;
    nav.innerHTML = link("index.html", "Galaxy", "index.html") + link("voice.html", "Voice Pads", "voice.html")
      + link("stage.html", "Mix Stage", "stage.html") + `<button type="button" class="hpMixBtn">Mixes</button>` + link("coach.html", "Get This Sound", "coach.html") + link("pipeline.html", "How it works", "pipeline.html");
    (slot || document.body).appendChild(nav);
    navEl = nav; addExtra();
    const panel = document.createElement("div"); panel.className = "hpmix"; document.body.appendChild(panel);
    nav.querySelector(".hpMixBtn").onclick = e => { e.stopPropagation(); panel.style.display = panel.style.display === "block" ? "none" : "block"; if (panel.style.display === "block") fill(panel); };
    document.addEventListener("click", e => { if (!panel.contains(e.target)) panel.style.display = "none"; });
  }
  function fill(panel) {
    const list = window.MIXES || [], fmt = s => { const d = new Date(s); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" }); };
    const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const row = m => `<a href="${base}${m.href}"><span>${esc(m.name)}</span><small>${esc(m.info || "")} ${fmt(m.date)}</small></a>`;
    const stages = list.filter(m => m.kind === "stage"), checks = list.filter(m => m.kind === "check");
    panel.innerHTML = `<h3>Mix Stage songs</h3>${stages.map(row).join("") || '<div class="hint">None yet.</div>'}`
      + `<h3>Mix Check reports</h3>${checks.map(row).join("") || '<div class="hint">None yet.</div>'}`
      + `<div class="hint">Add a song: <code>py soundlab.py stage song.rpp</code> draws its Mix Stage, <code>py soundlab.py check song.wav</code> writes a Mix Check report. Both show up here.</div>`;
  }
  // extra tabs of your own: window.EXTRA_TABS = [{ label, href }] in data/tabs.js (kept out of git with the rest of data/)
  let navEl = null;
  function addExtra() {
    if (!navEl || !window.EXTRA_TABS || navEl.dataset.extra) return; navEl.dataset.extra = "1";
    for (const t of window.EXTRA_TABS) { const a = document.createElement("a"); a.href = base + t.href; a.textContent = t.label;
      if (location.pathname.endsWith("/" + t.href.split("/").pop())) a.className = "on"; navEl.appendChild(a); }
  }
  const s = document.createElement("script"); s.src = base + "data/mixes.js"; s.onerror = () => {}; document.head.appendChild(s);
  const x = document.createElement("script"); x.src = base + "data/tabs.js"; x.onload = addExtra; x.onerror = () => {}; document.head.appendChild(x);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build); else build();
})();
