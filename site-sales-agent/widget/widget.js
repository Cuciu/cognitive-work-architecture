/**
 * Cognitive Work Architecture — Site Sales Agent — Widget Logic
 * ==============================================================
 * Client-side chat logic. Manages conversation state, API calls,
 * Turnstile verification, and lead capture.
 *
 * ARCHITECTURE DECISIONS:
 *   - IIFE wrapper: zero global namespace pollution
 *   - No framework: no React, no jQuery, no dependencies
 *   - All DOM refs guarded: widget degrades gracefully if HTML changes
 *   - History managed client-side: server stores no conversation content
 *   - Turnstile token lifecycle: render → use → renew (every request)
 *
 * BUDGET — CLIENT-SIDE COST CONTROLS:
 *   The server enforces MAX_HISTORY_TURNS = 10 and MAX_PROMPTS_PER_SESSION = 6.
 *   The client ALSO caps history at MAX_HISTORY = 10. This is defense in depth:
 *   even if someone modifies the client, the server enforces its own limits.
 *   But capping client-side means we don't send unnecessary tokens in the
 *   request payload — saving bandwidth and reducing server-side processing.
 *
 * SECURITY — TURNSTILE FLOW:
 *   1. Widget renders invisible Turnstile challenge on panel open
 *   2. User types question → if token ready, send immediately
 *   3. If token not ready → queue question, show typing indicator
 *   4. Token arrives → flush queue, send question
 *   5. After every API call → renew token for next message
 *   6. If token never arrives (4s timeout) → send anyway (server validates)
 *
 * Repo:   cognitive-work-architecture/site-sales-agent
 * Module: widget
 * License: MIT
 */

