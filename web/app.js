/* Divana 网页版的前端逻辑。没有框架，没有构建步骤。 */

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}

/* markdown 渲染在 web/markdown.js 里——它不碰 DOM，所以能用 node 直接测。
   那个文件在 index.html 里先于这个加载，所以 escapeHtml / renderMarkdown
   在这里是全局可用的。 */

/* ---------------------------------------------------------------- 视图切换 */

function showView(name) {
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("hidden", view.id !== `view-${name}`);
  });
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
  if (name === "notes") loadNotes();   // 每次切过来都刷新，免得看到旧的
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});

/* ---------------------------------------------------------------- 聊天 */

let busy = false;

function clearEmpty() {
  const e = document.querySelector(".empty");
  if (e) e.remove();
}

function scrollDown() {
  const box = $("messages");
  box.scrollTop = box.scrollHeight;
}

function addUser(text) {
  clearEmpty();
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="bubble">${escapeHtml(text).replace(/\n/g, "<br>")}</div>`;
  $("messages").appendChild(el);
  scrollDown();
}

function addAssistant() {
  clearEmpty();
  const el = document.createElement("div");
  el.className = "msg assistant";
  // 工具行和正文放**两个**容器。塞在同一个里的话，正文每次刷新（innerHTML =）
  // 都会把工具行一起抹掉——上一版就是这么丢的。
  el.innerHTML =
    `<div class="bubble"><div class="tools"></div>` +
    `<div class="answer"><div class="thinking">思考中…</div></div></div>`;
  $("messages").appendChild(el);
  scrollDown();
  return {
    bubble: el.querySelector(".bubble"),
    tools: el.querySelector(".tools"),
    answer: el.querySelector(".answer"),
  };
}

function addToolLine(tools, name, args) {
  const line = document.createElement("div");
  line.className = "tool";
  const short = args && args.length > 120 ? args.slice(0, 120) + "…" : (args || "");
  line.textContent = `[工具] ${name}(${short})`;
  tools.appendChild(line);
  scrollDown();
}

async function ask(text) {
  addUser(text);
  const { tools, answer: answerBox } = addAssistant();
  let buffer = "", answer = "", started = false;

  const fail = (message) => {
    answerBox.innerHTML = `<div class="error">${escapeHtml(message)}</div>`;
  };

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok || !res.body) {
      const info = await res.json().catch(() => ({}));
      fail(info.error || `请求失败（${res.status}）`);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);

        let name = "message", data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event: ")) name = line.slice(7).trim();
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        let payload = {};
        try { payload = JSON.parse(data || "{}"); } catch (e) { /* 半截帧，忽略 */ }

        if (name === "delta") {
          answer += payload.text || "";
          started = true;
          answerBox.innerHTML = renderMarkdown(answer);
          scrollDown();
        } else if (name === "tool") {
          addToolLine(tools, payload.name, payload.arguments);
        } else if (name === "done") {
          // 以最终文本为准。流式过程中如果因为重试重发了片段（比如搜索接口抽风
          // 重试过一次），累积的 answer 会有重复段落——这里用官方结果覆盖掉。
          if (payload.text) {
            started = true;
            answerBox.innerHTML = renderMarkdown(payload.text);
            scrollDown();
          }
        } else if (name === "error") {
          fail(payload.message || "出错了");
        }
      }
    }
    if (!started) answerBox.innerHTML = '<div class="thinking">（她没有返回内容）</div>';
  } catch (err) {
    fail(`连接断了：${err}`);
  }
}

async function sendCurrent() {
  const input = $("input");
  const text = input.value.trim();
  if (!text || busy) return;

  input.value = "";
  input.style.height = "auto";
  busy = true;
  $("send").disabled = true;
  try {
    await ask(text);
  } finally {
    busy = false;
    $("send").disabled = false;
    input.focus();
  }
}

/* ---------------------------------------------------------------- 侧边栏 */

async function loadState() {
  try {
    const s = await api("/api/state");
    $("p-goal").innerHTML = renderMarkdown(s.goal || "（还没记录）");
    $("p-now").innerHTML = renderMarkdown(s.plan_now || "（还没定）");
    $("p-next").innerHTML = renderMarkdown(s.plan_next || "（还没定）");
    $("n-count").textContent = s.note_count ? `（${s.note_count}）` : "";

    const box = $("notes");
    box.innerHTML = s.notes.length
      ? s.notes.map((n) => `
          <div class="note-item">
            <div class="t">${escapeHtml(n.title)}</div>
            <div class="m">${escapeHtml(n.date || "无日期")}${n.tags.length ? " · " + escapeHtml(n.tags.join("、")) : ""}</div>
          </div>`).join("")
      : '<div class="body">还没有笔记。</div>';
  } catch (err) {
    $("p-goal").innerHTML = `<span class="error">读取失败：${escapeHtml(String(err))}</span>`;
  }
}

async function makeSummary() {
  const btn = $("btn-summary");
  const box = $("summary-out");
  btn.disabled = true;
  box.innerHTML = '<div class="thinking">正在整理这次对话…</div>';
  try {
    const data = await api("/api/summary", { method: "POST" });
    box.innerHTML = renderMarkdown(data.markdown);
    loadState();
  } catch (err) {
    box.innerHTML = `<div class="error">${escapeHtml(err.message || String(err))}</div>`;
  } finally {
    btn.disabled = false;
  }
}

/* ---------------------------------------------------------------- 知识库 */

const notesState = { query: "", tag: "", active: "", notes: [] };

async function loadNotes() {
  const params = new URLSearchParams();
  if (notesState.query) params.set("q", notesState.query);
  if (notesState.tag) params.set("tag", notesState.tag);

  try {
    const data = await api(`/api/notes?${params.toString()}`);
    renderTags(data.tags);
    notesState.notes = data.notes;
    renderNoteList();
  } catch (err) {
    $("note-items").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

function renderTags(tags) {
  const all = [""].concat(tags);
  $("note-tags").innerHTML = all.map((tag) => {
    const label = tag ? escapeHtml(tag) : "全部";
    const active = tag === notesState.tag ? "active" : "";
    return `<button type="button" data-tag="${escapeHtml(tag)}" class="${active}">${label}</button>`;
  }).join("");

  $("note-tags").querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      notesState.tag = btn.dataset.tag;
      loadNotes();
    });
  });
}

function renderNoteList() {
  const box = $("note-items");
  const notes = notesState.notes;
  if (!notes.length) {
    box.innerHTML = '<div class="hint">没有匹配的笔记。</div>';
    return;
  }

  box.innerHTML = notes.map((n) => {
    const active = n.name === notesState.active ? "active" : "";
    const tags = n.tags.map((t) => `<span>${escapeHtml(t)}</span>`).join("");
    return `
      <div class="note-card ${active}" data-name="${escapeHtml(n.name)}">
        <div class="t">${escapeHtml(n.title)}</div>
        ${n.snippet ? `<div class="s">${escapeHtml(n.snippet)}</div>` : ""}
        <div class="m">${escapeHtml(n.date || "无日期")}${tags ? " · " + tags : ""}</div>
      </div>`;
  }).join("");

  box.querySelectorAll(".note-card").forEach((card) => {
    card.addEventListener("click", () => openNote(card.dataset.name));
  });
}

async function openNote(name) {
  notesState.active = name;
  renderNoteList();

  const box = $("note-detail");
  try {
    const note = await api(`/api/note?name=${encodeURIComponent(name)}`);
    // 文件正文的第一行就是标题（落盘时写的），这里已经用 <h1> 显示了，去掉免得重复
    const body = note.body.replace(/^#\s+.*\r?\n?/, "");
    const tags = note.tags.map(escapeHtml).join("、");
    box.innerHTML = `
      <h1>${escapeHtml(note.title)}</h1>
      <div class="meta">${escapeHtml(note.date || "无日期")}${tags ? " · " + tags : ""} · ${escapeHtml(note.name)}</div>
      ${renderMarkdown(body)}`;
  } catch (err) {
    box.innerHTML = `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

/* ---------------------------------------------------------------- 绑定 */

const input = $("input");

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendCurrent();
  }
});

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  sendCurrent();
});

$("quick").querySelectorAll("button").forEach((btn) => {
  btn.addEventListener("click", () => {
    input.value = btn.dataset.prompt;
    sendCurrent();
  });
});

$("btn-summary").addEventListener("click", makeSummary);

$("note-search").addEventListener("input", (e) => {
  notesState.query = e.target.value.trim();
  loadNotes();
});

/* ---------------------------------------------------------------- 启动 */

loadState();
loadNotes();
input.focus();
