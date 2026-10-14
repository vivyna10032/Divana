/* Divana 网页版的前端逻辑。没有框架，没有构建步骤。 */

const $ = (id) => document.getElementById(id);

/* 前端出错必须让人看见——这一条是被坑出来的。

   练习页曾经"点了没反应"：服务端对、HTML 对、接口返回也对，就因为脚本里一处
   拼错的名字，浏览器在 console 里小声抱怨一句，整段脚本就停在那儿了。而没人会
   去开 console，于是现象只剩"没反应"三个字，查起来全靠猜。

   所以这里接管全局错误：任何脚本报错都在页面顶上顶出一条红带，截图就能查。
   能自己报错的页面，才配叫"能调试的页面"。
*/
function reportPageError(message) {
  let bar = document.getElementById("page-error");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "page-error";
    document.body.insertBefore(bar, document.body.firstChild);
  }
  bar.textContent = `前端出错：${message}　（把这一条截图发给 Codex 就能查）`;
}

window.addEventListener("error", (event) => {
  const where = `${String(event.filename || "").split("/").pop()}:${event.lineno}`;
  reportPageError(`${event.message} @ ${where}`);
});
window.addEventListener("unhandledrejection", (event) => {
  reportPageError(String((event.reason && event.reason.message) || event.reason));
});

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}

async function deleteItem(kind, id, label) {
  const ok = confirm(
    `删掉「${label}」？\n\n` +
    "它会先移到回收站（data/trash/），不会真的消失——确认不要了，你自己再删那个文件。"
  );
  if (!ok) return false;

  try {
    await api("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, id }),
    });
    return true;
  } catch (err) {
    alert(`删除失败：${err.message || err}`);
    return false;
  }
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
  if (name === "digest") loadDigests();
  if (name === "practice") loadPractice();
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

/* 她在对话里出完题 / 批改完，题目其实已经落到 data/practice.json 了，
   但用户不会知道要去哪看。所以对话里直接给一条能点的路——
   比在 prompt 里教她"记得提醒用户去练习页"可靠：
   **能由程序确定做到的事，别交给模型去猜。**
*/
function addPracticeJump(box, label) {
  const row = document.createElement("div");
  row.className = "jump";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "jump-btn";
  button.textContent = `${label} → 去练习页`;
  button.addEventListener("click", () => showView("practice"));
  row.appendChild(button);
  box.appendChild(row);
  scrollDown();
}

async function ask(text) {
  addUser(text);
  const { tools, answer: answerBox } = addAssistant();
  let buffer = "", answer = "", started = false;
  let madeQuiz = false, gradedQuiz = false;

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
          if (payload.name === "make_quiz") madeQuiz = true;
          if (payload.name === "grade_quiz") gradedQuiz = true;
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
    // 放在最后：上面那句"她没有返回内容"会把整个气泡重写掉，先加就会被冲掉
    if (madeQuiz) addPracticeJump(answerBox, "题目出好了，在练习页");
    if (gradedQuiz) addPracticeJump(answerBox, "批改好了，在练习页");
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
    loadPractice();   // 她刚在对话里出了题/批了改的话，练习页现在就是最新的
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
        <button class="del" type="button" title="删掉这段对话">×</button>
      </div>`).join("");

    box.querySelectorAll(".session-item").forEach((el) => {
      el.addEventListener("click", () => switchSession(el.dataset.id));
    });
    box.querySelectorAll(".session-item .del").forEach((btn) => {
      btn.addEventListener("click", async (event) => {
        event.stopPropagation();   // 别顺手把会话切过去
        const item = btn.parentElement;
        const title = item.querySelector(".t").textContent;
        if (await deleteItem("session", item.dataset.id, title)) {
          await loadSessions();
          await loadHistory();   // 删的可能正是当前这段
        }
      });
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

/* ---------------------------------------------------------------- AI 热点 */

const digestState = { active: "" };

async function loadDigests() {
  try {
    const data = await api("/api/digests");
    renderDigestStatus(data.status);

    const box = $("digest-items");
    if (!data.digests.length) {
      box.innerHTML = '<div class="hint">还没有早报。点上面的按钮抓一期。</div>';
      return;
    }
    box.innerHTML = data.digests.map((d) => `
      <div class="note-card ${d.name === digestState.active ? "active" : ""}"
           data-name="${escapeHtml(d.name)}">
        <div class="t">${escapeHtml(d.date)}</div>
        <div class="s">${escapeHtml(d.title)}</div>
      </div>`).join("");

    box.querySelectorAll(".note-card").forEach((el) => {
      el.addEventListener("click", () => openDigest(el.dataset.name));
    });
  } catch (err) {
    $("digest-items").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

function renderDigestStatus(status) {
  const box = $("digest-status");
  if (!status) {
    box.textContent = "";
    return;
  }
  const when = status.last ? `上次早报：${status.days_since} 天前` : "还没有早报";
  const next = status.due ? "打开服务时会自动抓一期" : "今天的已经出过了";
  let html = `${escapeHtml(when)}　·　${escapeHtml(next)}`;
  if (status.last_error) {
    html += `<div class="error">上次自动抓取失败：${escapeHtml(status.last_error)}</div>`;
  }
  box.innerHTML = html;
}

async function openDigest(name) {
  digestState.active = name;
  $("digest-items")
    .querySelectorAll(".note-card")
    .forEach((el) => el.classList.toggle("active", el.dataset.name === name));

  const box = $("digest-body");
  try {
    const data = await api(`/api/digest?name=${encodeURIComponent(name)}`);
    box.innerHTML = renderMarkdown(data.body);
  } catch (err) {
    box.innerHTML = `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

async function makeDigest() {
  const btn = $("btn-new-digest");
  btn.disabled = true;
  $("digest-body").innerHTML =
    '<div class="thinking">正在查最近的动态…（要联网，会花几次搜索加一次模型调用）</div>';
  try {
    const data = await api("/api/digest/new", { method: "POST" });
    digestState.active = "";
    $("digest-body").innerHTML = renderMarkdown(data.markdown);
    loadDigests();
  } catch (err) {
    $("digest-body").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
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

    renderReviewStatus(plan.review);
    renderReviews(plan.reviews || []);
    renderStages(plan.stages);
  } catch (err) {
    $("plan-stages").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

function renderReviews(reviews) {
  const box = $("plan-retro");
  if (!reviews.length) {
    box.innerHTML = '<div class="muted">还没有复盘。点上面的按钮做一次。</div>';
    return;
  }

  box.innerHTML = reviews.map((review) => `
    <div class="entry">
      <div class="head">
        <div class="t">${escapeHtml(review.title)}</div>
        <button class="del" type="button" data-title="${escapeHtml(review.title)}"
                title="删掉这条复盘">×</button>
      </div>
      <div class="body">${renderMarkdown(review.body)}</div>
    </div>`).join("");

  box.querySelectorAll(".entry .del").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (await deleteItem("review", btn.dataset.title, btn.dataset.title)) loadPlan();
    });
  });
}

