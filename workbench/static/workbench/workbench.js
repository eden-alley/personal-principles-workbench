(function () {
  "use strict";

  var body = document.body;
  var toggle = document.querySelector("[data-nav-toggle]");
  var scrim = document.querySelector("[data-nav-close]");
  var nav = document.getElementById("side-nav");
  var mobile = window.matchMedia("(max-width: 900px)");

  function setNav(open) {
    body.classList.toggle("nav-open", open);
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (nav) {
      nav.inert = mobile.matches && !open;
      nav.setAttribute("aria-hidden", mobile.matches && !open ? "true" : "false");
    }
  }

  if (toggle) toggle.addEventListener("click", function () {
    setNav(!body.classList.contains("nav-open"));
  });
  if (scrim) scrim.addEventListener("click", function () { setNav(false); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && body.classList.contains("nav-open")) {
      setNav(false);
      if (toggle) toggle.focus();
    }
  });
  mobile.addEventListener("change", function () { setNav(false); });
  setNav(false);
  document.querySelectorAll(".side-nav a").forEach(function (link) {
    link.addEventListener("click", function () { setNav(false); });
  });

  document.querySelectorAll("form[data-busy-form]").forEach(function (form) {
    form.addEventListener("invalid", function (event) {
      var details = event.target.closest("details");
      if (details) details.open = true;
    }, true);
    form.addEventListener("submit", function () {
      var submit = form.querySelector("button[type='submit']");
      if (!submit || submit.disabled) return;
      submit.disabled = true;
      submit.setAttribute("aria-busy", "true");
      submit.dataset.originalLabel = submit.textContent;
      submit.textContent = "处理中…";
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) event.preventDefault();
    });
  });

  var dirty = false;
  document.querySelectorAll("form[data-busy-form] input, form[data-busy-form] textarea, form[data-busy-form] select").forEach(function (field) {
    field.addEventListener("input", function () { dirty = true; });
    field.addEventListener("change", function () { dirty = true; });
  });
  document.querySelectorAll("form[data-busy-form]").forEach(function (form) {
    form.addEventListener("submit", function () { dirty = false; });
  });
  window.addEventListener("beforeunload", function (event) {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  function textOf(element, value) {
    if (element) element.textContent = value == null ? "" : String(value);
  }

  function csrfFor(form) {
    var input = form && form.querySelector("input[name='csrfmiddlewaretoken']");
    return input ? input.value : "";
  }

  function jsonRequest(url, options) {
    var request = options || {};
    request.credentials = "same-origin";
    request.headers = Object.assign({"Accept": "application/json"}, request.headers || {});
    return fetch(url, request).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var error = new Error(data.message || "请求失败，请稍后重试。");
          error.payload = data;
          throw error;
        }
        return data;
      });
    });
  }

  var statusPanel = document.querySelector("[data-ai-status-panel]");
  if (statusPanel) {
    var statusMessage = statusPanel.querySelector("[data-ai-status-message]");
    var statusLabel = statusPanel.querySelector("[data-ai-status-label]");
    var statusProvider = statusPanel.querySelector("[data-ai-status-provider]");
    var statusModel = statusPanel.querySelector("[data-ai-status-model]");
    var statusError = statusPanel.querySelector("[data-ai-status-error]");
    var refreshButton = statusPanel.querySelector("[data-ai-refresh]");
    var loginButton = statusPanel.querySelector("[data-codex-login]");
    var authPanel = statusPanel.querySelector("[data-codex-auth]");
    var authMessage = statusPanel.querySelector("[data-codex-auth-message]");
    var authLink = statusPanel.querySelector("[data-codex-auth-link]");
    var cancelLoginButton = statusPanel.querySelector("[data-codex-login-cancel]");
    var loginTimer = null;
    var loginStarted = 0;

    function showStatusError(message) {
      textOf(statusError, message);
      if (statusError) statusError.hidden = !message;
    }

    function renderStatus(data) {
      data = data || {};
      textOf(statusMessage, data.message || "状态尚未读取。");
      textOf(statusLabel, data.label || "未检查");
      textOf(statusProvider, data.provider || "未选择");
      textOf(statusModel, data.model || "未读取");
      if (loginButton) loginButton.hidden = data.code === "connected";
      document.querySelectorAll("[data-ai-mode-label], [data-app-mode]").forEach(function (label) {
        var providerLabel = {codex: "Codex", api: "普通 API", demo: "演示"}[data.provider] || "AI";
        label.textContent = providerLabel + " · " + (data.label || "未检查");
      });
      if (statusLabel) {
        statusLabel.classList.remove("is-connected", "is-pending", "is-error", "is-neutral");
        var code = data.code || "neutral";
        statusLabel.classList.add(code === "connected" ? "is-connected" : code === "pending" ? "is-pending" : code === "neutral" || code === "unknown" ? "is-neutral" : "is-error");
      }
      showStatusError("");
    }

    function refreshStatus(active) {
      var url = statusPanel.getAttribute("data-status-url");
      if (!url) return Promise.resolve();
      if (refreshButton) {
        refreshButton.disabled = true;
        refreshButton.setAttribute("aria-busy", "true");
      }
      return jsonRequest(url + (active ? (url.indexOf("?") === -1 ? "?refresh=1" : "&refresh=1") : ""), {headers: {"Accept": "application/json"}}).then(function (data) {
        renderStatus(data);
      }).catch(function (error) {
        showStatusError(error.message || "状态刷新失败，请重试。");
      }).then(function () {
        if (refreshButton) {
          refreshButton.disabled = false;
          refreshButton.removeAttribute("aria-busy");
        }
      });
    }

    function stopLoginPolling() {
      if (loginTimer) window.clearTimeout(loginTimer);
      loginTimer = null;
      loginStarted = 0;
      if (cancelLoginButton) cancelLoginButton.hidden = true;
    }

    function showAuth(data) {
      if (!authPanel) return;
      authPanel.hidden = false;
      textOf(authMessage, data.message || "请在官方页面完成登录。");
      if (authLink) {
        var link = data.auth_url || "";
        var safe = typeof link === "string" && link.indexOf("https://") === 0;
        authLink.hidden = !safe;
        if (safe) authLink.href = link;
        else authLink.removeAttribute("href");
      }
      if (cancelLoginButton) cancelLoginButton.hidden = data.code !== "pending";
    }

    function pollLogin() {
      var now = Date.now();
      if (!loginStarted || now - loginStarted >= 300000) {
        stopLoginPolling();
        showStatusError("登录等待已超过 5 分钟，可以重新点击“登录 Codex”重试。");
        return;
      }
      jsonRequest(statusPanel.getAttribute("data-login-status-url"), {headers: {"Accept": "application/json"}}).then(function (data) {
        renderStatus(data);
        if (data.code === "connected") {
          stopLoginPolling();
          if (authPanel) authPanel.hidden = true;
          return;
        }
        if (data.code !== "pending") {
          stopLoginPolling();
          showAuth(data);
          showStatusError(data.message || "登录失败，可以重试。");
          return;
        }
        showAuth(data);
        loginTimer = window.setTimeout(pollLogin, 3000);
      }).catch(function (error) {
        stopLoginPolling();
        showStatusError(error.message || "登录状态读取失败，可以重试。");
        if (authPanel) authPanel.hidden = false;
        textOf(authMessage, "状态读取失败，请重试。");
      });
    }

    if (refreshButton) refreshButton.addEventListener("click", function () { refreshStatus(true); });
    if (loginButton) loginButton.addEventListener("click", function () {
      var form = document.querySelector("form[data-busy-form]");
      loginButton.disabled = true;
      loginButton.setAttribute("aria-busy", "true");
      showStatusError("");
      jsonRequest(statusPanel.getAttribute("data-login-url"), {
        method: "POST",
        headers: {"X-CSRFToken": csrfFor(form), "Accept": "application/json"},
        body: new URLSearchParams()
      }).then(function (data) {
        renderStatus(data);
        showAuth(data);
        if (data.code === "connected") {
          stopLoginPolling();
          if (authPanel) authPanel.hidden = true;
          return;
        }
        if (data.code !== "pending") {
          stopLoginPolling();
          showStatusError(data.message || "登录失败，可以重试。");
          return;
        }
        loginStarted = Date.now();
        pollLogin();
      }).catch(function (error) {
        showStatusError(error.message || "登录请求失败，可以重试。");
      }).then(function () {
        loginButton.disabled = false;
        loginButton.removeAttribute("aria-busy");
      });
    });
    if (cancelLoginButton) cancelLoginButton.addEventListener("click", function () {
      stopLoginPolling();
      var form = document.querySelector("form[data-busy-form]");
      jsonRequest(statusPanel.getAttribute("data-login-cancel-url"), {
        method: "POST",
        headers: {"X-CSRFToken": csrfFor(form), "Accept": "application/json"},
        body: new URLSearchParams()
      }).then(function (data) {
        renderStatus(data);
        if (authPanel) authPanel.hidden = true;
      }).catch(function (error) { showStatusError(error.message || "取消登录失败，可以重试。"); });
    });
  }

  var streamForm = document.querySelector("form[data-ai-stream]");
  if (streamForm) {
    var streamSubmit = streamForm.querySelector("[data-ai-submit]");
    var streamStatusTitle = document.querySelector("[data-preview-status-title]");
    var streamStatusMessage = document.querySelector("[data-preview-status-message]");
    var streamOutput = streamForm.querySelector("[data-stream-output]");
    var streamReadable = streamForm.querySelector("[data-stream-readable]");
    var streamProgress = streamForm.querySelector("[data-stream-progress]");
    var streamError = streamForm.querySelector("[data-stream-error]");
    var streamErrorMessage = streamForm.querySelector("[data-stream-error-message]");
    var streamRetry = streamForm.querySelector("[data-stream-retry]");
    var streamRaw = "";
    var streamReadableText = "";
    var streamTerminal = false;
    var streamAbortRequested = false;
    var streamTimeoutTriggered = false;
    var streamController = null;
    var streamTimeout = null;

    function readableFromObject(value, parts) {
      if (!value || typeof value !== "object") return;
      if (Array.isArray(value)) { value.forEach(function (item) { readableFromObject(item, parts); }); return; }
      ["answer", "statement", "recommendation", "question", "choice", "reason", "expectation"].forEach(function (key) {
        if (typeof value[key] === "string" && value[key].trim()) parts.push(value[key].trim());
      });
      if (value.principle) readableFromObject(value.principle, parts);
      if (Array.isArray(value.options)) value.options.forEach(function (option) {
        if (option && typeof option.action === "string") parts.push(option.action + (option.tradeoff ? "：" + option.tradeoff : ""));
      });
    }

    function renderDelta(fragment) {
      if (typeof fragment !== "string") return;
      streamRaw += fragment;
      var parts = [];
      try { readableFromObject(JSON.parse(streamRaw), parts); } catch (ignore) {
        ["answer", "statement", "recommendation", "question", "choice", "reason", "expectation"].some(function (key) {
          var pattern = new RegExp('"' + key + '"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)');
          var match = pattern.exec(streamRaw);
          if (match) {
            try { parts.push(JSON.parse('"' + match[1] + '"')); } catch (innerIgnore) { parts.push(match[1]); }
            return true;
          }
          return false;
        });
      }
      var nextText = parts.join("\n\n");
      if (nextText) streamReadableText = nextText;
      textOf(streamReadable, streamReadableText);
      textOf(streamProgress, streamReadableText ? "已接收内容，仍在整理。" : "已接收 " + streamRaw.length + " 个字符。");
    }

    function setStreamBusy(busy) {
      if (!streamSubmit) return;
      streamSubmit.disabled = busy;
      streamSubmit.setAttribute("aria-busy", busy ? "true" : "false");
      if (busy) streamSubmit.textContent = "正在分析…";
      else streamSubmit.textContent = streamSubmit.getAttribute("data-submit-label") || "生成建议";
    }

    function clearStreamTimeout() {
      if (streamTimeout) window.clearTimeout(streamTimeout);
      streamTimeout = null;
    }

    function streamFailure(message) {
      streamTerminal = true;
      clearStreamTimeout();
      setStreamBusy(false);
      textOf(streamStatusTitle, "分析未完成");
      textOf(streamStatusMessage, "请检查连接或稍后重试。");
      textOf(streamErrorMessage, message || "请求失败，可以重试。");
      if (streamError) streamError.hidden = false;
      streamController = null;
    }

    function submitStream() {
      if (streamSubmit && streamSubmit.disabled) return;
      if (!streamForm.checkValidity()) {
        streamForm.reportValidity();
        return;
      }
      streamRaw = "";
      streamReadableText = "";
      streamTerminal = false;
      streamAbortRequested = false;
      streamTimeoutTriggered = false;
      if (streamOutput) streamOutput.hidden = false;
      if (streamError) streamError.hidden = true;
      textOf(streamReadable, "");
      textOf(streamProgress, "正在等待模型响应。");
      textOf(streamStatusTitle, "正在分析");
      textOf(streamStatusMessage, "结果仅供检查，确认前不会保存为正式原则。");
      setStreamBusy(true);
      streamController = window.AbortController ? new AbortController() : null;
      if (streamController) {
        streamTimeout = window.setTimeout(function () {
          streamTimeoutTriggered = true;
          streamController.abort();
        }, 360000);
      }
      var request = {
        method: "POST",
        credentials: "same-origin",
        headers: {"Accept": "application/x-ndjson", "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": csrfFor(streamForm)},
        body: new FormData(streamForm)
      };
      if (streamController) request.signal = streamController.signal;
      fetch(streamForm.getAttribute("action") || window.location.href, request).then(function (response) {
        if (!response.ok || !response.body) throw new Error("服务暂时不可用（HTTP " + response.status + "）。");
        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        var pending = "";
        function read() {
          return reader.read().then(function (result) {
            pending += decoder.decode(result.value || new Uint8Array(), {stream: !result.done});
            var lines = pending.split("\n");
            pending = lines.pop();
            lines.forEach(handleStreamLine);
            if (result.done) {
              if (pending.trim()) handleStreamLine(pending);
              if (!streamTerminal) streamFailure("连接中断，无法确认结果是否已返回。可重试获取已完成的建议，原始资料仍保留。");
              return;
            }
            return read();
          });
        }
        function handleStreamLine(line) {
          if (!line.trim() || streamTerminal) return;
          var event;
          try { event = JSON.parse(line); } catch (ignore) { return; }
          if (event.type === "status") { textOf(streamStatusMessage, event.message || "正在分析。"); }
          else if (event.type === "delta") { renderDelta(event.text || ""); }
          else if (event.type === "complete") {
            streamTerminal = true;
            clearStreamTimeout();
            streamController = null;
            textOf(streamStatusTitle, "分析完成");
            textOf(streamStatusMessage, "正在打开建议页面。");
            if (event.url) {
              var target = new URL(event.url, window.location.origin);
              if (target.origin === window.location.origin) window.location.assign(target.href);
            } else setStreamBusy(false);
          } else if (event.type === "error") streamFailure((event.code ? event.code + "：" : "") + (event.message || "请求失败。"));
        }
        return read();
      }).catch(function (error) {
        if (streamAbortRequested || streamTerminal) return;
        if (streamTimeoutTriggered) streamFailure("等待超过 360 秒，无法确认结果是否已返回。可重试获取结果，原始资料仍保留。");
        else streamFailure(error.message || "请求失败，可以重试。");
      });
    }

    streamForm.addEventListener("submit", function (event) {
      event.preventDefault();
      submitStream();
    });
    if (streamRetry) streamRetry.addEventListener("click", submitStream);
    window.addEventListener("pagehide", function () {
      if (streamController) {
        streamAbortRequested = true;
        streamController.abort();
      }
    });
  }
})();
