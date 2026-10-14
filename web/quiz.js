/* 练习页的纯逻辑：拼答案、判定配色、成绩文案。

单独一个文件，理由和 markdown.js 一样：**不碰 DOM**，纯字符串进、纯字符串出，
所以能用 node 直接测（见 tests/test_webapp.py）。

其中最要紧的是 formatAnswers：网页提交批改时，我们其实是在"替用户说一句话"——
说成什么样，直接决定她判得准不准。所以它值得单独存在、单独测。
*/

/** 把答题框里的内容拼成一条给她看的输入。
 *
 * 没答的题也要发过去（写成"（没答）"）：漏着不发，成绩就只按答过的算，
 * 那等于替用户把不会的题藏起来了。
 */
function formatAnswers(items) {
  return items
    .map((item) => {
      const answer = String(item.answer || "").trim();
      return `第 ${item.index} 题：${answer || "（没答）"}`;
    })
    .join("\n");
}

/** 判定 → CSS 类后缀。只有三档：对 / 半对 / 错。 */
function verdictClass(verdict) {
  if (verdict === "对") return "ok";
  if (verdict === "半对") return "half";
  if (verdict === "错") return "bad";
  return "";
}

/** 一道题批改后的说明：漏了哪些要点、她的一句评语。 */
function noteText(question) {
  const parts = [];
  const missing = question.missing || [];
  if (missing.length) parts.push("漏了：" + missing.join("、"));
  if (question.comment) parts.push(question.comment);
  return parts.join("；");
}

/** 成绩那一行。没批改时给一句人话，不要显示 0 分。 */
function scoreLine(practice) {
  if (!practice || !practice.graded) return "还没批改";
  return (
    `${practice.total} 题：对 ${practice.right}、半对 ${practice.half}、` +
    `错 ${practice.wrong}（${practice.percent} 分）`
  );
}

// 这样浏览器里当普通脚本用，node 里也能 require 进来测
if (typeof module !== "undefined" && module.exports) {
  module.exports = { formatAnswers, verdictClass, noteText, scoreLine };
}