function renderReviewStatus(review) {
  const box = $("retro-status");
  if (!review) {
    box.innerHTML = "";
    return;
  }

  const when = review.last
    ? `上次复盘：${review.last}（${review.days_since} 天前）`
    : "还没有复盘过";
  const next = review.due
    ? "下次自动复盘：服务下次打开时"
    : `下次自动复盘：${review.due_in} 天后`;

  let html = `${escapeHtml(when)}　·　${escapeHtml(next)}`;
  if (review.last_error) {
    html += `<div class="error">上次自动复盘失败：${escapeHtml(review.last_error)}</div>`;
  }
  box.innerHTML = html;
}

async function makeReview() {
  const btn = $("btn-review");
  btn.disabled = true;
  $("plan-retro").innerHTML =
    '<div class="thinking">正在回看最近一周…（要花一次模型调用）</div>';
  try {
    const data = await api("/api/review", { method: "POST" });
    $("plan-retro").innerHTML = renderMarkdown(data.markdown);
    loadPlan();   // 复盘时间变了，状态那行也要跟着更新
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
        <h3>
          <span class="name">${escapeHtml(stage.name)}${badge}</span>
          <button class="del" type="button" data-name="${escapeHtml(stage.name)}"
                  title="删掉这个阶段（连同它的里程碑）">×</button>
        </h3>${items}
      </div>`;
  }).join("");

  box.querySelectorAll(".stage .del").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (await deleteItem("stage", btn.dataset.name, btn.dataset.name)) loadPlan();
    });
  });
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
      <div class="head">
        <h1>${escapeHtml(note.title)}</h1>
        <button class="del" id="btn-del-note" type="button" title="删掉这篇笔记">×</button>
      </div>
      <div class="meta">${escapeHtml(note.date || "无日期")}${tags ? " · " + tags : ""} · ${escapeHtml(note.name)}</div>
      ${renderMarkdown(body)}`;

    $("btn-del-note").addEventListener("click", async () => {
      if (await deleteItem("note", note.name, note.title)) {
        notesState.active = "";
        box.innerHTML = '<div class="hint">从左边选一篇笔记</div>';
        loadNotes();
      }
    });
  } catch (err) {
    box.innerHTML = `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

/* ---------------------------------------------------------------- 绑定 */

/* ---------------------------------------------------------------- 练习 */

let practiceData = null;

// 用户这次的答案只留在本地：值得落盘的是判定和错题，答案本身不值得存
// （刷新页面就没了——想留着就自己存成笔记）。
const practiceAnswers = {};

function practiceStatus(text) {
  const running = Boolean(text);
  $("btn-quiz").disabled = running;
  $("btn-grade").disabled = running;
  $("quiz-status").textContent = running ? text : practiceHeadline(practiceData);
}

/* 把答题框里已经敲进去的字先捞回内存，再重新画页面。

   为什么要这一步：你正在练习页写答案，切去别的页再切回来、或者她刚好在对话里
   动过练习（出题/批改），页面都会重画——不捞一遍就等于替你把答案删了。
   出题那条路（startQuiz）故意**不**捞：新题目要的是空白卷。
*/
function harvestAnswers() {
  document.querySelectorAll(".q-answer").forEach((box) => {
    const index = Number(box.dataset.index);
    if (index) practiceAnswers[index] = box.value;
  });
}

async function loadPractice() {
  harvestAnswers();
  try {
    practiceData = await api("/api/practice");
    renderPractice();
  } catch (err) {
    $("quiz-body").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

function renderPractice() {
  try {
    paintPractice(practiceData || {});
  } catch (err) {
    // 渲染炸了也得说一声。以前这种错只会留在 console 里，页面上是一片安静——
    // 用户看到的就是"点了没反应"，而我们什么都收不到。
    const message = err.message || String(err);
    $("quiz-body").innerHTML = `<div class="hint error">题目画不出来：${escapeHtml(message)}</div>`;
    reportPageError(message);
  }
}

function paintPractice(data) {
  const questions = data.questions || [];
  $("quiz-actions").classList.toggle("hidden", !data.has_quiz || data.graded);
  practiceStatus("");

  if (!data.has_quiz) {
    $("quiz-body").innerHTML =
      `<div class="hint">还没有题目。点上面的「出一份」，或者直接在对话里说"考考我"。</div>`;
    $("quiz-score").textContent = "还没批改";
    return;
  }

  $("quiz-body").innerHTML = questions
    .map((q) => {
      const mark = q.verdict
        ? `<span class="verdict ${verdictClass(q.verdict)}">${escapeHtml(q.verdict)}</span>`
        : "";
      const note = q.verdict
        ? `<div class="q-note">${escapeHtml(noteText(q))}</div>`
        : "";
      const saved = escapeHtml(practiceAnswers[q.index] || "");
      return `<article class="q-card">
        <div class="q-head"><span class="q-index">${q.index}</span><span class="q-prompt">${escapeHtml(q.prompt)}</span>${mark}</div>
        <textarea class="q-answer" data-index="${q.index}" rows="2" placeholder="在这里答…"${q.verdict ? " readonly" : ""}>${saved}</textarea>
        ${note}
      </article>`;
    })
    .join("");

  $("quiz-score").textContent = scoreLine(data);
}

async function startQuiz() {
  const topic = $("quiz-topic").value.trim();
  // 出题要跑一整轮：读计划、翻笔记、再写几道题——半分钟很正常。
  // 上一版这儿只写"她正在出题…"，等的人以为按钮坏了，就跑去对话里问了。
  practiceStatus("她正在出题…（要翻你的计划和笔记，大概半分钟）");
  try {
    const data = await api("/api/practice/quiz", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic }),
    });
    practiceData = data.practice;
    Object.keys(practiceAnswers).forEach((key) => delete practiceAnswers[key]);
    renderPractice();
    if (!practiceData.has_quiz) {
      // 她可能先说"我得先看看你在学什么"，也可能直接没出题——如实说，别装作出好了
      $("quiz-body").innerHTML =
        `<div class="hint error">她这次没出成题。去对话页看看她说了什么——多半是材料不够，或者她想先跟你聊聊方向。</div>`;
    }
  } catch (err) {
    practiceStatus("");
    $("quiz-body").innerHTML =
      `<div class="hint error">${escapeHtml(err.message || String(err))}</div>`;
  }
}

async function gradeQuiz() {
  const boxes = Array.from(document.querySelectorAll(".q-answer"));
  const items = boxes.map((box) => {
    const index = Number(box.dataset.index);
    practiceAnswers[index] = box.value;   // 先本地留一份：批完还能对着看
    return { index, answer: box.value };
  });

  practiceStatus("她正在批改…");
  try {
    const data = await api("/api/practice/grade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers: formatAnswers(items) }),
    });
    practiceData = data.practice;
    renderPractice();
  } catch (err) {
    practiceStatus("");
    $("quiz-grade-status").textContent = err.message || String(err);
  }
}

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
$("btn-new-digest").addEventListener("click", makeDigest);
$("btn-quiz").addEventListener("click", startQuiz);
$("btn-grade").addEventListener("click", gradeQuiz);

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
loadDigests();
loadPractice();
input.focus();
