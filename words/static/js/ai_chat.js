// ===== AI 智能助手独立页（多会话 + 文件上传 + 模型选择） =====
window.AIChat = (function () {
  var sending = false;
  var pendingFiles = []; // {name, mime, content(base64 或 text), type}
  var currentConvId = null; // 当前会话 ID（null = 尚未有会话）
  var conversations = []; // 会话列表缓存

  function el(id) { return document.getElementById(id); }

  function autoResizeInput(textarea) {
    if (!textarea) return;
    textarea.style.height = 'auto';
    var maxH = window.matchMedia('(max-width: 768px)').matches ? 120 : 150;
    var newH = Math.min(Math.max(textarea.scrollHeight, 38), maxH);
    textarea.style.height = newH + 'px';
  }

  function toggleDrawer(force) {
    var sidebar = el('aiChatSidebar');
    var mask = el('aiChatDrawerMask');
    if (!sidebar) return;
    var isOpen = typeof force === 'boolean' ? force : !sidebar.classList.contains('open');
    if (isOpen) {
      sidebar.classList.add('open');
      if (mask) mask.classList.add('open');
    } else {
      sidebar.classList.remove('open');
      if (mask) mask.classList.remove('open');
    }
  }

  function init() {
    loadData();
    var input = el('aiChatInput');
    if (input) {
      if (!window.matchMedia('(max-width: 768px)').matches) {
        input.placeholder = '输入消息…（Enter 发送，Shift+Enter 换行）';
      }
      input.addEventListener('input', function () {
        autoResizeInput(this);
      });
      input.addEventListener('focus', function () {
        if (window.matchMedia('(max-width: 768px)').matches) {
          setTimeout(scrollBottom, 250);
        }
      });
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          if (e.isComposing || e.keyCode === 229) return;
          var isTouchMobile = window.matchMedia('(max-width: 768px)').matches && ('ontouchstart' in window || navigator.maxTouchPoints > 0);
          if (isTouchMobile) return;
          e.preventDefault();
          sendFromInput();
        }
      });
    }
    var fileInput = el('aiChatFile');
    if (fileInput) {
      fileInput.addEventListener('change', function () { handleFiles(this.files); this.value = ''; });
    }
  }

  // ===== 数据加载 =====
  function loadData(convId) {
    var url = '/api/ai/chat/';
    if (convId) url += '?conversation_id=' + convId;
    setBody('<div class="ai-chat-empty"><span class="big"><i class="ph ph-sparkle"></i></span>加载对话记录…</div>');
    VOCAB_API.get(url).then(function (res) {
      if (!res.success) {
        setBody('<div class="ai-chat-empty">加载对话记录失败</div>');
        return;
      }
      conversations = res.conversations || [];
      currentConvId = res.current_conversation_id;
      renderConvList();
      var msgs = res.messages || [];
      if (!msgs.length) {
        showEmpty();
      } else {
        renderMessages(msgs);
      }
    }).catch(function () {
      setBody('<div class="ai-chat-empty">加载对话记录失败</div>');
    });
  }

  function fillPrompt(text) {
    var input = el('aiChatInput');
    if (!input) return;
    input.value = text;
    autoResizeInput(input);
    input.focus();
  }

  function showEmpty() {
    setBody('<div class="ai-chat-welcome">' +
      '<div class="ai-welcome-badge"><i class="ph ph-sparkle"></i></div>' +
      '<h2 class="ai-welcome-title">驻留集 AI 智能助手</h2>' +
      '<p class="ai-welcome-desc">精通考研英语真题考点、长难句结构拆解与词汇辨析。你可以直接提问，或点击下方建议快捷开始：</p>' +
      '<div class="ai-quick-prompts">' +
        '<button type="button" class="ai-quick-prompt" onclick="AIChat.fillPrompt(\'请帮我详细分析这句话的语法结构、主干与翻译：The American economic system is organized around a basically private-enterprise, market-oriented economy.\')">' +
          '<i class="ph ph-tree-structure"></i>' +
          '<span>长难句结构拆解与翻译</span>' +
        '</button>' +
        '<button type="button" class="ai-quick-prompt" onclick="AIChat.fillPrompt(\'请深度辨析考研高频词：comprise / consist / compose / constitute 的核心差异与常见真题考法\')">' +
          '<i class="ph ph-book-open-text"></i>' +
          '<span>真题高频近义词辨析</span>' +
        '</button>' +
        '<button type="button" class="ai-quick-prompt" onclick="AIChat.fillPrompt(\'请帮我批改这句英语作文并提供考研高分地道替换：I think the rapid development of science and technology has changed our life greatly.\')">' +
          '<i class="ph ph-pencil-line"></i>' +
          '<span>写作表达润色与升格</span>' +
        '</button>' +
        '<button type="button" class="ai-quick-prompt" onclick="AIChat.fillPrompt(\'请结合考研英语常考点，出 3 道核心高频词选择测验题，包含语境题干并附带详细解析\')">' +
          '<i class="ph ph-lightning"></i>' +
          '<span>核心考点随堂测验</span>' +
        '</button>' +
      '</div>' +
    '</div>');
  }

  // ===== 会话列表 =====
  function renderConvList() {
    var list = el('convList');
    if (!list) return;
    if (!conversations.length) {
      list.innerHTML = '<div class="text-secondary text-sm" style="padding: 8px 10px;">暂无会话，点击上方新建</div>';
      return;
    }
    var badge = el('convCountBadge');
    if (badge) {
      badge.textContent = conversations.length ? String(conversations.length) : '';
      badge.style.display = conversations.length ? 'inline-block' : 'none';
    }
    list.innerHTML = conversations.map(function (c) {
      var active = currentConvId === c.id ? ' active' : '';
      return '<div class="conv-item' + active + '" onclick="AIChat.switchConversation(' + c.id + ')">' +
        '<i class="ph ph-chat-circle" style="opacity: .7;"></i>' +
        '<span class="title" title="' + escapeHtml(c.title) + '">' + escapeHtml(c.title) + '</span>' +
        '<span class="text-secondary" style="font-size: 10px; opacity: .7;">' + c.message_count + '</span>' +
        '<button class="del" onclick="event.stopPropagation(); AIChat.deleteConversation(' + c.id + ')" title="删除会话">&times;</button>' +
        '</div>';
    }).join('');
  }

  function updateConvInList(conv) {
    var found = false;
    conversations = conversations.map(function (c) {
      if (c.id === conv.id) { found = true; return conv; }
      return c;
    });
    if (!found) conversations.unshift(conv);
    renderConvList();
  }

  // ===== 会话操作 =====
  function newConversation() {
    VOCAB_API.post('/api/ai/chat/new/', {}).then(function (res) {
      if (res.success) {
        currentConvId = res.conversation.id;
        updateConvInList(res.conversation);
        toggleDrawer(false);
        showEmpty();
        var input = el('aiChatInput');
        if (input) input.focus();
        showToast('已新建对话', 'success');
      } else {
        showToast(res.error || '新建失败', 'error');
      }
    }).catch(function () { showToast('请求失败', 'error'); });
  }

  function switchConversation(id) {
    toggleDrawer(false);
    if (id === currentConvId) return;
    loadData(id);
  }

  async function deleteConversation(id) {
    var conv = null;
    for (var i = 0; i < conversations.length; i++) {
      if (conversations[i].id === id) { conv = conversations[i]; break; }
    }
    var title = conv ? conv.title : '该会话';
    if (!await appConfirm('确定要删除会话「' + title + '」及其全部消息记录吗？')) return;
    VOCAB_API.del('/api/ai/chat/conversation/' + id + '/').then(function (res) {
      if (res.success) {
        loadData();
        showToast('会话已删除', 'success');
      } else {
        showToast(res.error || '删除失败', 'error');
      }
    }).catch(function () { showToast('请求失败', 'error'); });
  }

  async function clearHistory() {
    if (!currentConvId) { showToast('当前没有会话', 'error'); return; }
    if (!await appConfirm('确定要清空当前会话的全部聊天记录吗？')) return;
    VOCAB_API.del('/api/ai/chat/?conversation_id=' + currentConvId).then(function (res) {
      if (res.success) {
        showEmpty();
        conversations = conversations.map(function (c) {
          if (c.id === currentConvId) c.message_count = 0;
          return c;
        });
        renderConvList();
        showToast('聊天记录已清空', 'success');
      } else {
        showToast(res.error || '清空失败', 'error');
      }
    }).catch(function () { showToast('请求失败', 'error'); });
  }

  // ===== 消息渲染 =====
  function renderMessages(msgs) {
    var html = '';
    msgs.forEach(function (m) { html += bubbleHtml(m.role, m.content, m.created_at, m.attachments || []); });
    setBody(html);
    scrollBottom();
  }

  function bubbleHtml(role, content, time, attachments) {
    var cls = role === 'user' ? 'user' : 'ai';
    var attachHtml = '';
    if (attachments && attachments.length) {
      attachHtml = '<div class="ai-attach-tags">' + attachments.map(function (a) {
        var icon = a.type === 'image' ? 'ph-image' : 'ph-file-text';
        return '<span class="ai-attach-tag"><i class="ph ' + icon + '"></i>' + escapeHtml(a.name || '') + '</span>';
      }).join('') + '</div>';
    }
    var body = role === 'user'
      ? escapeHtml(content).replace(/\n/g, '<br>')
      : renderMarkdown(content);
    return '<div class="ai-msg md-compact ' + cls + '">' + attachHtml + body +
      (time ? '<span class="ai-msg-meta">' + escapeHtml(time) + '</span>' : '') + '</div>';
  }

  function appendMsg(role, content, time, attachments) {
    var body = el('aiChatBody');
    if (!body) return;
    var empty = body.querySelector('.ai-chat-empty');
    if (empty) empty.remove();
    body.insertAdjacentHTML('beforeend', bubbleHtml(role, content, time, attachments || []));
    scrollBottom();
  }

  // ===== 文件处理 =====
  function handleFiles(fileList) {
    var room = Math.max(0, 8 - pendingFiles.length);
    var files = Array.prototype.slice.call(fileList);
    if (files.length > room) {
      showToast('每次最多保留 8 个附件', 'error');
      files = files.slice(0, room);
    }
    var existingBytes = pendingFiles.reduce(function (sum, item) {
      return sum + (item.size || 0);
    }, 0);
    files = files.filter(function (file) {
      if (file.size > 10 * 1024 * 1024) {
        showToast('单个附件不能超过 10 MB：' + file.name, 'error');
        return false;
      }
      if (existingBytes + file.size > 30 * 1024 * 1024) {
        showToast('附件总大小不能超过 30 MB：' + file.name, 'error');
        return false;
      }
      existingBytes += file.size;
      return true;
    });
    var tasks = files.map(function (file) {
      return readFile(file).then(function (item) {
        pendingFiles.push(item);
        renderPending();
      }).catch(function () {
        showToast('读取文件失败：' + file.name, 'error');
      });
    });
    Promise.all(tasks).then(function () {
      var input = el('aiChatInput');
      if (input) input.focus();
    });
  }

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var mime = file.type || '';
      var isImage = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'].indexOf(mime.toLowerCase()) !== -1;
      var reader = new FileReader();
      reader.onerror = function () { reject(new Error('read error')); };
      if (isImage) {
        reader.onload = function () {
          // FileReader 的 dataURL 自带 data:image/xxx;base64, 前缀，剥掉只留 base64 部分
          var base64 = String(reader.result).split(',')[1] || '';
          resolve({ name: file.name, mime: mime, content: base64, type: 'image', size: file.size });
        };
        reader.readAsDataURL(file);
      } else {
        reader.onload = function () {
          resolve({ name: file.name, mime: mime || 'text/plain', content: String(reader.result), type: 'text', size: file.size });
        };
        reader.readAsText(file, 'utf-8');
      }
    });
  }

  function renderPending() {
    var box = el('aiAttachPending');
    if (!box) return;
    if (!pendingFiles.length) {
      box.innerHTML = '';
      return;
    }
    box.innerHTML = pendingFiles.map(function (f, i) {
      var icon = f.type === 'image' ? 'ph-image' : 'ph-file-text';
      return '<span class="ai-attach-chip"><i class="ph ' + icon + '" style="color: var(--c-green);"></i>' +
        '<span class="name">' + escapeHtml(f.name) + '</span>' +
        '<button class="rm" onclick="AIChat.removePending(' + i + ')" title="移除">&times;</button></span>';
    }).join('');
  }

  function removePending(idx) {
    pendingFiles.splice(idx, 1);
    renderPending();
  }

  // ===== 发送 =====
  function sendFromInput() {
    var input = el('aiChatInput');
    var text = input ? input.value.trim() : '';
    if (!text && !pendingFiles.length) return;
    input.value = '';
    autoResizeInput(input);
    send(text);
  }

  function send(text) {
    if (sending) return;
    sending = true;
    setSending(true);

    var files = pendingFiles.slice();
    pendingFiles = [];
    renderPending();

    appendMsg('user', text || '（已发送 ' + files.length + ' 个文件）', null, files.map(function (f) {
      return { name: f.name, type: f.type };
    }));

    var modelSelect = el('aiChatModel');
    var payload = {
      message: text,
      model_id: modelSelect ? modelSelect.value : null,
      conversation_id: currentConvId,
      files: files,
    };

    VOCAB_API.post('/api/ai/chat/', payload).then(function (res) {
      if (res.success) {
        currentConvId = res.conversation.id;
        updateConvInList(res.conversation);
        toggleDrawer(false);
        appendMsg('assistant', res.assistant_message.content, res.assistant_message.created_at);
      } else {
        appendMsg('assistant', '⚠️ ' + (res.error || '出错了'));
        if (res.error && /模型/.test(res.error)) {
          showToast('请到设置页配置并启用 AI 模型', 'error');
        }
      }
    }).catch(function () {
      appendMsg('assistant', '⚠️ 网络错误，请稍后重试');
    }).finally(function () {
      sending = false;
      setSending(false);
      var input = el('aiChatInput');
      if (input) input.focus();
    });
  }

  function setSending(on) {
    var btn = el('aiChatSendBtn');
    if (!btn) return;
    btn.disabled = on;
    btn.innerHTML = on
      ? '<span class="spinning"><i class="ph ph-circle-notch"></i></span>'
      : '<i class="ph ph-paper-plane-tilt"></i>';
  }

  // ===== 工具函数 =====
  function setBody(html) {
    var body = el('aiChatBody');
    if (body) body.innerHTML = html;
  }

  function scrollBottom() {
    var body = el('aiChatBody');
    if (body) body.scrollTop = body.scrollHeight;
  }

  function escapeHtml(s, keepGt) {
    var t = String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/"/g, '&quot;');
    if (!keepGt) t = t.replace(/>/g, '&gt;');
    return t;
  }

  // 轻量 Markdown 渲染（同小助手，先转义再排版）
  function renderMarkdown(src) {
    if (!src) return '';
    var lines = escapeHtml(src, true).split('\n');
    var html = '';
    var i = 0;
    var para = [];

    function flushPara() {
      if (para.length) { html += '<p>' + para.map(inline).join('<br>') + '</p>'; para = []; }
    }
    function inline(s) {
      s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, t, u) {
        return /^(https?:\/\/)/.test(u) ? '<a href="' + u + '" target="_blank" rel="noopener">' + t + '</a>' : m;
      });
      s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
      s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
      return s;
    }

    while (i < lines.length) {
      var line = lines[i];
      if (/^\s*```/.test(line)) {
        flushPara();
        var buf = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
        html += '<pre><code>' + buf.join('\n') + '</code></pre>';
        i++;
        continue;
      }
      var h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        flushPara();
        html += '<h' + h[1].length + '>' + inline(h[2]) + '</h' + h[1].length + '>';
        i++;
        continue;
      }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
        flushPara();
        html += '<hr>';
        i++;
        continue;
      }
      if (/^\s*>\s?/.test(line)) {
        flushPara();
        var q = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) { q.push(inline(lines[i].replace(/^\s*>\s?/, ''))); i++; }
        html += '<blockquote>' + q.join('<br>') + '</blockquote>';
        continue;
      }
      if (/^\s*[-*]\s+/.test(line)) {
        flushPara();
        var ul = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          var li = inline(lines[i].replace(/^\s*[-*]\s+/, ''));
          i++;
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i])) {
            li += '<br>' + inline(lines[i].trim());
            i++;
          }
          ul.push('<li>' + li + '</li>');
        }
        html += '<ul>' + ul.join('') + '</ul>';
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        flushPara();
        var ol = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          var oli = inline(lines[i].replace(/^\s*\d+\.\s+/, ''));
          i++;
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i])) {
            oli += '<br>' + inline(lines[i].trim());
            i++;
          }
          ol.push('<li>' + oli + '</li>');
        }
        html += '<ol>' + ol.join('') + '</ol>';
        continue;
      }
      if (line.indexOf('|') >= 0 && lines[i + 1] && /^\s*\|?[\s:|-]+\|[\s:|-]*\s*$/.test(lines[i + 1])) {
        flushPara();
        var rows = [];
        while (i < lines.length && lines[i] && lines[i].indexOf('|') >= 0) {
          var row = lines[i].trim().replace(/^\||\|$/g, '');
          if (!/^[\s:|-]+$/.test(row.replace(/\|/g, ''))) {
            rows.push(row.split('|').map(function (c) { return c.trim(); }));
          }
          i++;
        }
        if (rows.length) {
          var tbl = '<table><thead><tr>';
          rows[0].forEach(function (h2) { tbl += '<th>' + inline(h2) + '</th>'; });
          tbl += '</tr></thead><tbody>';
          for (var r = 1; r < rows.length; r++) {
            tbl += '<tr>';
            rows[r].forEach(function (c) { tbl += '<td>' + inline(c) + '</td>'; });
            tbl += '</tr>';
          }
          tbl += '</tbody></table>';
          html += tbl;
        }
        continue;
      }
      if (/^\s*$/.test(line)) { flushPara(); i++; continue; }
      para.push(line);
      i++;
    }
    flushPara();
    return html;
  }

  init();
  return {
    sendFromInput: sendFromInput,
    clearHistory: clearHistory,
    removePending: removePending,
    newConversation: newConversation,
    switchConversation: switchConversation,
    deleteConversation: deleteConversation,
    toggleDrawer: toggleDrawer,
    fillPrompt: fillPrompt,
  };
})();
