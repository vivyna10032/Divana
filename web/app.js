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
  if (name === "plan") loadPlan();
  if (name === "profile") loadProfile();
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});

/* ---------------------------------------------------------------- 聊天 */

let busy = false;
let currentSession = "";

function clearEmpty() {
  const e = document.querySelector(".empty");
  if (e) e.remove();
}

function scrollDown() {
  const box = $("messages");
  box.scrollTop = box.scrollHeight;
}

function addStaticMessage(role, text) {
  clearEmpty();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  const html =
    role === "user"
      ? escapeHtml(text).replace(/\n/g, "<br>")
      : renderMarkdown(text);
  el.innerHTML = `<div class="bubble">${html}</div>`;
  $("messages").appendChild(el);
  scrollDown();
}

function addUser(text) {
  addStaticMessage("user", text);
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
    loadSessions();   // 刷新时间戳和排序
  }
}

/* ---------------------------------------------------------------- 会话 */

async function loadSessions() {
  try {
    const data = await api("/api/sessions");
    currentSession = data.current;
    const box = $("session-list");

    if (!data.sessions.length) {
      box.innerHTML = '<div class="hint">还没有对话</div>';
      return;
    }
    box.innerHTML = data.sessions.map((s) => `
      <div class="session-item ${s.id === data.current ? "active" : ""}"
           data-id="${escapeHtml(s.id)}">
        <div class="t">${escapeHtml(s.title)}</div>
        <div class="m">${escapeHtml(s.updated_at)} · ${s.message_count} 条</div>
      </div>`).join("");

    box.querySelectorAll(".session-item").forEach((el) => {
      el.addEventListener("click", () => switchSession(el.dataset.id));
    });
  } catch (err) {
    $("session-list").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

async function loadHistory() {
  const box = $("messages");
  try {
    const data = await api("/api/history");
    box.innerHTML = "";
    if (!data.messages.length) {
      box.innerHTML =
        '<div class="empty"><div class="big">你好，我是 Divana</div>' +
        "<div>我记得你的学习目标，也知道你正在学什么。</div></div>";
      return;
    }
    for (const message of data.messages) {
      addStaticMessage(message.role, message.text);
    }
  } catch (err) {
    box.innerHTML = `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

async function switchSession(id) {
  if (!id || id === currentSession || busy) return;
  try {
    await api("/api/sessions/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: id }),
    });
    currentSession = id;
    await loadHistory();
    loadSessions();
  } catch (err) {
    $("messages").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

async function newSession() {
  if (busy) return;
  try {
    const data = await api("/api/sessions/new", { method: "POST" });
    currentSession = data.id;
    await loadHistory();
    loadSessions();
    input.focus();
  } catch (err) {
    $("messages").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
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

/* ---------------------------------------------------------------- 学习画像 */

async function loadProfile() {
  try {
    const p = await api("/api/profile");
    $("pf-goal").innerHTML = renderMarkdown(p.goal || "（还没记录）");
    $("pf-level").innerHTML = renderMarkdown(p.level || "（还没记录）");
    $("pf-known").innerHTML = renderMarkdown(p.known || "（还没记录）");
    $("pf-weak").innerHTML = renderMarkdown(p.weak || "（还没记录）");
    $("pf-habits").innerHTML = renderMarkdown(p.habits || "（还没记录）");

    $("pf-bar").style.width = p.percent + "%";
    $("pf-pct").textContent = p.total
      ? `已完成 ${p.done} / ${p.total} 个里程碑（${p.percent}%）`
      : "路线图里还没有可勾的里程碑";

    $("pf-meta").innerHTML = `
      <p>文件：<code>${escapeHtml(p.path)}</code></p>
      <p>最后更新：${escapeHtml(p.updated_at || "未知")}</p>
      <p>手上的笔记：${p.note_count} 篇</p>
      <p class="muted">她在聊天里了解到关于你的信息时，会自己更新这份画像。
      你也可以直接编辑那个文件。</p>
      <p class="muted">注意：画像是在服务启动时读进去的，你手改完要<b>重启服务</b>她才会读到。</p>`;
  } catch (err) {
    $("pf-goal").innerHTML =
      `<span class="error">读取失败：${escapeHtml(err.message || String(err))}</span>`;
  }
}

/* ---------------------------------------------------------------- 学习计划 */

async function loadPlan() {
  try {
    const plan = await api("/api/plan");
    $("plan-now").innerHTML = renderMarkdown(plan.now || "（还没定）");
    $("plan-next").innerHTML = renderMarkdown(plan.next || "（还没定）");
    $("plan-retro").innerHTML = renderMarkdown(plan.retro || "（还没有复盘）");

    $("plan-bar").style.width = plan.percent + "%";
    $("plan-pct").textContent = plan.total
      ? `已完成 ${plan.done} / ${plan.total}（${plan.percent}%）`
      : "路线图里还没有可勾的里程碑";

    renderStages(plan.stages);
  } catch (err) {
    $("plan-stages").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

async function makeReview() {
  const btn = $("btn-review");
  btn.disabled = true;
  $("plan-retro").innerHTML =
    '<div class="thinking">正在回看最近一周…（要花一次模型调用）</div>';
  try {
    const data = await api("/api/review", { method: "POST" });
    $("plan-retro").innerHTML = renderMarkdown(data.markdown);
  } catch (err) {
    $("plan-retro").innerHTML =
      `<div class="error">${escapeHtml(err.message || String(err))}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderStages(stages) {
  const box = $("plan-stages");
  if (!stages.length) {
    box.innerHTML =
      '<div class="hint">路线图还是空的。跟她说说你想学什么，她会和你一起把阶段拆出来。</div>';
    return;
  }

  // "当前阶段" = 第一个还有没做完的里程碑的阶段
  const current = stages.findIndex((s) => s.milestones.some((m) => !m.done));

  box.innerHTML = stages.map((stage, i) => {
    const badge = i === current ? '<span class="badge">当前</span>' : "";
    const items = stage.milestones.map((m) => `
      <div class="ms ${m.done ? "done" : ""}">
        <div class="mark">${m.done ? "✓" : ""}</div>
        <div class="label">${inline(escapeHtml(m.text))}</div>
      </div>`).join("");
    return `<div class="stage ${i === current ? "current" : ""}">
        <h3>${escapeHtml(stage.name)}${badge}</h3>${items}
      </div>`;
  }).join("");
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
$("btn-new-session").addEventListener("click", newSession);
$("btn-review").addEventListener("click", makeReview);

$("note-search").addEventListener("input", (e) => {
  notesState.query = e.target.value.trim();
  loadNotes();
});

/* ---------------------------------------------------------------- 启动 */

loadState();
loadSessions();
loadHistory();
loadNotes();
loadPlan();
loadProfile();
input.focus();
