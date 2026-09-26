"""生成《Divana 项目里的 Agent 工程》PDF。

框架部分：字体、样式、图表、页眉页脚、目录。正文内容在 content.py 里，
用一个小 DSL 写成数据，这样改文字不用碰排版代码。

用运行时自带的 Python 跑（带 reportlab）：
  C:/Users/HS/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe
  （运行目录要在项目根目录：它会读 web/ 以外的相对路径，输出到 output/pdf/）
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content import BLOCKS as CORE_BLOCKS  # noqa: E402
from content_v010 import CHAPTERS as EXTRA_CHAPTERS  # noqa: E402


def _with_new_chapters(blocks: list, extra: list) -> list:
    """把 v0.8~v0.10 那两章插在「面试速查」之前。

    为什么不直接写进 content.py：那两章要插在**中间**（面试速查和"接下来"要留在最后），
    而 content.py 是 600 多行中文数据，插错位置很难看出来。单独一步、单独一个文件，
    改完还能对着目录核一遍。
    """
    for index, (kind, payload) in enumerate(blocks):
        if kind == "h1" and payload.startswith("第 9 章"):
            return [*blocks[:index], *extra, *blocks[index:]]
    return [*blocks, *extra]


BLOCKS = _with_new_chapters(CORE_BLOCKS, EXTRA_CHAPTERS)

FONT_DIR = Path("C:/Windows/Fonts")
OUT_PATH = Path("output/pdf/Divana-Agent-学习手册.pdf")

ACCENT = colors.HexColor("#16a34a")
ACCENT_DARK = colors.HexColor("#14612f")
INK = colors.HexColor("#1f2421")
MUTED = colors.HexColor("#6b7280")
LINE = colors.HexColor("#dfe5e0")
SOFT = colors.HexColor("#f2f7f3")
CODE_BG = colors.HexColor("#f4f6f4")

# ---------------------------------------------------------------- 字体

pdfmetrics.registerFont(TTFont("CJK", str(FONT_DIR / "msyh.ttc"), subfontIndex=0))
pdfmetrics.registerFont(TTFont("CJKB", str(FONT_DIR / "msyhbd.ttc"), subfontIndex=0))
pdfmetrics.registerFont(TTFont("Mono", str(FONT_DIR / "consola.ttf")))
pdfmetrics.registerFont(TTFont("MonoB", str(FONT_DIR / "consolab.ttf")))
pdfmetrics.registerFontFamily(
    "CJK", normal="CJK", bold="CJKB", italic="CJK", boldItalic="CJKB"
)

# 代码块里会出现中文：示例输出、注释都有。Consolas 没有中文字形，中文会**静默消失**
# （第一版就丢过一整行 [回归]/[修好]，看起来只是"空了几行"）。MS Gothic 是等宽的，
# 中英文都在；系统里万一没有这种字体，就退回 Consolas（纯 ASCII 的代码照样好看）。
_CODE_FONT = "Mono"
if (FONT_DIR / "msgothic.ttc").exists():
    pdfmetrics.registerFont(
        TTFont("CJKMono", str(FONT_DIR / "msgothic.ttc"), subfontIndex=0)
    )
    _CODE_FONT = "CJKMono"

# ---------------------------------------------------------------- 样式

S = {
    "title": ParagraphStyle(
        "title", fontName="CJKB", fontSize=27, leading=38, textColor=INK
    ),
    "subtitle": ParagraphStyle(
        "subtitle", fontName="CJK", fontSize=13, leading=22, textColor=MUTED
    ),
    "h1": ParagraphStyle(
        "H1",
        fontName="CJKB",
        fontSize=17,
        leading=25,
        textColor=INK,
        spaceBefore=6,
        spaceAfter=10,
    ),
    "h2": ParagraphStyle(
        "H2",
        fontName="CJKB",
        fontSize=13,
        leading=20,
        textColor=ACCENT_DARK,
        spaceBefore=13,
        spaceAfter=6,
    ),
    "h3": ParagraphStyle(
        "H3",
        fontName="CJKB",
        fontSize=11.2,
        leading=18,
        textColor=INK,
        spaceBefore=9,
        spaceAfter=4,
    ),
    "body": ParagraphStyle(
        "body",
        fontName="CJK",
        fontSize=10.4,
        leading=17.6,
        textColor=INK,
        spaceAfter=6,
        alignment=TA_LEFT,
        wordWrap="CJK",
    ),
    "lead": ParagraphStyle(
        "lead",
        fontName="CJK",
        fontSize=10.2,
        leading=17,
        textColor=ACCENT_DARK,
        leftIndent=8,
        spaceAfter=9,
        wordWrap="CJK",
    ),
    "bullet": ParagraphStyle(
        "bullet",
        fontName="CJK",
        # 项目符号默认用 Helvetica，而它不会被打进 PDF（基础字体之一）。
        # 换成 CJK 字体，所有字形才会随文件一起走。
        bulletFontName="CJK",
        fontSize=10.4,
        leading=17,
        textColor=INK,
        leftIndent=13,
        bulletIndent=3,
        spaceAfter=3.5,
        wordWrap="CJK",
    ),
    "note": ParagraphStyle(
        "note",
        fontName="CJK",
        fontSize=9.8,
        leading=16,
        textColor=colors.HexColor("#3f4a44"),
        wordWrap="CJK",
    ),
    "caption": ParagraphStyle(
        "caption",
        fontName="CJK",
        fontSize=8.8,
        leading=14,
        textColor=MUTED,
        spaceBefore=3,
        spaceAfter=10,
        alignment=TA_CENTER,
        wordWrap="CJK",
    ),
    "cell": ParagraphStyle(
        "cell", fontName="CJK", fontSize=9.1, leading=14.5, textColor=INK, wordWrap="CJK"
    ),
    "cellb": ParagraphStyle(
        "cellb", fontName="CJKB", fontSize=9.1, leading=14.5, textColor=INK, wordWrap="CJK"
    ),
    "code": ParagraphStyle(
        "code",
        fontName=_CODE_FONT,
        fontSize=8.4,
        leading=12.6,
        textColor=colors.HexColor("#111827"),
    ),
    "toc1": ParagraphStyle(
        "toc1",
        fontName="CJKB",
        fontSize=10.4,
        leading=20,
        spaceBefore=6,
        textColor=INK,
    ),
    "toc2": ParagraphStyle(
        "toc2",
        fontName="CJK",
        fontSize=9.8,
        leading=16,
        textColor=colors.HexColor("#3f4a44"),
        leftIndent=14,
    ),
}

# TableOfContents.levelStyles 要的是"按层级排的样式列表"，第 0 项对应一级标题
TOC_STYLE_LEVELS = [S["toc1"], S["toc2"]]


# ---------------------------------------------------------------- 图表


def _box(d, x, y, w, h, text, *, fill=SOFT, stroke=LINE, font="CJK", size=8.6, color=INK):
    d.add(Rect(x, y, w, h, fillColor=fill, strokeColor=stroke, strokeWidth=0.7, rx=3, ry=3))
    lines = text.split("\n")
    total = len(lines)
    start = y + h / 2 + (total - 1) * (size * 0.62)
    for i, line in enumerate(lines):
        d.add(
            String(
                x + w / 2,
                start - i * (size * 1.24),
                line,
                fontName=font,
                fontSize=size,
                fillColor=color,
                textAnchor="middle",
            )
        )


def _arrow(d, x1, y1, x2, y2, color=MUTED):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=0.8))
    # 箭头尖朝右
    d.add(Polygon([x2, y2, x2 - 4.2, y2 + 2.4, x2 - 4.2, y2 - 2.4], fillColor=color, strokeColor=color))


def figure_ask_flow() -> Drawing:
    """一次提问的完整流程。"""
    d = Drawing(470, 206)
    _box(d, 6, 158, 118, 30, "你敲一句话", fill=colors.white)
    _box(d, 156, 158, 200, 30, "拼出这次请求\n（历史 + 指令 + 工具定义）", fill=colors.white)
    _box(d, 156, 100, 120, 30, "模型决定\n回答还是调工具", fill=SOFT)
    _box(d, 330, 100, 134, 30, "工具真的执行\n（搜索 / 读仓库 / 写文件）", fill=colors.white)
    _box(d, 36, 24, 150, 30, "输出最终回答\n并写回会话库", fill=colors.white)

    _arrow(d, 124, 173, 156, 173)          # 你 -> 拼请求
    _arrow(d, 216, 158, 216, 130)          # 拼请求 -> 模型
    _arrow(d, 276, 115, 330, 115)          # 模型 -> 工具
    d.add(String(303, 124, "要调工具", fontName="CJK", fontSize=7.4, fillColor=MUTED, textAnchor="middle"))

    # 工具结果回到模型（画在下面那条回路上）
    d.add(Line(400, 100, 400, 72, strokeColor=MUTED, strokeWidth=0.8))
    d.add(Line(400, 72, 246, 72, strokeColor=MUTED, strokeWidth=0.8))
    _arrow(d, 246, 72, 246, 100)
    d.add(String(323, 76, "结果塞回去再问一次", fontName="CJK", fontSize=7.4, fillColor=MUTED, textAnchor="middle"))

    # 模型直接回答
    d.add(Line(156, 112, 111, 112, strokeColor=MUTED, strokeWidth=0.8))
    _arrow(d, 111, 112, 111, 54)
    return d


def figure_rule() -> Drawing:
    """封面上那根绿色短线。"""
    d = Drawing(470, 6)
    d.add(Line(0, 3, 56, 3, strokeColor=ACCENT, strokeWidth=3))
    return d


def figure_layers() -> Drawing:
    """四层架构。"""
    d = Drawing(480, 150)
    rows = [
        ("显示层", "cli.py（终端）　web/（网页）", "只有这一层知道\"界面\"长什么样"),
        ("服务层", "service.py", "把能力组合成动作，返回结构化数据"),
        ("能力层", "搜索 / 读材料 / 总结 / 计划 / 笔记 / 画像", "每个模块只干一件事，纯逻辑可离线测试"),
        ("数据层", "vault/*.md　data/divana.db", "画像、计划、笔记、对话存档"),
    ]
    y = 110
    fills = [colors.HexColor("#e7f4ec"), colors.HexColor("#eef6f1"), colors.HexColor("#f5faf7"), colors.white]
    for i, (name, detail, note) in enumerate(rows):
        d.add(Rect(6, y, 468, 30, fillColor=fills[i], strokeColor=LINE, strokeWidth=0.7, rx=3, ry=3))
        _box(d, 12, y + 5, 62, 20, name, fill=ACCENT if i < 2 else colors.HexColor("#dcece2"), stroke=LINE, font="CJKB", size=9)
        d.add(String(84, y + 15.5, detail, fontName="CJK", fontSize=8.8, fillColor=INK))
        d.add(String(84, y + 5.5, note, fontName="CJK", fontSize=7.8, fillColor=MUTED))
        y -= 34
    return d


FIGURES = {"ask_flow": figure_ask_flow, "layers": figure_layers, "rule": figure_rule}


# ---------------------------------------------------------------- 文档模板


class Handbook(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in {"H1", "H2"}:
            if flowable.getPlainText() == "目录":
                return
            level = 0 if flowable.style.name == "H1" else 1
            self.notify("TOCEntry", (level, flowable.getPlainText(), self.page))

    def on_page(self, canvas, doc):
        if doc.page == 1:
            return
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.6)
        canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
        canvas.setFont("CJK", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 11 * mm, "Divana 项目里的 Agent 工程")
        canvas.drawRightString(A4[0] - 20 * mm, 11 * mm, f"第 {doc.page} 页")
        canvas.restoreState()


# ---------------------------------------------------------------- block -> flowable


def table_flow(spec):
    head = spec["head"]
    rows = spec["rows"]
    widths = spec.get("widths")
    data = [[Paragraph(c, S["cellb"]) for c in head]]
    for row in rows:
        data.append([Paragraph(c, S["cell"]) for c in row])

    col_widths = None
    if widths:
        avail = A4[0] - 40 * mm
        col_widths = [avail * w for w in widths]

    t = Table(data, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), SOFT),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def code_flow(text):
    para = Paragraph(text.replace("\n", "<br/>").replace(" ", "&nbsp;"), S["code"])
    t = Table([[para]], colWidths=[A4[0] - 40 * mm], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return t


def note_flow(text):
    para = Paragraph(text, S["note"])
    t = Table([[para]], colWidths=[A4[0] - 40 * mm], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfdfb")),
                ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return t


def build_story():
    story = []
    toc = TableOfContents()
    toc.levelStyles = TOC_STYLE_LEVELS
    # 目录的每一条其实是表格的一行，而表格默认有 3pt 的上下内边距。
    # 38 条就是 200 多 pt，会把目录顶到第二页去。
    toc.tableStyle = TableStyle(
        [
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]
    )
    for kind, payload in BLOCKS:
        if kind == "pagebreak":
            story.append(PageBreak())
        elif kind == "toc":
            story.append(toc)
        elif kind == "spacer":
            story.append(Spacer(1, payload))
        elif kind == "title":
            story.append(Paragraph(payload, S["title"]))
        elif kind == "subtitle":
            story.append(Paragraph(payload, S["subtitle"]))
        elif kind in {"h1", "h2", "h3"}:
            story.append(Paragraph(payload, S[kind]))
        elif kind == "p":
            story.append(Paragraph(payload, S["body"]))
        elif kind == "lead":
            story.append(Paragraph(payload, S["lead"]))
        elif kind == "quote":
            story.append(Paragraph(payload, S["body"]))
        elif kind == "ul":
            for item in payload:
                story.append(Paragraph(item, S["bullet"], bulletText="•"))
            story.append(Spacer(1, 4))
        elif kind == "ol":
            for i, item in enumerate(payload, 1):
                story.append(Paragraph(item, S["bullet"], bulletText=f"{i}."))
            story.append(Spacer(1, 4))
        elif kind == "table":
            story.append(Spacer(1, 3))
            story.append(table_flow(payload))
            story.append(Spacer(1, 9))
        elif kind == "code":
            story.append(Spacer(1, 2))
            story.append(code_flow(payload))
            story.append(Spacer(1, 9))
        elif kind == "note":
            story.append(Spacer(1, 2))
            story.append(note_flow(payload))
            story.append(Spacer(1, 10))
        elif kind == "figure":
            story.append(Spacer(1, 4))
            story.append(FIGURES[payload]())
            story.append(Spacer(1, 2))
        elif kind == "caption":
            story.append(Paragraph(payload, S["caption"]))
        else:
            raise ValueError(f"不认识的 block 类型：{kind}")
    return story


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = Handbook(
        str(OUT_PATH),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title="Divana 项目里的 Agent 工程",
        author="Divana 项目笔记",
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body"
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=doc.on_page)])
    doc.multiBuild(build_story())
    print(f"已生成：{OUT_PATH.resolve()}")


if __name__ == "__main__":
    main()
