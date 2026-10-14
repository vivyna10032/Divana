/* 用假 DOM 把前端三个脚本真跑一遍：不开浏览器，也能知道"页面画不画得出来"。
 *
 * 为什么值得单独留一个（它是这次事故换来的）：
 * 用户报"点了没反应"的时候，服务端是对的、HTML 是对的、接口返回也是对的——
 * 错只可能在渲染那几行。而那种错：
 *   · Python 测试看不见（它根本不执行前端）；
 *   · 静态检查也看不见（id 都在、函数名都在，就是运行时炸了）。
 * 只有真跑一遍才知道。于是这里搭一个最小的假 DOM（只有 getElementById 和
 * innerHTML 是真会用的），把 markdown.js / quiz.js / app.js 按浏览器的顺序执行，
 * 喂一份真的练习数据，然后把渲染结果报回去。
 *
 * 用法：node tests/frontend_harness.mjs <payload.json>
 *       或者把 payload 的 JSON 从标准输入喂进来（测试里就是这么用的）
 * 输出：一行 JSON —— 题目区 HTML、成绩行、顶上状态行、错误横幅、答题框个数
 */

import fs from "node:fs";

const WEB = new URL("../web/", import.meta.url);
const raw = process.argv[2]
  ? fs.readFileSync(process.argv[2], "utf8")
  : fs.readFileSync(0, "utf8");
const payload = JSON.parse(raw);

/* ------------------------------------------------------------ 假 DOM */

const els = new Map();

function makeEl(id) {
  return {
    id,
    innerHTML: "",
    textContent: "",
    value: "",
    disabled: false,
    dataset: {},
    style: {},
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener() {},
    appendChild() {},
    querySelectorAll: () => [],
    focus() {},
  };
}

globalThis.window = globalThis;
globalThis.addEventListener = () => {}; // node 的 globalThis 上没有这个（浏览器才有）
globalThis.confirm = () => false;
globalThis.alert = () => {};
globalThis.document = {
  getElementById: (id) => els.get(id) || null,
  createElement: (tag) => makeEl(tag),
  querySelector: () => null,
  querySelectorAll: () => [], // 假 DOM 不会解析 innerHTML，所以这里永远是空
  addEventListener() {},
  body: {
    firstChild: null,
    // reportPageError 就是靠这个把红带挂上去的；挂的时候顺手登记，测试才拿得到它
    insertBefore(el) {
      if (el.id) els.set(el.id, el);
    },
  },
};

// index.html 里有哪些 id，这里就造哪些；app.js 伸手去够不存在的 id 会当场暴露
const html = fs.readFileSync(new URL("index.html", WEB), "utf8");
for (const [, id] of html.matchAll(/id="([^"]+)"/g)) els.set(id, makeEl(id));

globalThis.fetch = async (path) => {
  if (path === "/api/practice") {
    return { ok: true, status: 200, json: async () => payload };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};

/* ------------------------------------------------------------ 跑起来 */

const source = ["markdown.js", "quiz.js", "app.js"]
  .map((name) => fs.readFileSync(new URL(name, WEB), "utf8"))
  .join("\n;\n");

// 包成一个函数体：这样三个脚本的函数声明落在同一作用域里，彼此能看见——
// 这正是浏览器里 <script> 之间的真实关系
const boot = new Function(`${source}\nreturn { loadPractice };`);

const body = () => els.get("quiz-body");

let crashed = "";
try {
  // 脚本一加载就会跑最后那段启动代码（挂监听器、拉数据）。它要是抛了，
  // 浏览器里的表现就是"整页没反应"——所以这一步单独试，和渲染分开报。
  const page = boot();
  try {
    await page.loadPractice();
  } catch (err) {
    crashed = `loadPractice 抛了：${err.message}`;
  }
} catch (err) {
  crashed = `脚本加载就抛了：${err.message}`;
}

const rendered = body().innerHTML;

process.stdout.write(
  JSON.stringify({
    crashed,
    seenIds: [...els.keys()].length,
    quizBody: rendered,
    textareas: (rendered.match(/class="q-answer"/g) || []).length,
    score: els.get("quiz-score").textContent,
    headline: els.get("quiz-status").textContent,
    banner: els.has("page-error") ? els.get("page-error").textContent : "",
  })
);
