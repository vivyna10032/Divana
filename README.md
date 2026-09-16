# Divana

陪伴式 AI 学习助手：答疑、知识总结、学习路径规划、技术热点追踪。

## 当前版本：v0.7.2

命令行学习伴侣，已接入 DeepSeek，会记人、会查资料、会把概念落成笔记。

## 快速开始

需要 Python 3.13 或更高。下面的命令**显式指定解释器版本**，不要用光秃秃的 `python`：
如果这台机器上装了 Anaconda，`python` 很可能指向它，建出来的环境会跟项目对不上。

```powershell
# 1. 建环境（用 py 启动器指定 3.14）
py -3.14 -m venv .venv

# py -0p 如果列不出 Python，就走官方安装的完整路径：
# & "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m venv .venv

# 2. 激活：之后这个窗口里的 python / pip 都是项目自己的
.\.venv\Scripts\Activate.ps1

# 3. 装依赖
pip install -r requirements.txt

# 4. 配置密钥
Copy-Item .env.example .env
# 打开 .env，填入你的 DIVANA_API_KEY

# 5. 开始聊
python -m divana
```

激活成功后命令行开头会出现 `(.venv)`。**每开一个新窗口都要重新激活一次**，忘了激活就会报
`No module named 'agents'` 这类错。不想激活也行，把命令里的 `python` 换成
`.\.venv\Scripts\python.exe`，效果一样，而且没有歧义。

## 换模型 / 换服务商

密钥、接口地址、模型名都在 `.env` 里，改完重启即可，不需要动代码：

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `DIVANA_API_KEY` | 密钥，必填 | 无 |
| `DIVANA_BASE_URL` | 接口地址 | `https://api.deepseek.com` |
| `DIVANA_MODEL` | 模型名 | `deepseek-v4-pro` |

当前 DeepSeek 接口可用的模型名是 `deepseek-v4-pro` 和 `deepseek-flash`。
换成其它 OpenAI 兼容服务时，把 `DIVANA_BASE_URL` 和 `DIVANA_MODEL` 一起改掉就行。

## 联网搜索（可选）

不配也能用，只是她遇到不确定的事只能说不确定，查不了。

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `DIVANA_SEARCH_PROVIDER` | 服务商，三选一：`tavily` / `brave` / `serper` | `tavily` |
| `DIVANA_SEARCH_API_KEY` | 对应服务商的 key | 空（等于关闭搜索） |
| `DIVANA_SEARCH_MAX_RESULTS` | 每次搜索取几条 | `5` |