(function () {
  // ==========================================================================
  // #chat OPENER — ATTACHED FIRST so it works even if later code errors.
  // Opens the chat panel from ANY <a href="...#chat"> on the page.
  //
  // WHY BETTER: Most chat widgets only open from their own button.
  // This lets you put "Chat with us" links anywhere on your site —
  // navigation, CTAs, inline text — and they all open the same widget.
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
      d.innerHTML =
        "Hi! I'm the Sky Alleys assistant 👋 " +
        "Ask me about our AI, cloud, or advisory services.";
      logEl.appendChild(d);
    }
    var inp = document.getElementById("sa-input");
    if (inp) inp.focus();
  }, true); // capture phase — fires before page builder's own handlers

  // Also open on load / hash change (full-URL navigation safety net)
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
  // CONFIG
  // Replace these with your own Cloud Function URLs and Turnstile site key.
  // ==========================================================================
  const AGENT_URL   = "https://REGION-PROJECT_ID.cloudfunctions.net/sales-agent";
  const LEAD_URL    = "https://REGION-PROJECT_ID.cloudfunctions.net/capture-lead";
  const SITE_KEY    = "YOUR_CLOUDFLARE_TURNSTILE_SITE_KEY";

  // BUDGET: Client-side history cap — defense in depth with server's
  // MAX_HISTORY_TURNS. Keeps request payload small.
  const MAX_HISTORY = 10;

  // -----------------------------------------------------------------------
  // STABLE SESSION ID — persisted in localStorage.
  // Same visitor = same session across page loads.
  // Enables session prompt counting and lead attribution on the server.
  // -----------------------------------------------------------------------
  const SESSION_ID = (() => {
    let s = localStorage.getItem("sa_session_id");
    if (!s) {
      s = "sess-" + Math.random().toString(36).slice(2) +
          Date.now().toString(36);
      localStorage.setItem("sa_session_id", s);
    }
    return s;
  })();

  // ==========================================================================
  // TEASERS — Rotating auto-ask prompts
  // ==========================================================================
  // WHY BETTER: Static "Chat with us" buttons get ignored. Rotating teasers
  // that surface a specific proof point or pain point generate curiosity.
  // Each teaser has a pre-loaded question — clicking it opens the chat AND
  // sends the question automatically. The visitor gets value immediately.
  //
  // GUARDRAIL: Shown ONCE per browser session (sessionStorage).
  // No repeat popups. Respects visitor attention.
  // ==========================================================================
  const TEASERS = [
    {
      msg: "👋 <b>Hi there!</b> Cloud migrations are hard — we've made them <b>33% cheaper</b>. Ask me how.",
      ask: "How do you make cloud migrations 33% cheaper?",
    },
    {
      msg: "💡 <b>Hey!</b> AI pilots rarely fail on tech — they fail on governance. We fix that.",
      ask: "Why do AI pilots fail on governance, and how do you fix that?",
    },
    {
      msg: "🤖 <b>Hi!</b> Most AI agents run without cost controls. Ours don't. Ask me why.",
      ask: "How do you add cost controls to AI agents?",
    },
    {
      msg: "🎯 <b>Welcome!</b> Boards want AI answers, not experiments. We deliver them in <b>3 weeks</b>.",
      ask: "What is your 3-week board AI reality check?",
    },
    {
      msg: "🚀 <b>Hey!</b> Tired of paying for consulting hours with no measurable result? So were we.",
      ask: "How is your consulting model different from billing hours?",
    },
    {
      msg: "📊 <b>Hi there!</b> Enterprise AI at scale needs governance baked in — not bolted on. Ask me more.",
      ask: "How do you bake governance into enterprise AI at scale?",
    },
    {
      msg: "🔒 <b>Hello!</b> Regulated industry? We run AI in <b>air-gapped environments</b>. Ask me how.",
      ask: "How do you run AI in air-gapped or regulated environments?",
    },
    {
      msg: "🤝 <b>Hi!</b> Evaluating a tech acquisition? Our <b>8-dimension framework</b> catches what others miss.",
      ask: "Tell me about your 8-dimension technical due diligence framework.",
    },
    {
      msg: "⚡ <b>Hey!</b> We don't bill hours — we co-invest in your outcome. Curious how that works?",
      ask: "How does your outcome-based, co-invested model work?",
    },
    {
      msg: "🏭 <b>Hi there!</b> Factory-floor AI inference — offline, governed, and production-ready. Ask me more.",
      ask: "How do you deploy AI inference on the factory floor?",
    },
  ];
  const chosenTeaser = TEASERS[Math.floor(Math.random() * TEASERS.length)];

  // ==========================================================================
  // STATE
  // ==========================================================================
  let lastTier = "Full";
  let pendingQuestion = null; // question waiting for Turnstile token
  let history = [];           // conversation memory: [{role, text}]

  // ==========================================================================
  // DOM REFS — all optional, null-guarded everywhere
  // GUARDRAIL: If any element is missing, the widget degrades gracefully
  // instead of throwing. This matters when the HTML is embedded in CMSs
  // (WordPress, Webflow) that might strip or rename elements.
  // ==========================================================================
  const $ = (id) => document.getElementById(id);
  const log = $("sa-log"),
    input = $("sa-input"),
    sendBtn = $("sa-send");
  const panel = $("sa-panel"),
    launcher = $("sa-launcher"),
    closeBtn = $("sa-close");
  const teaser = $("sa-teaser"),
    teaserClose = $("sa-teaser-close"),
    dot = $("sa-dot");

  // ==========================================================================
  // TURNSTILE — Bot verification lifecycle
  // ==========================================================================
  // Render once. Token is single-use: consumed on API call, then renewed.
  // If token isn't ready when user sends, queue the message and flush
  // when the token callback fires. 4-second timeout as fallback.
  // ==========================================================================
  let tsWidgetId = null,
    tsToken = "";

  function token() {
    return tsToken;
  }
  function renewTS() {
    tsToken = "";
    if (window.turnstile && tsWidgetId !== null) {
      try { turnstile.reset(tsWidgetId); } catch (e) {}
    }
  }
  function onToken(t) {
    tsToken = t;
    // Flush any queued question now that we have a valid token
    if (pendingQuestion) {
      const q = pendingQuestion;
      pendingQuestion = null;
      doSend(q);
    }
  }
  function renderTS() {
    if (tsWidgetId !== null) return;
    if (!document.getElementById("sa-ts")) return;
    if (window.turnstile && typeof turnstile.render === "function") {
      try {
        tsWidgetId = turnstile.render("#sa-ts", {
          sitekey: SITE_KEY,
          callback: onToken,
          "expired-callback": () => { tsToken = ""; },
          "error-callback": () => { tsToken = ""; },
          appearance: "interaction-only",
        });
      } catch (e) {}
    } else {
      setTimeout(renderTS, 300); // Turnstile script not loaded yet — retry
    }
  }
  renderTS();

  // ==========================================================================
  // HELPERS
  // ==========================================================================

  function addMsg(html, who) {
    if (!log) return;
    const d = document.createElement("div");
    d.className = "sa-msg sa-" + who;
    d.innerHTML = html;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
  }

  /** SECURITY: Escape user input before rendering as HTML.
   *  Prevents XSS even if user types <script> tags in the chat. */
  function escapeHtml(t) {
    const d = document.createElement("div");
    d.textContent = t == null ? "" : String(t);
    return d.innerHTML;
  }

  /** Push a turn to conversation history. Capped at MAX_HISTORY to
   *  bound the payload size sent to the server on each request.
   *  BUDGET: Smaller payloads = less bandwidth = faster requests. */
  function pushHistory(role, text) {
    history.push({ role: role, text: (text || "").toString() });
    if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
  }

  function leadForm() {
    return (
      '<div class="sa-lead">' +
      "<p>Leave your email and we'll follow up:</p>" +
      '<input id="sa-lead-email" type="email" placeholder="you@company.com"/>' +
      '<input id="sa-lead-company" type="text" placeholder="Company (optional)"/>' +
      '<button id="sa-lead-submit">Send</button>' +
      '<div id="sa-lead-status"></div></div>'
    );
  }

  /** Submit lead capture form to capture-lead Cloud Function.
   *  Includes session_id for agent-to-lead attribution and
   *  current tier for segmentation (Full vs. Degraded). */
  async function submitLead() {
    const emailEl = $("sa-lead-email"),
      companyEl = $("sa-lead-company"),
      status = $("sa-lead-status");
    const email = ((emailEl && emailEl.value) || "").trim();
    const company = ((companyEl && companyEl.value) || "").trim();
    if (!status) return;
    if (!email) {
      status.textContent = "Please enter a valid email.";
      return;
    }
    status.textContent = "Sending…";
    try {
      const r = await fetch(LEAD_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          company,
          session_id: SESSION_ID,
          source: "floating_bubble",
          tier: lastTier,
          cf_token: token(),
        }),
      });
      const j = await r.json();
      status.textContent =
        j.status === "captured"
          ? "Thanks! We'll be in touch."
          : j.error || "Something went wrong.";
      renewTS();
    } catch (e) {
      status.textContent = "Network error — please try again.";
      renewTS();
    }
  }

  // ==========================================================================
  // CORE SEND — sends full history; booking button on intent;
  //             lead form only at session end
  // ==========================================================================
  // ARCHITECTURE: The client sends the FULL conversation history with every
  // request. The server is stateless regarding conversation content — it only
  // stores metadata (prompt count, budget). This means:
  //   - No server-side session storage of PII (conversation text)
  //   - Client controls what context the model sees
  //   - Server enforces its own history cap independently (defense in depth)
  // ==========================================================================

  async function doSend(msg) {
    addMsg(escapeHtml(msg), "user");
    if (sendBtn) sendBtn.disabled = true;
    pushHistory("user", msg);

    // Typing indicator
    addMsg('<span class="sa-typing">…</span>', "bot");
    const typing = log ? log.lastChild : null;

    try {
      const r = await fetch(AGENT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: msg,
          session_id: SESSION_ID,
          history: history,
          cf_token: token(),
        }),
      });
      const j = await r.json();
      if (typing) typing.remove();

      lastTier = j.tier || lastTier;
      const isEnd = j.tier === "SessionCap" || j.tier === "Degraded";

      if (j.html) {
        // Degraded/cap HTML — rendered directly from server
        addMsg(j.html, "bot");
        if (isEnd) addMsg(leadForm(), "bot");
      } else if (j.reply) {
        // Normal agent response
        addMsg(escapeHtml(j.reply).replace(/\n/g, "<br>"), "bot");
        pushHistory("model", j.reply);

        // Booking CTA — rendered only when backend determines intent + turn gate
        if (j.cta_html) addMsg(j.cta_html, "bot");

        // Lead form — ONLY at session end, never mid-conversation
        if (isEnd) addMsg(leadForm(), "bot");
      } else if (j.error) {
        addMsg("⚠️ " + escapeHtml(j.error), "bot");
      } else {
        addMsg("Hmm, I didn't catch that. Try rephrasing?", "bot");
      }

      // Wire up lead form submit if rendered
      const sub = $("sa-lead-submit");
      if (sub) sub.onclick = submitLead;

      renewTS(); // SECURITY: Fresh token for next message
    } catch (e) {
      if (typing) typing.remove();
      addMsg("Network error — please try again.", "bot");
      renewTS();
    } finally {
      if (sendBtn) sendBtn.disabled = false;
      if (input) input.focus();
    }
  }

  // ==========================================================================
  // QUEUE SEND — handles Turnstile token timing
  // ==========================================================================
  // SECURITY: If the token isn't ready yet (Turnstile hasn't completed
  // the invisible challenge), queue the message and wait for the callback.
  // 4-second timeout as fallback — if the token never arrives, send anyway
  // and let the server validate (it will reject if Turnstile is configured).
  // ==========================================================================

  function queueSend(msg) {
    msg = (msg || "").trim();
    if (!msg) return;
    if (token()) {
      doSend(msg);
    } else {
      pendingQuestion = msg;
      addMsg('<span class="sa-typing">…</span>', "bot");
      setTimeout(() => {
        if (pendingQuestion) {
          const q = pendingQuestion;
          pendingQuestion = null;
          if (
            log && log.lastChild && log.lastChild.querySelector &&
            log.lastChild.querySelector(".sa-typing")
          ) {
            log.lastChild.remove();
          }
          doSend(q);
        }
      }, 4000); // Fallback timeout
    }
  }

  function sendFromInput() {
    const msg = ((input && input.value) || "").trim();
    if (!msg) return;
    if (input) input.value = "";
    queueSend(msg);
  }

  // ==========================================================================
  // OPEN / CLOSE
  // ==========================================================================

  function openChat() {
    if (panel) panel.style.display = "flex";
    if (launcher) launcher.style.display = "none";
    if (teaser) teaser.style.display = "none";
    if (dot) dot.style.display = "none";
    renderTS();
    if (input) input.focus();
    if (log && !log.childElementCount) {
      addMsg(
        "Hi! I'm the Sky Alleys assistant 👋 " +
        "Ask me about our AI, cloud, or advisory services — " +
        "or how we deliver outcome-based results.",
        "bot"
      );
    }
  }

  function openChatWithQuestion(question) {
    if (panel) panel.style.display = "flex";
    if (launcher) launcher.style.display = "none";
    if (teaser) teaser.style.display = "none";
    if (dot) dot.style.display = "none";
    renderTS();
    if (log && !log.childElementCount) {
      addMsg("Hi! I'm the Sky Alleys assistant 👋", "bot");
    }
    if (question) {
      queueSend(question);
    } else if (input) {
      input.focus();
    }
  }

  // ==========================================================================
  // WIRE EVENTS — all null-guarded
  // ==========================================================================
  if (sendBtn) sendBtn.onclick = sendFromInput;
  if (input) input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendFromInput();
  });
  if (launcher) launcher.onclick = openChat;
  if (teaser) teaser.onclick = () => openChatWithQuestion(chosenTeaser.ask);
  if (teaserClose) teaserClose.onclick = (e) => {
    e.stopPropagation();
    teaser.style.display = "none";
  };
  if (closeBtn) closeBtn.onclick = () => {
    if (panel) panel.style.display = "none";
    if (launcher) launcher.style.display = "flex";
  };

  // ==========================================================================
  // TEASER — show once per browser session
  // GUARDRAIL: sessionStorage ensures no repeat popups. Respects attention.
  // ==========================================================================
  if (teaser && !sessionStorage.getItem("sa_teaser_shown")) {
    setTimeout(() => {
      if (!panel || panel.style.display !== "flex") {
        const t = $("sa-teaser-text");
        if (t) t.innerHTML = chosenTeaser.msg;
        teaser.style.display = "block";
        sessionStorage.setItem("sa_teaser_shown", "1");
      }
    }, 4000); // 4-second delay — let the page settle before prompting
  }

  // If page already loaded at #chat, open immediately
  openIfHashChat();
})();
