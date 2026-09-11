# Divana

陪伴式 AI 学习助手：答疑、知识总结、学习路径规划、技术热点追踪。

## 当前版本：v0.2

命令行学习伴侣，已接入 DeepSeek，模型可配置。

## 快速开始

```powershell
# 1. 建环境、装依赖
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. 配置密钥
Copy-Item .env.example .env
# 打开 .env，填入你的 DIVANA_API_KEY

# 3. 开始聊
python -m divana
```

## 换模型 / 换服务商

密钥、接口地址、模型名都在 `.env` 里，改完重启即可，不需要动代码：

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `DIVANA_API_KEY` | 密钥，必填 | 无 |
| `DIVANA_BASE_URL` | 接口地址 | `https://api.deepseek.com` |
| `DIVANA_MODEL` | 模型名 | `deepseek-v4-pro` |

当前 DeepSeek 接口可用的模型名是 `deepseek-v4-pro` 和 `deepseek-flash`。
换成其它 OpenAI 兼容服务时，把 `DIVANA_BASE_URL` 和 `DIVANA_MODEL` 一起改掉就行。

## 自检

改完配置先跑这个，比直接聊天更快定位问题：

```powershell
python -m scripts.smoke_test
```

它会打印当前用的模型和接口地址，然后让 Divana 自我介绍一句。

## 目录结构

```
.
├─ prompts/divana.md      人格与教学法（改这里就能调教她的风格）
├─ divana/
│  ├─ config.py           配置：密钥、接口地址、模型
│  ├─ agent.py            agent 定义
│  ├─ cli.py              命令行交互循环
│  └─ __main__.py         python -m divana 的入口
├─ scripts/smoke_test.py  连通性自检
└─ vault/notes/           学习笔记落盘位置
```

## 路线图

- [x] **v0.1 会说话** CLI + 人格 prompt + 多轮上下文
- [x] **v0.2 可换模型** 配置外置到 .env，接入 DeepSeek
- [ ] **v0.3 会查证** 接联网搜索，答案带出处；概念能落盘成笔记
- [ ] **v0.4 会总结** 把一次对话整理成结构化笔记
- [ ] **v0.5 会规划** plan.md 记录学习路径，每周复盘
- [ ] **v0.6 抓热点** 定时扫描 AI 动态，按你的方向过滤成早报