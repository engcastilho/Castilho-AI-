"use strict";

const tbody = document.getElementById("users");
const message = document.getElementById("admin-message");

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (res.status === 401) {
    window.location.href = "/login";
    return null;
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `Erro ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

function button(label, onClick, kind = "") {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.className = `small ${kind}`;
  b.addEventListener("click", onClick);
  return b;
}

async function act(path, options) {
  message.textContent = "";
  try {
    await api(path, options);
    await load();
  } catch (err) {
    message.textContent = err.message;
    message.className = "form-message error";
  }
}

async function load() {
  const users = await api("/api/admin/users");
  if (!users) return;
  tbody.replaceChildren();

  for (const u of users) {
    const tr = document.createElement("tr");

    const name = document.createElement("td");
    name.textContent = u.username + (u.is_admin ? " (admin)" : "");

    const status = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = "pill " + (u.is_approved ? "ok" : "pending");
    pill.textContent = u.is_approved ? "Ativo" : "Aguardando / bloqueado";
    status.appendChild(pill);

    const created = document.createElement("td");
    created.textContent = new Date(u.created_at * 1000).toLocaleDateString("pt-BR");

    const actionsCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "actions";
    actionsCell.appendChild(actions);
    if (!u.is_admin) {
      if (u.is_approved) {
        actions.append(button("Bloquear", () => act(`/api/admin/users/${u.id}/block`, { method: "POST" })));
      } else {
        actions.append(button("Aprovar", () => act(`/api/admin/users/${u.id}/approve`, { method: "POST" }), "primary"));
      }
      const del = button("Excluir", () => {
        // Confirmação em dois cliques, sem caixa de diálogo do navegador.
        if (del.dataset.confirm) act(`/api/admin/users/${u.id}`, { method: "DELETE" });
        else {
          del.dataset.confirm = "1";
          del.textContent = "Confirmar exclusão";
        }
      }, "danger");
      actions.append(del);
    }

    tr.append(name, status, created, actionsCell);
    tbody.appendChild(tr);
  }
}

load().catch((err) => {
  message.textContent = err.message;
  message.className = "form-message error";
});
