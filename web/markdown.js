/* markdown 渲染。

单独放一个文件，因为它**不碰 DOM**——纯字符串进、纯 HTML 出。这跟我在 Python
那边反复做的是同一件事（把纯逻辑和框架胶水分开）：纯函数才能脱离运行环境测试。
浏览器里当普通脚本用；node 里 require 进来就能测（见 tests/test_webapp.py）。

不求完整的 markdown，只求她常用的那几种看得清：标题、列表、引用、表格、代码块。
*/

/* 一定要先转义再替换——模型输出的 HTML 不能被执行。 */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function isTableRow(line) {
  // 至少两个单元格才算表格行（| a | b |）
  return line.startsWith("|") && line.split("|").length >= 3;
}

function isTableDivider(line) {
  return /^\|[\s:|-]+$/.test(line) && line.includes("-");
}

function splitRow(line) {
  return line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function renderTable(header, rows) {
  const head = header.map((cell) => `<th>${inline(cell)}</th>`).join("");
  const body = rows
    .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`)
    .join("");
  // 外面套一层可以横向滚动的容器：表格很容易比气泡宽
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead>` +
         `<tbody>${body}</tbody></table></div>`;
}

function renderMarkdown(text) {
  const lines = escapeHtml(text).split("\n");
  const out = [];
  let list = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  let i = 0;

  while (i < lines.length) {
    const line = lines[i].trim();

    if (!line) { closeList(); i++; continue; }

    // ``` 代码块：里面的内容原样保留，不再做行内替换
    if (line.startsWith("```")) {
      closeList();
      const lang = line.slice(3).trim();
      i++;
      const code = [];
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        code.push(lines[i]);
        i++;
      }
      i++; // 跳过收尾的 ```
      const attr = lang ? ` data-lang="${lang}"` : "";
      out.push(`<pre${attr}><code>${code.join("\n")}</code></pre>`);
      continue;
    }

    // 表格：本行是表头、下一行是 |---|---| 才算，免得把普通的竖线当成表
    if (isTableRow(line) && i + 1 < lines.length && isTableDivider(lines[i + 1].trim())) {
      closeList();
      const header = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableRow(lines[i].trim())) {
        rows.push(splitRow(lines[i].trim()));
        i++;
      }
      out.push(renderTable(header, rows));
      continue;
    }

    let m;
    if ((m = line.match(/^#{2,4}\s+(.*)/))) { closeList(); out.push(`<h3>${inline(m[1])}</h3>`); i++; continue; }
    if ((m = line.match(/^#\s+(.*)/))) { closeList(); out.push(`<h2>${inline(m[1])}</h2>`); i++; continue; }
    if ((m = line.match(/^[-*]\s+(.*)/))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`); i++; continue;
    }
    if ((m = line.match(/^\d+[.)]\s+(.*)/))) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[1])}</li>`); i++; continue;
    }
    if ((m = line.match(/^>\s?(.*)/))) { closeList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); i++; continue; }

    closeList();
    out.push(`<p>${inline(line)}</p>`);
    i++;
  }
  closeList();
  return out.join("");
}

// 浏览器里这段是空操作；node 里 require 进来才有 module
if (typeof module !== "undefined" && module.exports) {
  module.exports = { escapeHtml, inline, renderMarkdown };
}
