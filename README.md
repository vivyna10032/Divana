# Divana

陪伴式 AI 学习助手：答疑、知识总结、学习路径规划、技术热点追踪。

## 当前版本：v0.3

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

画像、笔记落盘、搜索解析的离线单测，不需要 key 也不需要网络。
改这几个模块就顺手跑一下——它们占了项目一多半的逻辑。

报 `No module named ...` 基本都是"忘了激活环境"，回到快速开始第 2 步。

## 她的记忆

Divana 有三层记忆，分工完全不同，这也是理解 agent 的一个好切口：

| 层 | 存在哪 | 装什么 | 怎么进模型 |
| --- | --- | --- | --- |
| 会话存档 | `data/divana.db` | 每轮对话的原样记录 | 只带最近若干条（见 `divana/session.py` 的 `HISTORY_LIMIT`） |
| 学习者画像 | `vault/profile.md` | 提炼过的结论：目标、水平、薄弱点… | 每轮都拼进 system prompt |
| 学习笔记 | `vault/notes/*.md` | 一个个概念，带出处 | 不进上下文，要查的时候她自己翻 |

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

## 目录结构

```
.
├─ prompts/divana.md      人格与教学法（改这里就能调教她的风格）
├─ divana/
│  ├─ config.py           配置：密钥、接口地址、模型、搜索
│  ├─ agent.py            agent 定义（人格 + 画像拼成 instructions）
│  ├─ context.py          工具依赖的容器（画像 / 笔记 / 搜索）
│  ├─ profile.py          学习者画像的读写
│  ├─ notes.py            笔记落盘（文件名清洗、防重名）
│  ├─ search.py           联网搜索（三个服务商适配器）
│  ├─ storage.py          原子写入
│  ├─ session.py          会话持久化（SQLite）
│  ├─ tools.py            给模型用的工具
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
- [ ] **v0.4 会总结** 把一次对话整理成结构化笔记
- [ ] **v0.5 会规划** plan.md 记录学习路径，每周复盘
- [ ] **v0.6 抓热点** 定时扫描 AI 动态，按你的方向过滤成早报
