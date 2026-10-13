"use strict";

const form = document.getElementById("auth-form");
const tabLogin = document.getElementById("tab-login");
const tabRegister = document.getElementById("tab-register");
const submit = document.getElementById("submit");
const message = document.getElementById("form-message");
const password = document.getElementById("password");
const passwordHint = document.getElementById("password-hint");

let mode = "login"; // "login" ou "register"

function setMode(next) {
  mode = next;
  const isLogin = mode === "login";
  tabLogin.classList.toggle("active", isLogin);
  tabRegister.classList.toggle("active", !isLogin);
  tabLogin.setAttribute("aria-selected", String(isLogin));
  tabRegister.setAttribute("aria-selected", String(!isLogin));
  submit.textContent = isLogin ? "Entrar" : "Criar conta";
  password.autocomplete = isLogin ? "current-password" : "new-password";
  passwordHint.classList.toggle("hidden", isLogin);
  showMessage("");
}

function showMessage(text, kind = "error") {
  message.textContent = text;
  message.className = `form-message ${kind}`;
}

tabLogin.addEventListener("click", () => setMode("login"));
tabRegister.addEventListener("click", () => setMode("register"));

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = form.username.value.trim();
  if (!username || !password.value) {
    showMessage("Preencha usuário e senha.");
    return;
  }

  submit.disabled = true;
  try {
    const res = await fetch(`/api/auth/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password: password.value }),
    });
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      const detail = typeof data.detail === "string" ? data.detail : "Verifique os dados e tente de novo.";
      showMessage(detail);
    } else if (data.status === "pending") {
      showMessage(data.message, "success");
      password.value = "";
      setTimeout(() => setMode("login"), 4000);
    } else {
      window.location.href = "/";
    }
  } catch {
    showMessage("Não foi possível falar com o servidor. Tente de novo.");
  } finally {
    submit.disabled = false;
  }
});