Tavily 在 [tavily.com](https://tavily.com) 注册就有免费额度，把 key 填进 `.env` 即可。
换服务商只改 `DIVANA_SEARCH_PROVIDER`——三个适配器都在 `divana/search.py` 里，
想接新的照抄一段就行。

## GitHub 配额（走代理的话建议配）

GitHub 未登录调 API 是 **60 次/小时**，而且**按你的出口 IP 算**。走代理的话，
这 60 次是同一个节点上所有人共享的——经常整天都是 403，看起来像"仓库不存在"。

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `DIVANA_GITHUB_TOKEN` | 配额提到 **5000 次/小时**，且按 token 算、不看 IP | 空（匿名） |

这个 token **不需要任何权限**：公开仓库匿名就能读，它纯粹用来提高配额。生成路径：
GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens，
勾一个 "Public Repositories (read-only)" 就够。

代码层面还省了一半配额：README 走 `raw.githubusercontent.com`（**那个域名不计入
API 配额**），只有文件名不标准时才退回 API 端点。所以现在读一个仓库只用 1 次 API。

## 自检

两个检查，按顺序跑就行。都不写文件，随便跑。

**一、环境对不对**（不联网，秒出）：

```powershell
python -m scripts.check_env
```

它会告诉你当前跑的是哪个 Python、是不是项目的 `.venv`、依赖装没装、`.env` 里的 key 读到没有。
最后一行是结论，有问题的项会编号列出来。

**二、模型通不通**（要联网，会花一点 token）：

```powershell
python -m scripts.smoke_test
```

它会打印当前用的模型和接口地址，然后让 Divana 自我介绍一句。

**三、纯逻辑对不对**（不联网，秒出）：

```powershell
python -m unittest discover -s tests -v
```

画像、笔记读写、搜索解析、配置解析、instructions 拼装的离线单测，
不需要 key 也不需要网络。
改这几个模块就顺手跑一下——它们占了项目一多半的逻辑。

报 `No module named ...` 基本都是"忘了激活环境"，回到快速开始第 2 步。

里面还有一组**网页层的测试**：起一个真服务器，用一个假 service 把 HTTP 和 SSE
跑一遍（它需要 starlette，没有就自动跳过）。

## 她的记忆

"记忆"其实是四份不同的东西，分工完全不同，这也是理解 agent 的一个好切口：

| 层 | 存在哪 | 装什么 | 怎么进模型 |
| --- | --- | --- | --- |
| 会话存档 | `data/divana.db` | 每轮对话的原样记录 | 只带最近若干条（见 `divana/session.py` 的 `HISTORY_LIMIT`） |
| 学习者画像 | `vault/profile.md` | 提炼过的结论：目标、水平、薄弱点… | 每轮都拼进 system prompt |
| 学习笔记 | `vault/notes/*.md` | 一个个概念，带出处 | 不进上下文，靠 `search_notes` / `read_note` 自己翻 |
| 学习计划 | `vault/plan.md` | 现在的位置 / 下一步 / 路线图 / 复盘记录 | 不进上下文，聊到方向时她自己读 |

**会话存档**：聊完自动落库，关掉窗口再启动就是接着上次聊。想开一段新记忆，换个会话名：

```powershell
python -m divana --session 2026-09
```

数据库里始终是全量记录，`HISTORY_LIMIT` 限制的只是"每次带多少进模型"——
聊得越久成本越高，这个上限就是刹车。

**学习者画像**：分五节（目标 / 当前水平 / 已掌握 / 薄弱点 / 学习习惯）。
她在聊到关于你自己的、以后还用得上的信息时会自己更新，并告诉你记了什么。
你也可以直接手改这个文件，她下次启动读到的就是最新版本；终端里输入 `/profile` 可以随时查看。

**学习笔记**：聊到一个值得留下的概念，她会问一句"要不要存成笔记"，你说好，
她就写成 `vault/notes/日期-标题.md`，带 `title / date / tags` 的 front-matter，
Obsidian 打开就能用。同名不会覆盖，会加 `-2`、`-3`。

三层记忆的分工是刻意的：**画像必须每轮都在眼前，所以它得小；笔记可以有很多，
所以它不进上下文，靠工具按需去取。** 什么该塞进 prompt、什么该做成工具按需查，
是 agent 工程里最常要权衡的一件事。

## 终端里的命令

在 `python -m divana` 里直接输入：

| 命令 | 作用 |
| --- | --- |
| `/profile` | 看 Divana 记了你什么（也可以直接编辑那个文件） |
| `/plan` | 看当前的学习计划（也可以直接编辑那个文件） |
| `/notes` | 列出手上的笔记 |
| `/search 关键词` | 不走模型，直接试一次联网搜索 |
| `/summary` | 把这次对话整理成一篇笔记（要花一次模型调用） |
| `exit` | 退出（`quit`、`退出` 也行），退出前会问一句要不要整理成笔记 |

`/search` 存在的意义是**把两个问题分开**："搜索本身通不通"和"模型会不会去调它"。
排查的时候这两件事必须能单独验证，否则你永远不知道是哪一环坏了。

启动信息里有三行也值得看一眼：

```
  配置来源: D:\CodexProjects\divana\.env（最后修改 2026-09-13 19:09）
  学习者画像: D:\CodexProjects\divana\vault\profile.md
  联网搜索: tavily（没配 key，遇到不确定的事只会说不确定）
```

`配置来源` 会告诉你读的是哪个 `.env`、最后什么时候改的；`联网搜索` 会区分
"服务商不认识""没配 key""已配置"三种情况。**"我明明填了却没生效"这类问题，先看这两行**——
实际经验里，九成是改错了文件、忘了保存，或者等号后面没粘上真正的值。

## 代码分层

```
vault/  data/        数据：画像、计划、笔记、对话存档
divana/*.py          能力：搜索、读材料、总结、规划……每个模块只干一件事
divana/service.py    服务层：把能力组合成"问一句 / 总结 / 看计划"这样的动作
divana/cli.py        显示层：读键盘、打印——唯一知道"终端"存在的地方
```

显示层只认服务层，服务层完全不认显示层。所以接前端时只需要**再写一个显示层**
（网页），能力一个字都不用动。**这就是为什么"抽服务层"是做前端的第一步，
而不是做完前端之后的整理工作。**

服务层返回的是**结构化数据**（`Reply` 里带回答文本、以及这轮调用了哪些工具），
不是打印好的字符串——网页想画成卡片、终端想打一行，都随调用方。

CLI 现在还有个 `--stream` 开关，加上它会边生成边显示（打字机效果）：

```powershell
python -m divana --stream
```

默认不开。因为它是一条比较新的代码路径（走 SDK 的流式接口），先用验证过的
非流式跑；想出问题也好定位。

## 学习手册（PDF）

`output/pdf/Divana-Agent-学习手册.pdf` 是一本 17 页的小册子，把这个项目里用到的
Agent 知识、架构取舍和工程技巧整理成了一册，专门为了两件事：把项目读薄，
以及面试前拿来复习。

```
第 1 章  这个项目是什么
第 2 章  核心概念（agent loop / instructions / 工具 / 上下文工程 / 记忆分层 / 多 agent）
第 3 章  工程技巧（分层与抽象时机 / 可测性 / 安全 / 健壮性）
第 4 章  十个真实的坑（最有价值的一章）
第 5 章  术语表（中英对照，每一条对应到项目里的哪一行）
第 6 章  面试速查（关键数字、可讲的故事、常见问题怎么答）
第 7 章  接下来
```

**它进了仓库是有意的**：推到 GitHub 之后，你在手机上就能直接打开看，
不用回寝室开电脑。

想改内容或者项目更新后重新生成：

```powershell
# 用自带的运行时 Python（里面有 reportlab）
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" docs\handbook\build_handbook.py
```

正文全部在 `docs/handbook/content.py` 里，用 `("类型", 内容)` 的形式写成数据，
排版在 `build_handbook.py` 里。改文字不用碰排版。

## 网页版

```powershell
python -m divana.webapp
```

然后浏览器打开 **http://127.0.0.1:8765**。左边是导航，现在两个页面：

- **对话**：聊天（带打字机效果），右边四块只读内容（学习目标 / 现在的位置 /
  下一步 / 最近的笔记）。输入框上方有三个快捷提问按钮——它们是把 prompt 里
  那些规则摆到台面上，省得你不知道能问什么。右下角还有"整理成笔记"按钮，
  因为网页没有"退出时询问"那个环节。
- **知识库**：左边是笔记列表，支持关键词搜索和标签筛选；右边读全文。

页面文件拆成了几个：`web/index.html`（结构）、`web/style.css`（样式）、
`web/app.js`（逻辑）、`web/markdown.js`（渲染），由 Starlette 的静态文件挂载提供。
**依然没有构建步骤**，改完刷新就生效。

`markdown.js` 单独放是有原因的：它不碰 DOM，纯字符串进、纯 HTML 出。所以
`tests/test_webapp.py` 可以用 node 直接把它 require 进来跑断言——包括"模型输出的
HTML 必须被转义"这条安全断言。跟 Python 那边把纯逻辑和框架胶水分开是同一个道理。
目前支持：标题、列表、引用、**表格**、代码块、加粗、行内代码、链接。

**它只监听本机（127.0.0.1）**，别改成 `0.0.0.0`：这个服务能读你本地的文件、
用你的 API key，暴露到局域网就等于把它们交出去。

它和命令行共用同一套东西——同一个 service 层、同一个会话库、同一份画像和笔记，
所以你在这边聊的，那边也记得。只是一次别同时在两个进程里聊同一场对话，
两边的历史会互相插话。

顺带说下为什么用 Starlette 而不是 FastAPI：**环境里本来就有**（openai-agents
通过 mcp 带进来的），不用装任何东西；而且 Starlette 就是 FastAPI 底下那一层，
路由写法几乎一样，想换随时能换。

## 她会做的事（工具）

模型自己能做的只有"说话"。要动手就得有工具——每个工具对应 `divana/tools.py` 里的一个函数：

| 工具 | 做什么 | 动到哪 |
| --- | --- | --- |
| `update_learner_profile` | 更新画像的某一节 | `vault/profile.md` |
| `read_plan` | 看当前的学习计划 | `vault/plan.md` |
| `update_plan` | 更新计划的某一节 | `vault/plan.md` |
| `search_web` | 联网查证 | 搜索服务商 |
| `save_note` | 把一个概念存成笔记 | `vault/notes/` |
| `search_notes` | 翻自己的笔记（传 `*` = 列出全部） | `vault/notes/` |
| `read_note` | 读某篇笔记的全文 | `vault/notes/` |
| `read_url` | 读网页正文 | 网络 |
| `read_github_repo` | 读 GitHub 仓库（元信息 + README） | GitHub API |
| `read_arxiv_paper` | 读 arXiv 论文（元信息 + 摘要 + 正文） | arXiv API |

工具的名字、描述（就是函数的 docstring）和参数类型，合起来是**给模型的说明书**——
模型看不到函数体，它只能靠这三样判断什么时候该调、参数怎么填。
所以**改工具说明就等于改她的行为**，而且它通常比改人格 prompt 更直接、更容易验证。

## 学习计划（v0.5 前半）

`vault/plan.md` 分四节：**现在的位置 / 下一步 / 路线图 / 复盘记录**。
终端里输入 `/plan` 可以随时看，也可以直接编辑那个文件。

它和画像的分工不一样，这个差别是刻意的：

| | 内容 | 进不进 instructions |
| --- | --- | --- |
| 学习者画像 | 你是谁、学到哪了（短、稳定） | **进**，每轮都在眼前 |
| 学习计划 | 接下来怎么走（会长、会改） | **不进**，聊到方向时她自己读 |

这是"上下文预算"这同一条取舍的第三次应用（前两次是画像 vs 笔记、笔记摘要 vs 全文）。
实际效果：你问"接下来学什么"，她会先 `read_plan` 再回答；你说"这个我学会了"，
她会把里程碑勾成 `- [x]`，并把下一件事提到"下一步"。

而且她做规划时有个硬要求：**必须基于画像和笔记**（你的目标、水平、学过什么），
不许凭空给一份通用学习路线——那种东西你问任何一个 AI 都有。

## 读外部材料（项目 / 论文 / 网页）

丢给她一个仓库地址、论文编号或者文章链接，她读进来再讲：

```text
帮我总结一下 https://github.com/openai/openai-agents-python
1706.03762 这篇论文讲了什么？
https://example.com/some-post 这篇博客的核心观点是什么
```

三条路线是分开的，因为"怎么拿到内容"完全不同：

| 材料 | 怎么读 | 为什么这么读 |
| --- | --- | --- |
| GitHub 仓库 | 官方 REST API（元信息 + raw README） | 仓库页面是 JS 渲染的，抓 HTML 只能拿到一堆脚本 |
| arXiv 论文 | 官方 Atom API（元信息 + 摘要）+ `arxiv.org/html/{id}` 拿正文 | 摘要够速览，正文拿得到就更深一层 |
| 其它网页 | 直接抓 HTML，去脚本样式后抽正文 | 没有 API 可用，只能这样 |

总结的结构（一句话 / 怎么做的 / 值得看的地方 / 局限 / 跟你有关 / 出处）写在
`prompts/divana.md` 里，想改格式就改那里。

几个已知边界，遇到别以为是坏了：

- **JS 渲染的页面抓不到东西**，她会明说"抓下来是空的"，而不是编内容
- **一份材料最多 12000 字**，超了会截断并标注，所以论文通常只覆盖到摘要和开头
- **arXiv 的旧论文可能没有 HTML 版**，那就只用摘要讲
- **只允许 http/https**：`file:///...` 会被挡掉——防的是她被网页里的话带着去读你本地的文件
- **GitHub 匿名配额是 60 次/小时，且按出口 IP 算**：走代理容易被同一个节点的别人用光。
  撞上时她会告诉你大概多久恢复、以及怎么彻底解决（见上面那节）

## 回顾一次对话（v0.4 后半）

聊完一场，她可以把这次对话整理成一篇结构化笔记，存进 `vault/notes/`——
所以以后用 `search_notes` 也翻得到。两种触发方式：

- **退出时问一句**："要不要把这次对话整理成一篇笔记？(y/N)"。默认不整理，你说了算
- **随时手动**：在对话里输入 `/summary`

为什么这件事需要**第二个 agent**，而"读项目/论文"不需要？这是理解 agent 分工的一个好切口：

| | 素材在哪 | 主 agent 看得到吗 |
| --- | --- | --- |
| 读项目 / 论文 | 刚抓进来的内容 | 看得到，就在它眼前——**不需要第二个 agent** |
| 回顾这次对话 | `data/divana.db` | **看不到**，它的上下文只有最近 40 条——所以要另起一个 |

总结者（`prompts/summarizer.md`）是一个**没有工具、指令完全不同**的 agent，
拿到的是从会话库还原出来的完整对话文本。它跑的时候不传 session，所以这次旁路调用
不会写回对话、也不影响正在进行的上下文。

**这就是多 agent 最朴素的形式：不是"多个角色在聊天"，而是不同任务用不同的上下文和不同的指令。**

## 目录结构

```
.
├─ prompts/divana.md      人格与教学法（改这里就能调教她的风格）
│  └─ summarizer.md       总结者的指令（改这里就能调总结的详略和结构）
├─ web/                   网页版（无构建步骤：改完刷新就生效）
│  ├─ index.html          结构
│  ├─ style.css           样式
│  ├─ app.js              逻辑（聊天流式、知识库、侧边栏）
│  └─ markdown.js         markdown 渲染（不碰 DOM，可用 node 测试）
├─ docs/handbook/         学习手册的正文与生成脚本
│  ├─ content.py          正文（改文字只改这个文件）
│  └─ build_handbook.py   排版（reportlab）
├─ output/pdf/            生成好的 PDF
├─ divana/
│  ├─ config.py           配置：密钥、接口地址、模型、搜索
│  ├─ agent.py            agent 定义（人格 + 画像拼成 instructions）
│  ├─ prompt.py           instructions 的拼装（人格 + 日期 + 画像）
│  ├─ context.py          工具依赖的容器（画像 / 笔记 / 搜索）
│  ├─ contracts.py        服务层和显示层的数据契约（纯数据，不依赖框架）
│  ├─ service.py          服务层：能力组合，不依赖终端
│  ├─ webapp.py           HTTP 层：路由 + SSE（Starlette）
│  ├─ profile.py          学习者画像的读写
│  ├─ plan.py             学习计划的读写
│  ├─ markdown_store.py   带固定章节的 markdown（画像和计划共用）
│  ├─ notes.py            笔记读写（文件名清洗、防重名、翻找）
│  ├─ search.py           联网搜索（三个服务商适配器）
│  ├─ fetch.py            读外部材料（网页 / GitHub / arXiv）
│  ├─ transcript.py       会话记录 → 对话文本；总结输出 → 标题 + 正文
│  ├─ summarize.py        总结者 agent（读会话库 → 交给它写笔记）
│  ├─ storage.py          原子写入
│  ├─ session.py          会话持久化（SQLite）
│  ├─ tools.py            给模型用的十个工具
│  ├─ cli.py              命令行交互循环
│  └─ __main__.py         python -m divana 的入口
├─ scripts/
│  ├─ check_env.py        环境自检（哪个 Python、依赖、配置）
│  └─ smoke_test.py       连通性自检
├─ tests/                 离线单元测试（画像 / 笔记 / 搜索解析）
├─ data/divana.db         对话存档（自动生成）
└─ vault/
   ├─ profile.md          学习者画像（自动生成，可以手改）
   ├─ plan.md             学习计划（自动生成，可以手改）
   └─ notes/              学习笔记（一篇一个概念）
```

## 路线图

- [x] **v0.1 会说话** CLI + 人格 prompt + 多轮上下文
- [x] **v0.2 可换模型** 配置外置到 .env，接入 DeepSeek
- [x] **v0.3 会查证** 会话持久化 + 学习者画像 + 联网搜索（答案带出处）+ 概念落盘成笔记
- [x] **v0.4 会读材料 / 会回顾** 读 GitHub 仓库、arXiv 论文、网页；把一次对话整理成笔记
- [x] **v0.5 会规划** plan.md 记录学习路径（现在的位置 / 下一步 / 路线图 / 复盘记录）
- [x] **v0.6 抽服务层** 能力和显示分开（`divana/service.py`），前端才能接进来
- [x] **v0.7 前半 · 最小界面** 单页：聊天（流式）+ 右侧看画像 / 计划 / 最近笔记
- [x] **v0.7 中段 · 知识库 + 快捷提问** 笔记列表（关键词 / 标签筛选）+ 全文；页面拆成 html/css/js
- [ ] **v0.7 后半 · 补齐其余页面** 计划页（里程碑进度）、画像页、会话列表、定时任务
- [ ] **v0.8 会复盘** 每周回顾一次，结论写进计划的"复盘记录"（挂在定时任务上）
- [ ] **v0.9 抓热点** 定时扫描 AI 动态，按你的方向过滤成早报

> 路线图在 v0.5 之后重排过一次。原因：**"每周复盘"和"早报"都需要"你没打开它
> 时也在跑"的载体**，而 CLI 是你打开才发生的。所以本地前端（连同定时任务）
> 不是装修，它是这两个功能的前提。

再往后还有一件用户提过、但不着急的事：

- [ ] **个性化** 自定义背景图和图标（2026-09-16 提出，非必须，等主流程稳定了再说）
