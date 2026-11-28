// soundlab MIDI input: lets a keyboard or pad controller (an Arturia MiniLab 3, for example) play the pages.
// Pads are notes on MIDI channel 10 (the MiniLab 3 default: bank A is 36-43, bank B is 44-51), keys are any other channel.
// Pages listen with  HPMIDI.on(e => ...)  where e = { kind: "pad"|"key"|"cc", on, note, pad, vel (0-1), ch (1-16), cc, val }.
(function () {
  const subs = [], st = { inputs: [], pick: "", last: "" };
  try { st.pick = localStorage.getItem("soundlab_midi_in") || ""; } catch (e) {}
  const badge = document.createElement("div");
  badge.style.cssText = "position:fixed;right:10px;bottom:10px;z-index:45;font:12px system-ui,sans-serif;color:#8794a6;background:#0f141cee;border:1px solid #232b38;border-radius:8px;padding:4px 9px;cursor:pointer;max-width:320px";
  badge.title = "MIDI controller. Click to pick which one to listen to.";
  const label = t => { badge.textContent = t; };
  function emit(e) { for (const f of subs) { try { f(e); } catch (err) { console.error(err); } } }
  function onMsg(m) {
    const [s, a, b] = m.data, type = s & 0xf0, ch = (s & 0x0f) + 1;
    if (type === 0x90 || type === 0x80) {
      const on = type === 0x90 && b > 0, pad = ch === 10 && a >= 36 && a <= 51 ? a - 36 : -1;
      emit({ kind: pad >= 0 ? "pad" : "key", on, note: a, pad, vel: b / 127, ch });
      if (on) { st.last = (pad >= 0 ? "pad " + (pad + 1) : "key " + a); label("MIDI: " + st.name + "  ·  " + st.last); }
    } else if (type === 0xb0) emit({ kind: "cc", cc: a, val: b / 127, ch });
  }
  function hook(access) {
    st.inputs = [...access.inputs.values()];
    const want = st.pick ? st.inputs.filter(i => i.name === st.pick) : st.inputs;
    st.inputs.forEach(i => i.onmidimessage = null);
    want.forEach(i => i.onmidimessage = onMsg);
    st.name = want.length === 1 ? want[0].name : want.length ? want.length + " controllers" : "none";
    label(st.inputs.length ? "MIDI: " + st.name : "MIDI: no controller found");
  }
  let access = null;
  function start() {
    if (!navigator.requestMIDIAccess) { label("MIDI: this browser has no MIDI (use Chrome or Edge)"); return; }
    navigator.requestMIDIAccess().then(a => { access = a; hook(a); a.onstatechange = () => hook(a); }, () => label("MIDI: blocked, allow it in the address bar"));
  }
  badge.onclick = () => {
    if (!access) return start();
    const names = st.inputs.map(i => i.name);
    const pick = prompt("Listen to which controller? Leave empty for all.\n\n" + names.join("\n"), st.pick);
    if (pick === null) return; st.pick = pick.trim();
    try { localStorage.setItem("soundlab_midi_in", st.pick); } catch (e) {}
    hook(access);
  };
  window.HPMIDI = { on: f => subs.push(f) };
  const go = () => { document.body.appendChild(badge); label("MIDI: starting..."); start(); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go); else go();
})();
