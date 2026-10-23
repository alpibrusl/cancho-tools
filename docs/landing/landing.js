// The landing page's behaviour: a replay of a real session, two tabs, a copy button and a few reveals. No dependencies.
// The session itself (window.CFLOW) is written by scripts/site.py from what the built tools printed.
(function () {
  var doc = document, root = doc.documentElement;
  root.classList.remove("nojs");
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  function $(id) { return doc.getElementById(id); }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  // ---- the replay -----------------------------------------------------------------------------------------------------------------
  var screen = $("screen"), fileBox = $("file"), laneA = $("lane-a"), laneB = $("lane-b"), verdict = $("verdict");
  var mode = "guarded", timer = null, running = 0, settled = false;
  function span(c, t) { return '<span class="' + c + '">' + esc(t) + "</span>"; }
  function setState(s) {
    if (!s) { return; }
    fileBox.innerHTML = '<span class="fn">notes.txt</span>' + s.file.map(function (l) { return '<span class="ln ' + l[1] + '">' + esc(l[0]) + "</span>"; }).join("");
    laneA.innerHTML = "<b>agent A</b>" + s.a.map(function (e) { return '<span class="ev ' + e[1] + '">' + esc(e[0]) + "</span>"; }).join("");
    laneB.innerHTML = "<b>agent B</b>" + s.b.map(function (e) { return '<span class="ev ' + e[1] + '">' + esc(e[0]) + "</span>"; }).join("");
    verdict.className = "verdict " + (s.verdict[1] || "");
    verdict.textContent = s.verdict[0];
  }
  function finish(items) {
    clearTimeout(timer); settled = true;
    screen.innerHTML = items.map(function (it) { return span(it.l[0], it.l[1]); }).join("\n");
    setState(items[items.length - 1].s);
  }
  function play(which) {
    var items = window.CFLOW[which].items, token = ++running;
    clearTimeout(timer); settled = false;
    if (reduce) { finish(items); return; }
    screen.innerHTML = "";
    setState(window.CFLOW[which].start);
    var i = 0, j = 0, html = "";
    (function tick() {
      if (token !== running) { return; }
      if (i >= items.length) { settled = true; return; }
      var it = items[i], text = it.l[1], fast = it.l[0] !== "d";
      j += fast ? 400 : 3;
      screen.innerHTML = html + span(it.l[0], text.slice(0, j));
      screen.scrollTop = screen.scrollHeight;
      if (j >= text.length) {
        html += span(it.l[0], text) + "\n"; if (it.s) { setState(it.s); }
        i++; j = 0;
        timer = setTimeout(tick, it.pause || 650);
      } else { timer = setTimeout(tick, 13); }
    })();
  }
  if (screen && window.CFLOW) {
    var segs = doc.querySelectorAll("#modeseg button");
    function pick(which) {
      mode = which;
      for (var k = 0; k < segs.length; k++) { segs[k].setAttribute("aria-selected", segs[k].dataset.mode === which ? "true" : "false"); }
      play(which);
    }
    for (var k = 0; k < segs.length; k++) { segs[k].addEventListener("click", function () { pick(this.dataset.mode); }); }
    $("replay").addEventListener("click", function () { play(mode); });
    window.cdemo = { play: function (w) { pick(w || mode); }, mode: function () { return mode; }, done: function () { return settled; } };
    // Start when the demo comes into view, once.
    var started = false;
    function start() { if (!started) { started = true; pick("plain"); } }
    if ("IntersectionObserver" in window && !reduce) {
      new IntersectionObserver(function (es, o) { if (es[0].isIntersecting) { o.disconnect(); start(); } }, { threshold: 0.35 }).observe($("demo-stage"));
    } else { start(); }
  }

  // ---- humans and agents ------------------------------------------------------------------------------------------------------
  var tabs = doc.querySelectorAll("#audience button"), panels = { humans: $("humans"), agents: $("agents") };
  function show(which, scroll) {
    for (var k = 0; k < tabs.length; k++) { tabs[k].setAttribute("aria-selected", tabs[k].dataset.panel === which ? "true" : "false"); }
    for (var name in panels) { if (panels[name]) { panels[name].classList.toggle("on", name === which); } }
    if (scroll) { try { history.replaceState(null, "", "#" + which); } catch (e) {} }
  }
  for (var t = 0; t < tabs.length; t++) { tabs[t].addEventListener("click", function () { show(this.dataset.panel, true); }); }
  function fromHash() {
    var h = location.hash.replace("#", "");
    if (h === "agents" || h === "humans") { show(h, false); $("audience-section").scrollIntoView(); }
    else { show("humans", false); }
  }
  window.addEventListener("hashchange", fromHash);
  fromHash();

  // ---- copy the prompt ------------------------------------------------------------------------------------------------------------
  var copyBtn = $("copyprompt");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var text = $("agentprompt").textContent;
      function done(ok) { copyBtn.textContent = ok ? "Copied" : "Select and copy"; setTimeout(function () { copyBtn.textContent = "Copy"; }, 2000); }
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); }); }
      else { var r = doc.createRange(); r.selectNodeContents($("agentprompt")); var sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); done(false); }
    });
  }

  // ---- reveals ----------------------------------------------------------------------------------------------------------------------
  var reveal = doc.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && !reduce) {
    var io = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }); }, { threshold: 0.25 });
    for (var r = 0; r < reveal.length; r++) { io.observe(reveal[r]); }
  } else { for (var q = 0; q < reveal.length; q++) { reveal[q].classList.add("in"); } }
})();
