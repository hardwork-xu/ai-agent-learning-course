# AI Agent Learning Course · AI 智能体应用开发与校招准备

**面向有基础编程能力的 CS 学生：理解机制，独立实现，用项目和实验回答面试追问。**

从一个只读工具开始，逐步完成中文知识检索、引用检查、HTTP 身份认证、工单草稿、人工审批和故障恢复。中文解释配合 English technical terms，默认实验无需 API Key。编程零基础读者先走补课路径；算法研究和 AI Infra 另有分流，不把所有 Agent 岗位混为一种。

[从这里开始](docs/start-here.md) · [先修诊断](docs/prerequisites.md) · [动手练习](docs/practice.md) · [学习路线](docs/roadmap.md) · [应用项目](docs/projects/capstone.md) · [面试训练](docs/interview/README.md)

## 选择入口

| 你现在的情况 | 从哪里开始 | 怎样知道可以往下走 |
| --- | --- | --- |
| 会写基础 Python，没学过 Agent | [先修诊断与补课](docs/prerequisites.md) | 自己的 JSON、HTTP、SQL 练习通过 |
| 看得懂示例，还不能自己写 | [引导 → 补全 → 独立迁移](docs/practice.md) | 学生提交检查通过，能解释失败原因 |
| 已能写工具与状态逻辑 | [中文 RAG](docs/projects/04-chinese-rag.md) → [完整服务](docs/projects/05-service.md) | 跑通证据、草稿、审批、重试、恢复 |
| 准备投递实习／校招 | [综合项目验收](docs/projects/capstone.md) → [岗位映射](docs/job-readiness.md) | 有独立改动、实测报告、失败分析和答辩记录 |

## 先观察一个小系统

需要 Python 3.11+。下载或 clone 后，进入含有 `pyproject.toml` 的仓库根目录：

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m agentlab demo tools
python scripts/smoke_service.py
```

Windows PowerShell（直接调用虚拟环境，不需要执行激活脚本）：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m agentlab demo tools
.\.venv\Scripts\python.exe scripts/smoke_service.py
```

Windows 后文的 `python` 命令都可替换为 `.\.venv\Scripts\python.exe`；找不到 `py` 时，先确认 Python 已安装，再使用本机的 `python -m venv .venv`。仅离线运行源码时，macOS/Linux 可用 `PYTHONPATH=src python3 -m agentlab demo tools`；PowerShell 先执行 `$env:PYTHONPATH="src"`，再运行 `python -m agentlab demo tools`。具体入口见 [开始学习](docs/start-here.md)。

第一条演示使用预先编写的 Planner，输出 `status=completed` 和独立的 `task_success`；循环结束与任务正确分开判断。第二条命令启动真实本机 HTTP 服务，走完中文证据 → 草稿 → 测试审核人批准 → 工单 → 下游故障恢复，全程使用临时合成资料并自动清理。它没有调用模型，也不代表真人完成了审批或学习。

## 接下来必须自己写

把 [起始文件](exercises/starter.py) 复制到本地 `work/my_answers.py`，按 [练习教程](docs/practice.md) 逐步实现，然后运行：

```bash
python scripts/grade.py --submission work/my_answers.py --task prerequisites
python scripts/grade.py --submission work/my_answers.py --task inventory
python scripts/grade.py --submission work/my_answers.py --task quote
python scripts/grade.py --submission work/my_answers.py --task transfer --report work/transfer-grade.json
```

**没有填写的起始代码应该失败。** 评分命令读取你提交的文件；参考答案的检查通过不算你的练习通过。公开测试也不能证明独立性，还需要换约束后的迁移任务和代码解释。评分器会执行本地 Python 文件，只运行自己信任的代码。

## 知识地图

| 阶段 | 阅读 | 配套实践 |
| --- | --- | --- |
| 理解任务与模型 | [01 Agent 与 Workflow](docs/chapters/01-foundations.md)、[02 LLM 基础](docs/chapters/02-llm-basics.md)、[03 结构化输出](docs/chapters/03-prompts-and-structured-output.md) | 知识诊断、JSON 校验、终止与任务验收 |
| 连接工具与资料 | [04 工具](docs/chapters/04-tool-use.md)、[05 RAG](docs/chapters/05-retrieval.md)、[06 记忆](docs/chapters/06-memory-and-context.md) | 新工具、中文检索对照、引用与拒答 |
| 组织与恢复 | [07 Workflow](docs/chapters/07-planning-and-workflows.md)、[08 多 Agent](docs/chapters/08-multi-agent.md)、[框架与协议](docs/ecosystem.md) | SQLite 状态、版本审批、outbox 故障演练 |
| 验证与交付 | [09 评估](docs/chapters/09-evaluation.md)、[10 安全](docs/chapters/10-security.md)、[11 生产工程](docs/chapters/11-production.md) | 完整服务、学生独立改动、答辩与交付清单 |
| 研究分支 | [12 研究前沿](docs/chapters/12-research-frontier.md)、[复现实验](docs/research-lab.md) | 基线、消融与误差分析；训练／Infra 专项另学 |

## 项目与证据

- **基础实验 01–03**：工具边界、英文词项检索、可信调用方的审批事务，适合看清单个机制。
- **项目 04**：中文 BM25、资料版本与权限过滤、冲突拒答、逐题评测；可选本地模型生成草稿，结构检查与语义审核分开。
- **项目 05**：有身份与角色的本机 HTTP 应用，完整草稿／批准／提交链路、持久化、撤权、重放与独立下游恢复。
- **综合验收**：在参考项目上完成独立业务变化。只有运行参考服务，不能通过应用作品集门槛。

[项目目录](docs/projects/README.md) · [代码导读](docs/code-guide.md) · [验证范围](docs/verification.md) · [作品集清单](docs/templates/portfolio.md)

## 求职练习

[面试目录](docs/interview/README.md) 包括 60 道 Agent 讨论题、4 道工程编程参考题和 3 场模拟面试；另有 [通用 CS 诊断](docs/interview/cs-foundations.md)、学生提交练习、岗位官网样本和独立项目答辩。数据结构、网络、数据库与操作系统仍需按目标岗位补齐。

课程验收检查实际作品，不预测录用。参考代码的自动检查、真实模型实验和真人试学分别记录；当前没有真人学习效果数据，[试学材料](docs/teaching/pilot.md) 已提供。不要把小型合成评测写成线上指标或模型排名。

## 内容与维护

所有资料为合成教学数据，不需要姓名、学校、真实业务记录或私人聊天。凭据、数据库、个人作业和实验原始记录放在忽略的 `work/` 中；不要上传。模型只在显式选择真实模式时调用，费用或本地硬件消耗自行记录。

[原始资料](docs/resources.md) · [贡献约定](CONTRIBUTING.md) · [MIT License](LICENSE)。第三方资料只作必要引用和链接，保留其许可。仓库旧名为 `agent-from-zero-to-production`，包名和原有命令仍为 `agentlab`。
