# Divana

陪伴式 AI 学习助手：答疑、知识总结、学习路径规划、技术热点追踪。

## 当前版本：v0.4.0

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

## 她的记忆

Divana 有三层记忆，分工完全不同，这也是理解 agent 的一个好切口：

| 层 | 存在哪 | 装什么 | 怎么进模型 |
| --- | --- | --- | --- |
| 会话存档 | `data/divana.db` | 每轮对话的原样记录 | 只带最近若干条（见 `divana/session.py` 的 `HISTORY_LIMIT`） |
| 学习者画像 | `vault/profile.md` | 提炼过的结论：目标、水平、薄弱点… | 每轮都拼进 system prompt |
| 学习笔记 | `vault/notes/*.md` | 一个个概念，带出处 | 不进上下文，靠 `search_notes` / `read_note` 自己翻 |

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
| `/search 关键词` | 不走模型，直接试一次联网搜索 |
| `exit` | 退出（`quit`、`退出` 也行） |

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

## 她会做的事（工具）

模型自己能做的只有"说话"。要动手就得有工具——每个工具对应 `divana/tools.py` 里的一个函数：

| 工具 | 做什么 | 动到哪 |
| --- | --- | --- |
| `update_learner_profile` | 更新画像的某一节 | `vault/profile.md` |
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
- **GitHub API 未登录时每小时 60 次**，超了会提示"被限流"，等一会儿就好

## 目录结构

```
.
├─ prompts/divana.md      人格与教学法（改这里就能调教她的风格）
├─ divana/
│  ├─ config.py           配置：密钥、接口地址、模型、搜索
│  ├─ agent.py            agent 定义（人格 + 画像拼成 instructions）
│  ├─ prompt.py           instructions 的拼装（人格 + 日期 + 画像）
│  ├─ context.py          工具依赖的容器（画像 / 笔记 / 搜索）
│  ├─ profile.py          学习者画像的读写
│  ├─ notes.py            笔记读写（文件名清洗、防重名、翻找）
│  ├─ search.py           联网搜索（三个服务商适配器）
│  ├─ fetch.py            读外部材料（网页 / GitHub / arXiv）
│  ├─ storage.py          原子写入
│  ├─ session.py          会话持久化（SQLite）
│  ├─ tools.py            给模型用的八个工具
│  ├─ cli.py              命令行交互循环
│  └─ __main__.py         python -m divana 的入口
├─ scripts/
│  ├─ check_env.py        环境自检（哪个 Python、依赖、配置）
│  └─ smoke_test.py       连通性自检
├─ tests/                 离线单元测试（画像 / 笔记 / 搜索解析）
├─ data/divana.db         对话存档（自动生成）
└─ vault/
   ├─ profile.md          学习者画像（自动生成，可以手改）
   └─ notes/              学习笔记（一篇一个概念）
```

## 路线图

- [x] **v0.1 会说话** CLI + 人格 prompt + 多轮上下文
- [x] **v0.2 可换模型** 配置外置到 .env，接入 DeepSeek
- [x] **v0.3 会查证** 会话持久化 + 学习者画像 + 联网搜索（答案带出处）+ 概念落盘成笔记
- [x] **v0.4 前半 · 会读材料** 读 GitHub 仓库 / arXiv 论文 / 网页，并做结构化总结
- [ ] **v0.4 后半 · 会回顾** 把一次对话整理成结构化笔记（退出时询问 + `/summary`）
- [ ] **v0.5 会规划** plan.md 记录学习路径，每周复盘
- [ ] **v0.6 抓热点** 定时扫描 AI 动态，按你的方向过滤成早报
