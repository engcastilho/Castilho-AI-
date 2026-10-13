"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  sidebar: $("sidebar"),
  conversations: $("conversations"),
  newChat: $("new-chat"),
  menuToggle: $("menu-toggle"),
  title: $("chat-title"),
  messages: $("messages"),
  empty: $("empty"),
  form: $("composer"),
  input: $("input"),
  send: $("send"),
  stop: $("stop"),
};

const state = {
  conversations: [],
  currentId: null,
  controller: null, // AbortController da resposta em andamento
};

// ---------- Renderização ----------

function renderMarkdown(text) {
  if (window.marked && window.DOMPurify) {
    return DOMPurify.sanitize(marked.parse(text));
  }
  const div = document.createElement("div");
  div.textContent = text;
  div.style.whiteSpace = "pre-wrap";
  return div.outerHTML;
}

function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function addMessage(role, text = "") {
  els.empty.classList.add("hidden");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (role === "assistant") div.innerHTML = renderMarkdown(text);
  else div.textContent = text;
  els.messages.appendChild(div);
  scrollToBottom();
  return div;
}

function clearMessages() {
  els.messages.querySelectorAll(".msg").forEach((m) => m.remove());
  els.empty.classList.remove("hidden");
}

function renderConversations() {
  els.conversations.replaceChildren();
  for (const conv of state.conversations) {
    const item = document.createElement("div");
    item.className = "conv" + (conv.id === state.currentId ? " active" : "");

    const open = document.createElement("button");
    open.className = "open";
    open.type = "button";
    open.textContent = conv.title;
    open.title = conv.title;
    open.addEventListener("click", () => openConversation(conv.id));

    const del = document.createElement("button");
    del.className = "delete";
    del.type = "button";
    del.textContent = "✕";
    del.setAttribute("aria-label", "Excluir conversa");
    del.addEventListener("click", () => deleteConversation(conv.id));

    item.append(open, del);
    els.conversations.appendChild(item);
  }
}

function setStreaming(on) {
  els.send.classList.toggle("hidden", on);
  els.stop.classList.toggle("hidden", !on);
  els.input.disabled = on;
  els.newChat.disabled = on;
}

function autoResize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 200) + "px";
}

// ---------- API ----------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.status === 204 ? null : res.json();
}

async function loadConversations() {
  state.conversations = await api("/api/conversations");
  renderConversations();
}

async function openConversation(id) {
  if (state.controller) return;
  const conv = await api(`/api/conversations/${id}`);
  state.currentId = id;
  els.title.textContent = conv.title;
  clearMessages();
  for (const m of conv.messages) addMessage(m.role, m.text);
  renderConversations();
  els.sidebar.classList.remove("open");
  els.input.focus();
}

function newConversation() {
  if (state.controller) return;
  state.currentId = null;
  els.title.textContent = "Castilho AI";
  clearMessages();
  renderConversations();
  els.sidebar.classList.remove("open");
  els.input.focus();
}

async function deleteConversation(id) {
  if (state.controller) return;
  if (!confirm("Excluir esta conversa?")) return;
  await api(`/api/conversations/${id}`, { method: "DELETE" });
  if (id === state.currentId) newConversation();
  await loadConversations();
}

// ---------- Envio e streaming ----------

async function sendMessage(text) {
  els.messages.querySelectorAll(".msg.error, .msg.notice").forEach((m) => m.remove());

  if (!state.currentId) {
    const conv = await api("/api/conversations", { method: "POST" });
    state.currentId = conv.id;
    await loadConversations();
  }

  const userEl = addMessage("user", text);
  const botEl = addMessage("assistant");
  botEl.classList.add("typing");

  state.controller = new AbortController();
  setStreaming(true);

  let answer = "";
  let pending = false;
  const paint = () => {
    pending = false;
    botEl.innerHTML = renderMarkdown(answer);
    scrollToBottom();
  };

  // Desfaz a troca na tela (ela não foi salva) e devolve o texto para a caixa.
  const rollback = (message, kind = "error") => {
    userEl.remove();
    botEl.remove();
    els.input.value = text;
    autoResize();
    if (message) addMessage(kind, message);
    if (!els.messages.querySelector(".msg.user, .msg.assistant")) els.empty.classList.remove("hidden");
  };

  try {
    const res = await fetch(`/api/conversations/${state.currentId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text }),
      signal: state.controller.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === "delta") {
          answer += ev.text;
          if (!pending) {
            pending = true;
            requestAnimationFrame(paint);
          }
        } else if (ev.type === "done") {
          paint();
          botEl.classList.remove("typing");
          if (!answer.trim()) addMessage("notice", "O modelo não retornou texto. Tente reformular a pergunta.");
          if (ev.stop_reason === "max_tokens") addMessage("notice", "A resposta atingiu o limite de tamanho e foi cortada.");
          if (ev.title) els.title.textContent = ev.title;
          await loadConversations();
        } else if (ev.type === "error") {
          rollback(ev.message);
        }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") rollback("Resposta interrompida.", "notice");
    else rollback("Falha de conexão com o servidor. Tente novamente.");
  } finally {
    state.controller = null;
    setStreaming(false);
    els.input.focus();
  }
}

// ---------- Eventos ----------

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.input.value.trim();
  if (!text || state.controller) return;
  els.input.value = "";
  autoResize();
  sendMessage(text);
});

els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});

els.input.addEventListener("input", autoResize);
els.stop.addEventListener("click", () => state.controller?.abort());
els.newChat.addEventListener("click", newConversation);
els.menuToggle.addEventListener("click", () => els.sidebar.classList.toggle("open"));

loadConversations().catch(() => addMessage("error", "Não foi possível carregar as conversas."));
els.input.focus();
