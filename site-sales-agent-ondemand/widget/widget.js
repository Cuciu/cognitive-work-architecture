(function () {
  // ==========================================================================
  // #chat OPENER — ATTACHED FIRST so it works even if later code errors.
  // Opens the chat panel from ANY <a href="...#chat"> on the page.
  // ==========================================================================
  document.addEventListener("click", function (e) {
    var link = e.target.closest('a[href*="#chat"]');
    if (!link) return;
    e.preventDefault();
    var p = document.getElementById("sa-panel");
    var l = document.getElementById("sa-launcher");
    var t = document.getElementById("sa-teaser");
    var dt = document.getElementById("sa-dot");
    if (p) p.style.display = "flex";
    if (l) l.style.display = "none";
    if (t) t.style.display = "none";
    if (dt) dt.style.display = "none";
    var logEl = document.getElementById("sa-log");
    if (logEl && !logEl.childElementCount) {
      var d = document.createElement("div");
      d.className = "sa-msg sa-bot";
      d.innerHTML = "Hi! 👋 Ask me about our services.";
      logEl.appendChild(d);
    }
    var inp = document.getElementById("sa-input");
    if (inp) inp.focus();
  }, true);

  // Also open on load / hash change if the URL contains #chat
  function openIfHashChat() {
    if (window.location.hash === "#chat") {
      var p = document.getElementById("sa-panel");
      var l = document.getElementById("sa-launcher");
      if (p) p.style.display = "flex";
      if (l) l.style.display = "none";
    }
  }
  window.addEventListener("hashchange", openIfHashChat);

  // ==========================================================================
  // CONFIG — your two function endpoints + Turnstile site key
  // ==========================================================================
  // CHANGE THESE to your deployed Cloud Run / Cloud Functions URLs.
  const AGENT_URL = "https://REGION-YOUR_PROJECT.cloudfunctions.net/sales-agent";
  const LEAD_URL  = "https://REGION-YOUR_PROJECT.cloudfunctions.net/capture-lead";
  const SITE_KEY  = "YOUR_CLOUDFLARE_TURNSTILE_SITE_KEY";
  const MAX_HISTORY = 10;

  // Stable per-visitor session id
  const SESSION_ID = (() => {
    let s = localStorage.getItem("sa_session_id");
    if (!s) { s = "sess-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem("sa_session_id", s); }
    return s;
  })();

  // ==========================================================================
  // Teasers (rotating auto-ask prompts)
  // Replace with your own service-specific messages and questions.
  // ==========================================================================
  const TEASERS = [
    { msg: "👋 <b>Hi there!</b> Have a question about our services? Ask away.",
      ask: "What services do you offer?" },
    { msg: "💡 <b>Hey!</b> Curious how we can help? Let's chat.",
      ask: "How can you help my business?" },
    { msg: "🚀 <b>Welcome!</b> We deliver outcomes, not hours. Ask me how.",
      ask: "How is your engagement model different?" },
    // Add more teasers to match your service areas and proof points.
  ];
  const chosenTeaser = TEASERS[Math.floor(Math.random() * TEASERS.length)];

  // ==========================================================================
  // State
  // ==========================================================================
  let lastTier = "Full";
  let pendingQuestion = null;
  let history = [];

  // ==========================================================================
  // DOM refs (all optional — guarded everywhere)
  // ==========================================================================
  const $ = (id) => document.getElementById(id);
  const log = $("sa-log"), input = $("sa-input"), sendBtn = $("sa-send");
  const panel = $("sa-panel"), launcher = $("sa-launcher"), closeBtn = $("sa-close");
  const teaser = $("sa-teaser"), teaserClose = $("sa-teaser-close"), dot = $("sa-dot");

  // ==========================================================================
  // Turnstile: render once; flush pending question when token arrives
  // ==========================================================================
  let tsWidgetId = null, tsToken = "";
  function token(){ return tsToken; }
  function renewTS(){ tsToken = ""; if (window.turnstile && tsWidgetId !== null) { try { turnstile.reset(tsWidgetId); } catch(e){} } }
  function onToken(t){
    tsToken = t;
    if (pendingQuestion){ const q = pendingQuestion; pendingQuestion = null; doSend(q); }
  }
  function renderTS(){
    if (tsWidgetId !== null) return;
    if (!document.getElementById("sa-ts")) return;
    if (window.turnstile && typeof turnstile.render === "function") {
      try {
        tsWidgetId = turnstile.render('#sa-ts', {
          sitekey: SITE_KEY,
          callback: onToken,
          'expired-callback': () => { tsToken = ""; },
          'error-callback': () => { tsToken = ""; },
          appearance: 'interaction-only'
        });
      } catch(e){}
    } else { setTimeout(renderTS, 300); }
  }
  renderTS();

  // ==========================================================================
  // Helpers
  // ==========================================================================
  function addMsg(html, who) {
    if (!log) return;
    const d = document.createElement("div");
    d.className = "sa-msg sa-" + who;
    d.innerHTML = html;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
  }
  function escapeHtml(t){
    const d = document.createElement("div");
    d.textContent = (t == null ? "" : String(t));
    return d.innerHTML;
  }
  function pushHistory(role, text){
    history.push({ role: role, text: (text || "").toString() });
    if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
  }

  function leadForm(){
    return `<div class="sa-lead">
      <p>Leave your email and we'll follow up:</p>
      <input id="sa-lead-email" type="email" placeholder="you@company.com"/>
      <input id="sa-lead-company" type="text" placeholder="Company (optional)"/>
      <button id="sa-lead-submit">Send</button>
      <div id="sa-lead-status"></div></div>`;
  }

  async function submitLead(){
    const emailEl = $("sa-lead-email"), companyEl = $("sa-lead-company"), status = $("sa-lead-status");
    const email = (emailEl && emailEl.value || "").trim();
    const company = (companyEl && companyEl.value || "").trim();
    if (!status) return;
    if (!email){ status.textContent = "Please enter a valid email."; return; }
    status.textContent = "Sending…";
    try{
      const r = await fetch(LEAD_URL, { method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ email, company, session_id: SESSION_ID, source:"floating_bubble", tier: lastTier, cf_token: token() }) });
      const j = await r.json();
      status.textContent = j.status === "captured" ? "Thanks! We'll be in touch." : (j.error || "Something went wrong.");
      renewTS();
    }catch(e){ status.textContent = "Network error — please try again."; renewTS(); }
  }

  // ==========================================================================
  // Core send — sends full history; booking button on intent; lead form at end
  // ==========================================================================
  async function doSend(msg){
    addMsg(escapeHtml(msg), "user");
    if (sendBtn) sendBtn.disabled = true;
    pushHistory("user", msg);
    addMsg('<span class="sa-typing">…</span>', "bot");
    const typing = log ? log.lastChild : null;
    try{
      const r = await fetch(AGENT_URL, { method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
          message: msg,
          session_id: SESSION_ID,
          history: history,
          cf_token: token()
        }) });
      const j = await r.json();
      if (typing) typing.remove();
      lastTier = j.tier || lastTier;
      const isEnd = (j.tier === "SessionCap" || j.tier === "Degraded");

      if (j.html){
        addMsg(j.html, "bot");
        if (isEnd) addMsg(leadForm(), "bot");
      } else if (j.reply){
        addMsg(escapeHtml(j.reply).replace(/\n/g, "<br>"), "bot");
        pushHistory("model", j.reply);
        if (j.cta_html) addMsg(j.cta_html, "bot");
        if (isEnd) addMsg(leadForm(), "bot");
      } else if (j.error){
        addMsg("⚠️ " + escapeHtml(j.error), "bot");
      } else {
        addMsg("Hmm, I didn't catch that. Try rephrasing?", "bot");
      }

      const sub = $("sa-lead-submit");
      if (sub) sub.onclick = submitLead;
      renewTS();
    }catch(e){
      if (typing) typing.remove();
      addMsg("Network error — please try again.", "bot");
      renewTS();
    }finally{
      if (sendBtn) sendBtn.disabled = false;
      if (input) input.focus();
    }
  }

  // Queue a message: send now if token ready, else hold until token arrives
  function queueSend(msg){
    msg = (msg || "").trim(); if (!msg) return;
    if (token()){ doSend(msg); }
    else {
      pendingQuestion = msg;
      addMsg('<span class="sa-typing">…</span>', "bot");
      setTimeout(() => {
        if (pendingQuestion){ const q = pendingQuestion; pendingQuestion = null;
          if (log && log.lastChild && log.lastChild.querySelector && log.lastChild.querySelector('.sa-typing')) log.lastChild.remove();
          doSend(q);
        }
      }, 4000);
    }
  }

  function sendFromInput(){
    const msg = (input && input.value || "").trim(); if (!msg) return;
    if (input) input.value = "";
    queueSend(msg);
  }

  // ==========================================================================
  // Open / close
  // ==========================================================================
  function openChat(){
    if (panel) panel.style.display = "flex";
    if (launcher) launcher.style.display = "none";
    if (teaser) teaser.style.display = "none";
    if (dot) dot.style.display = "none";
    renderTS();
    if (input) input.focus();
    if (log && !log.childElementCount)
      addMsg("Hi! 👋 Ask me about our services — or how we can help your business.", "bot");
  }

  function openChatWithQuestion(question){
    if (panel) panel.style.display = "flex";
    if (launcher) launcher.style.display = "none";
    if (teaser) teaser.style.display = "none";
    if (dot) dot.style.display = "none";
    renderTS();
    if (log && !log.childElementCount) addMsg("Hi! 👋", "bot");
    if (question){ queueSend(question); } else if (input){ input.focus(); }
  }

  // ==========================================================================
  // Wire events (all guarded)
  // ==========================================================================
  if (sendBtn) sendBtn.onclick = sendFromInput;
  if (input) input.addEventListener("keydown", e => { if (e.key === "Enter") sendFromInput(); });
  if (launcher) launcher.onclick = openChat;
  if (teaser) teaser.onclick = () => openChatWithQuestion(chosenTeaser.ask);
  if (teaserClose) teaserClose.onclick = (e) => { e.stopPropagation(); teaser.style.display = "none"; };
  if (closeBtn) closeBtn.onclick = () => {
    if (panel) panel.style.display = "none";
    if (launcher) launcher.style.display = "flex";
  };

  // ==========================================================================
  // Show the teaser bubble once per session
  // ==========================================================================
  if (teaser && !sessionStorage.getItem("sa_teaser_shown")){
    setTimeout(() => {
      if (!panel || panel.style.display !== "flex"){
        const t = $("sa-teaser-text");
        if (t) t.innerHTML = chosenTeaser.msg;
        teaser.style.display = "block";
        sessionStorage.setItem("sa_teaser_shown", "1");
      }
    }, 4000);
  }

  // If the page already loaded at #chat, open immediately
  openIfHashChat();
})();
