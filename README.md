# AI Agent Learning Course · AI 智能体工程入门课程

Previously `agent-from-zero-to-production`. Package and command names remain unchanged (`agentlab`). 仓库原名 `agent-from-zero-to-production`，包名及现有命令保持不变。

**面向计算机专业学生的 Agent 工程学习路线：讲清原理，写出系统，用实验支撑面试回答。**

从一次工具调用开始，逐步完成知识检索、状态管理、审批、评估与故障恢复。中文讲解配合 English technical terms，适合自学、小组实践和求职前复习。

这里的“精通”意味着能够解释设计、复现结果、定位失败、作出取舍。读完文档只是起点，能独立完成迁移任务才算掌握。

[开始学习](docs/start-here.md) · [十周路线](docs/roadmap.md) · [实战项目](docs/projects/README.md) · [面试训练](docs/interview/README.md) · [术语速查](docs/glossary.md)

## 学完应该能做什么

- 判断一个需求适合普通程序、固定 Workflow，还是需要模型动态决策的 Agent。
- 实现有边界的工具循环，解释参数验证、权限检查、停止条件和重试策略。
- 拆解 RAG 的检索、证据、回答与拒答，区分检索命中和答案正确。
- 设计可恢复的业务流程，处理重复请求、过期审批和跨租户访问。
- 构造评估集、对照实验和失败分析，用可复核的项目证据回答面试追问。

## 先跑起来

需要 Python 3.11 或更新版本。先用 GitHub 的 **Code → Download ZIP** 下载解压，或复制仓库地址进行 `git clone`。进入含有 `pyproject.toml` 的仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m agentlab demo tools
python -m agentlab demo rag
python -m agentlab demo workflow
python -m agentlab evaluate --output work/evaluation.json
python -m unittest discover -s tests -v
```

Windows PowerShell 的激活命令为 `.venv\Scripts\Activate.ps1`；也可直接调用虚拟环境中的 Python。离线环境可跳过安装，设置 `PYTHONPATH=src` 后运行相同命令；PowerShell 使用 `$env:PYTHONPATH="src"`。

默认演示不需要 API Key、外部数据库或付费服务。工具循环使用 **ScriptedPlanner 测试替身**，知识问答使用词项检索和原文证据，业务流程由状态机控制。它们用于观察工程机制，离线结果不能证明大模型的规划能力。可选真实模型接入和费用边界见 [代码导读](docs/code-guide.md)。

## 完整知识地图

| 阶段 | 阅读 | 完成标志 |
| --- | --- | --- |
| 理解系统 | [01 Agent 与 Workflow](docs/chapters/01-foundations.md)、[02 LLM 基础](docs/chapters/02-llm-basics.md)、[03 提示词与结构化输出](docs/chapters/03-prompts-and-structured-output.md) | 能画出数据流并指出不确定性在哪里 |
| 连接环境 | [04 工具调用](docs/chapters/04-tool-use.md)、[05 检索与 RAG](docs/chapters/05-retrieval.md)、[06 记忆与上下文](docs/chapters/06-memory-and-context.md) | 能追踪一次请求从输入到证据和工具结果 |
| 组织任务 | [07 规划与工作流](docs/chapters/07-planning-and-workflows.md)、[08 多 Agent](docs/chapters/08-multi-agent.md)、[协议与框架](docs/ecosystem.md) | 能用同一任务比较两种架构 |
| 验证交付 | [09 评估](docs/chapters/09-evaluation.md)、[10 安全](docs/chapters/10-security.md)、[11 生产工程](docs/chapters/11-production.md) | 能展示失败样例、回归检查与上线门槛 |
| 深入研究 | [12 研究前沿](docs/chapters/12-research-frontier.md)、[论文阅读与复现](docs/research-lab.md) | 能提出可证伪假设并设计消融实验 |

各章包含概念解释、具体例子、工程取舍、练习和参考答案。可按顺序阅读，也可从项目遇到的问题返回对应章节。

## 三个实战入口

1. **工具执行器**：观察模型决策接口与实际执行器的分工，验证工具白名单、参数错误和循环预算。
2. **证据检索助手**：为问题找到合成资料中的证据，检查无答案问题、租户隔离和检索基线。
3. **审批工单流程**：把提案、授权和副作用分开，验证 SQLite 状态、重放和幂等冲突。

[项目教程与毕业项目](docs/projects/README.md) · [代码结构](docs/code-guide.md) · [验证范围](docs/verification.md) · [实验记录模板](docs/templates/experiment.md)

## 如何把学习变成求职证据

每个项目保留四样东西：可复现的命令、明确的指标口径、一组失败样例、一份设计取舍。项目答辩应能解释“为什么这样设计、什么情况下失效、如何证明改动有效”。

[岗位能力映射](docs/job-readiness.md) 帮助选择侧重点；[面试训练](docs/interview/README.md) 包含 60 道分层问答、4 道带可运行参考答案的编程题、3 场模拟面试，以及简历和项目答辩模板。后端、数据库、网络、算法和测试基础仍是准备工作的一部分。

## 内容与维护约定

- 所有项目资料均为合成教学数据，不需要个人信息、真实客户资料或聊天记录。
- 文档中的假设算例会明确标注；实测结果由命令生成，不把教学分数写成模型能力排名。
- 原理与具体版本分开。协议示例固定版本，框架用法迁移前应检查官方文档。
- [参考资料](docs/resources.md) 以原始论文、协议和官方文档为主；[贡献指南](CONTRIBUTING.md) 规定新章节和题目的质量要求。

代码与原创文档采用 [MIT License](LICENSE)。第三方论文和文档保留各自的版权与许可，仅链接和作必要引用。
