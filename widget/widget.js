/**
 * Amit Digital Twin — Embeddable Chat Widget
 *
 * Usage (add to WordPress footer):
 *   <script
 *     src="https://your-app.onrender.com/widget.js"
 *     data-api="https://your-app.onrender.com"
 *     data-position="bottom-right"
 *     data-theme="light"
 *     data-greeting="Hi, I'm a digital version of Amit. Ask me anything about my work, book, or career."
 *   ></script>
 *
 * Loop 7 note: This is the initial widget stub — UI polish happens in Loop 7.
 * The API contract is already wired correctly.
 */

(function () {
  // ── Config from script tag attributes ──────────────────────────────────────
  const script = document.currentScript ||
    document.querySelector('script[data-api]') ||
    document.querySelector('script[src*="widget.js"]');

  const API_BASE   = script?.getAttribute("data-api") || window.location.origin;
  const POSITION   = script?.getAttribute("data-position") || "bottom-right";
  const GREETING   = script?.getAttribute("data-greeting") ||
    "Hi, I'm a digital version of Amit. Ask me anything about my work, book, or career.";

  // ── State ──────────────────────────────────────────────────────────────────
  let isOpen   = false;
  let isTyping = false;
  let history  = [];   // [{role, content}, ...] — last 6 turns, client-managed

  // ── Styles ─────────────────────────────────────────────────────────────────
  const CSS = `
    #amit-widget * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }

    #amit-bubble {
      position: fixed;
      ${POSITION.includes("right") ? "right: 24px;" : "left: 24px;"}
      bottom: 24px;
      width: 56px; height: 56px;
      border-radius: 50%;
      background: #1a1a2e;
      color: white;
      border: none;
      cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      font-size: 22px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.25);
      z-index: 99999;
      transition: transform 0.2s ease;
    }
    #amit-bubble:hover { transform: scale(1.08); }

    #amit-panel {
      position: fixed;
      ${POSITION.includes("right") ? "right: 24px;" : "left: 24px;"}
      bottom: 92px;
      width: 360px;
      max-height: 520px;
      background: #ffffff;
      border-radius: 16px;
      box-shadow: 0 8px 40px rgba(0,0,0,0.18);
      display: flex; flex-direction: column;
      z-index: 99998;
      overflow: hidden;
      transform: scale(0.92) translateY(12px);
      opacity: 0;
      pointer-events: none;
      transition: all 0.22s cubic-bezier(0.34,1.56,0.64,1);
    }
    #amit-panel.open {
      transform: scale(1) translateY(0);
      opacity: 1;
      pointer-events: all;
    }

    #amit-header {
      background: #1a1a2e;
      color: white;
      padding: 14px 16px;
      display: flex; align-items: center; gap: 10px;
    }
    #amit-header .avatar {
      width: 34px; height: 34px; border-radius: 50%;
      background: #e8f4f8;
      display: flex; align-items: center; justify-content: center;
      font-size: 18px; flex-shrink: 0;
    }
    #amit-header .info { flex: 1; }
    #amit-header .name { font-size: 14px; font-weight: 700; }
    #amit-header .status { font-size: 11px; opacity: 0.7; margin-top: 1px; }
    #amit-close {
      background: none; border: none; color: white; cursor: pointer;
      font-size: 18px; opacity: 0.7; padding: 2px 4px;
    }
    #amit-close:hover { opacity: 1; }

    #amit-messages {
      flex: 1; overflow-y: auto; padding: 14px;
      display: flex; flex-direction: column; gap: 10px;
    }
    .amit-msg {
      max-width: 82%; padding: 10px 13px;
      border-radius: 14px; font-size: 13.5px; line-height: 1.55;
    }
    .amit-msg.bot {
      background: #f2f3f7; color: #1a1a2e;
      border-bottom-left-radius: 4px; align-self: flex-start;
    }
    .amit-msg.user {
      background: #1a1a2e; color: white;
      border-bottom-right-radius: 4px; align-self: flex-end;
    }
    .amit-sources {
      font-size: 10.5px; color: #888; margin-top: 5px;
      align-self: flex-start; padding: 0 2px;
    }
    .amit-typing { display: flex; gap: 4px; padding: 8px 12px; }
    .amit-typing span {
      width: 7px; height: 7px; border-radius: 50%; background: #aaa;
      animation: amit-bounce 1.2s infinite;
    }
    .amit-typing span:nth-child(2) { animation-delay: 0.2s; }
    .amit-typing span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes amit-bounce {
      0%, 80%, 100% { transform: translateY(0); }
      40% { transform: translateY(-6px); }
    }

    #amit-input-row {
      display: flex; gap: 8px; padding: 10px 12px;
      border-top: 1px solid #eee;
    }
    #amit-input {
      flex: 1; border: 1px solid #ddd; border-radius: 20px;
      padding: 8px 14px; font-size: 13.5px; outline: none;
      resize: none; line-height: 1.4;
    }
    #amit-input:focus { border-color: #1a1a2e; }
    #amit-send {
      width: 36px; height: 36px; border-radius: 50%;
      background: #1a1a2e; color: white; border: none;
      cursor: pointer; display: flex; align-items: center; justify-content: center;
      font-size: 14px; flex-shrink: 0;
    }
    #amit-send:disabled { opacity: 0.4; cursor: not-allowed; }

    @media (max-width: 420px) {
      #amit-panel { width: calc(100vw - 32px); right: 16px; left: 16px; }
    }
  `;

  // ── DOM Setup ───────────────────────────────────────────────────────────────
  function init() {
    const container = document.createElement("div");
    container.id = "amit-widget";

    const style = document.createElement("style");
    style.textContent = CSS;

    const bubble = document.createElement("button");
    bubble.id = "amit-bubble";
    bubble.innerHTML = "💬";
    bubble.setAttribute("aria-label", "Chat with digital Amit");
    bubble.onclick = togglePanel;

    const panel = document.createElement("div");
    panel.id = "amit-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Chat with Amit's digital twin");
    panel.innerHTML = `
      <div id="amit-header">
        <div class="avatar">👤</div>
        <div class="info">
          <div class="name">Amit (Digital Twin)</div>
          <div class="status">Ask me about my work, book, or career</div>
        </div>
        <button id="amit-close" aria-label="Close chat">✕</button>
      </div>
      <div id="amit-messages"></div>
      <div id="amit-input-row">
        <textarea id="amit-input" rows="1" placeholder="Ask me anything..." maxlength="500"></textarea>
        <button id="amit-send" aria-label="Send">➤</button>
      </div>
    `;

    container.appendChild(style);
    container.appendChild(bubble);
    container.appendChild(panel);
    document.body.appendChild(container);

    document.getElementById("amit-close").onclick = togglePanel;
    document.getElementById("amit-send").onclick = sendMessage;
    const input = document.getElementById("amit-input");
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 80) + "px";
    });

    // Show greeting
    addBotMessage(GREETING);
  }

  // ── UI helpers ──────────────────────────────────────────────────────────────
  function togglePanel() {
    isOpen = !isOpen;
    const panel = document.getElementById("amit-panel");
    panel.classList.toggle("open", isOpen);
    document.getElementById("amit-bubble").innerHTML = isOpen ? "✕" : "💬";
    if (isOpen) document.getElementById("amit-input").focus();
  }

  function addBotMessage(text, sources) {
    const msgs = document.getElementById("amit-messages");
    const msg = document.createElement("div");
    msg.className = "amit-msg bot";
    msg.textContent = text;
    msgs.appendChild(msg);
    if (sources && sources.length) {
      const src = document.createElement("div");
      src.className = "amit-sources";
      src.textContent = "Sources: " + sources.join(" · ");
      msgs.appendChild(src);
    }
    msgs.scrollTop = msgs.scrollHeight;
  }

  function addUserMessage(text) {
    const msgs = document.getElementById("amit-messages");
    const msg = document.createElement("div");
    msg.className = "amit-msg user";
    msg.textContent = text;
    msgs.appendChild(msg);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function showTyping() {
    const msgs = document.getElementById("amit-messages");
    const el = document.createElement("div");
    el.id = "amit-typing";
    el.className = "amit-msg bot amit-typing";
    el.innerHTML = "<span></span><span></span><span></span>";
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function hideTyping() {
    document.getElementById("amit-typing")?.remove();
  }

  // ── Send message ─────────────────────────────────────────────────────────
  async function sendMessage() {
    const input = document.getElementById("amit-input");
    const sendBtn = document.getElementById("amit-send");
    const text = input.value.trim();
    if (!text || isTyping) return;

    addUserMessage(text);
    input.value = "";
    input.style.height = "auto";

    // Add to history
    history.push({ role: "user", content: text });
    if (history.length > 12) history = history.slice(-12);  // keep last 6 turns

    isTyping = true;
    sendBtn.disabled = true;
    showTyping();

    try {
      const response = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: history.slice(0, -1),  // history without the current message
        }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();

      hideTyping();
      addBotMessage(data.reply, data.sources);
      history.push({ role: "assistant", content: data.reply });
      if (history.length > 12) history = history.slice(-12);

    } catch (err) {
      hideTyping();
      addBotMessage("Sorry, something went wrong — please try again in a moment.");
      console.error("Amit widget error:", err);
    } finally {
      isTyping = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  // ── Boot ────────────────────────────────────────────────────────────────────
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
