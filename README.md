# Divana

陪伴式 AI 学习助手：答疑、知识总结、学习路径规划、技术热点追踪。

## 当前版本：v0.1

一个能连续对话、带固定教学风格的命令行老师。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# 打开 .env，把 OPENAI_API_KEY 换成你自己的 key

python -m divana
```

## 目录结构

```
.
├─ prompts/divana.md      人格与教学法（改这里就能调教她的风格）
├─ divana/
│  ├─ agent.py            agent 定义：模型、指令、工具
│  ├─ cli.py              命令行交互循环
│  └─ __main__.py         python -m divana 的入口
└─ vault/notes/           学习笔记落盘位置
```

## 路线图

- [x] **v0.1 会说话** CLI + 人格 prompt + 多轮上下文
- [ ] **v0.2 会查证** 接 web_search，答案带出处；概念能落盘成笔记
- [ ] **v0.3 会总结** 把一次对话整理成结构化笔记
- [ ] **v0.4 会规划** plan.md 记录学习路径，每周复盘
- [ ] **v0.5 抓热点** 定时扫描 AI 动态，按你的方向过滤成早报
