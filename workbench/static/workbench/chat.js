(function () {
  'use strict';
  var surface = document.querySelector('[data-chat-surface]');
  if (!surface) return;
  var controller = null, unsaved = false, followTail = true;
  window.addEventListener('scroll', function () {
    followTail = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 150;
  }, {passive: true});

  function markConversation() {
    document.querySelectorAll('.side-nav .nav-link').forEach(function (link) {
      link.classList.toggle('is-active', link.getAttribute('href') === '/cases/');
    });
  }

  function readable(raw) {
    try {
      var value = JSON.parse(raw);
      return [value.answer].concat((value.options || []).map(function (option) {
        return '可选做法：' + option.action + '\n代价与考虑：' + option.tradeoff;
      }), value.question ? ['一个关键问题：' + value.question] : []).filter(Boolean).join('\n\n');
    } catch (ignore) {
      var match = /"answer"\s*:\s*"((?:\\.|[^"\\])*)/.exec(raw);
      if (!match) return '';
      try { return JSON.parse('"' + match[1] + '"'); } catch (partial) { return match[1].replace(/\\n/g, '\n').replace(/\\$/, ''); }
    }
  }

  function bubble(role, text) {
    var article = document.createElement('article');
    article.className = 'chat-message chat-message-' + role;
    var author = document.createElement('div'); author.className = 'chat-author'; author.textContent = role === 'user' ? '你' : 'AI';
    var copy = document.createElement('div'); copy.className = 'chat-copy'; copy.textContent = text;
    article.append(author, copy);
    surface.querySelector('[data-chat-messages]').append(article);
    return article;
  }

  function bind() {
    var form = surface.querySelector('[data-chat-form]');
    var input = form.querySelector('textarea');
    var send = form.querySelector('[data-chat-send]');
    var progress = form.querySelector('[data-chat-progress]');
    var errorBox = form.querySelector('[data-chat-error]');
    var savedLink = form.querySelector('[data-chat-saved-link]');
    var pending = null, busy = false, saved = null, liveReply = null;

    function setBusy(value) {
      busy = value;
      form.querySelectorAll('button').forEach(function (button) { button.disabled = value; });
      input.readOnly = value;
      form.setAttribute('aria-busy', value ? 'true' : 'false');
      send.textContent = value ? '回复中…' : pending ? '重试发送' : '发送';
    }

    input.addEventListener('input', function () {
      unsaved = true;
      if (pending) {
        pending = null;
        form.elements.operation_key.value = crypto.randomUUID();
        send.textContent = '发送';
      }
    });
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
        event.preventDefault();
        if (!busy) form.requestSubmit(send);
      }
    });

    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      var mode = event.submitter ? event.submitter.value : 'send';
      followTail = true;
      if (!pending) pending = {url: form.getAttribute('action'), data: new URLSearchParams(new FormData(form)), shown: false};
      pending.data.set('action', mode);
      errorBox.hidden = true; progress.hidden = false;
      progress.textContent = mode === 'save' ? '正在保存…' : '正在连接模型…';
      if (liveReply) liveReply.remove();
      liveReply = null;
      setBusy(true);
      if (mode === 'save') send.textContent = '保存中…';
      var terminal = false, raw = '', timedOut = false;
      controller = new AbortController();
      var timeout = window.setTimeout(function () { timedOut = true; if (controller) controller.abort(); }, 360000);

      function receive(line) {
        if (!line.trim() || terminal) return;
        var item = JSON.parse(line);
        if (item.type === 'saved') {
          saved = item; unsaved = false;
          form.setAttribute('action', item.send_url); form.elements.revision.value = item.revision;
          savedLink.href = item.url; savedLink.hidden = false;
          window.history.replaceState({}, '', item.url);
          markConversation();
          document.title = '对话 · 个人原则工作台';
          document.querySelectorAll('[data-home-extra]').forEach(function (el) { el.hidden = true; });
          surface.querySelector('.chat-heading').classList.remove('chat-heading-new');
          surface.querySelector('.chat-heading h1').textContent = '聊聊这件事';
          var intro = surface.querySelector('.chat-heading p'); if (intro) intro.hidden = true;
          if (!pending.shown && pending.data.get('content').trim()) bubble('user', pending.data.get('content'));
          pending.shown = true;
          progress.textContent = mode === 'save' ? '已保存。' : '原话已保存，正在思考…';
        } else if (item.type === 'status') {
          progress.textContent = '正在思考…';
        } else if (item.type === 'delta') {
          raw += item.text || '';
          var text = readable(raw);
          if (text) {
            if (!liveReply) liveReply = bubble('assistant', text);
            else liveReply.querySelector('.chat-copy').textContent = text;
            progress.textContent = '正在回复…';
            if (followTail) liveReply.scrollIntoView({block: 'end'});
          }
        } else if (item.type === 'complete') {
          terminal = true; unsaved = false; pending = null;
          // HTML is rendered by our own Django templates; model text is escaped there.
          surface.innerHTML = item.html;
          window.history.replaceState({}, '', item.url);
          markConversation();
          document.querySelectorAll('[data-home-extra]').forEach(function (el) { el.hidden = true; });
          bind();
          var next = surface.querySelector('textarea'); next.focus({preventScroll: true});
          var notice = surface.querySelector('[data-chat-progress]'); notice.hidden = false;
          notice.textContent = mode === 'save' ? '已保存。需要时点击“继续回复”。' : '回复已保存，可继续聊。';
          if (followTail) next.scrollIntoView({block: 'center'});
        } else if (item.type === 'error') {
          var error = new Error(item.message || '回复未完成，请重试。'); error.code = item.code; throw error;
        }
      }

      try {
        var response = await fetch(pending.url, {method: 'POST', body: pending.data, credentials: 'same-origin',
          headers: {'Accept': 'application/x-ndjson', 'X-CSRFToken': form.elements.csrfmiddlewaretoken.value}, signal: controller.signal});
        if (!response.ok || !response.body) throw new Error('连接失败，请稍后重试（HTTP ' + response.status + '）。');
        var reader = response.body.getReader(), decoder = new TextDecoder(), buffer = '';
        while (true) {
          var chunk = await reader.read();
          buffer += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
          var lines = buffer.split('\n'); buffer = lines.pop();
          lines.forEach(receive);
          if (chunk.done) { if (buffer.trim()) receive(buffer); break; }
        }
        if (!terminal) throw new Error('连接中断。点击重试可取回已完成的回复。');
      } catch (error) {
        if (!terminal) {
          if (liveReply) { liveReply.remove(); liveReply = null; }
          errorBox.hidden = false; progress.hidden = true;
          form.querySelector('[data-chat-error-text]').textContent = (saved ? '你的原话已保存。' : '') +
            (timedOut ? '等待超时，请重试获取结果。' : error.message);
          setBusy(false);
        }
      } finally { window.clearTimeout(timeout); controller = null; }
    });
  }

  window.addEventListener('beforeunload', function (event) { if (unsaved) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pagehide', function () { if (controller) controller.abort(); });
  bind();
})();
