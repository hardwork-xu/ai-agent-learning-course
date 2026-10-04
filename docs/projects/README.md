# 实战：从执行边界到完整应用

[返回首页](../../README.md) · [面试题库](../interview/README.md)

基础实验与完整服务共用 Python 标准库包 `agentlab`，默认无需模型。先观察单个机制，再完成中文检索与有身份、审批和恢复的应用，最后提交自己的改动。

| 项目 | 已实现的内容 | 建议学习产物 |
| --- | --- | --- |
| [01 工具执行器](01-tool-agent.md) | 脚本化 planner、参数校验、只读边界、有限步骤和重试 | 一条成功轨迹与一条失败轨迹 |
| [02 有证据的检索](02-evidence-rag.md) | 词项检索、租户过滤、原文提取、拒答与离线评测 | 阈值对比及逐题错误分析 |
| [03 可恢复的审批流程](03-durable-workflow.md) | SQLite 状态、审批绑定、事务幂等、连接重启恢复 | 状态图与故障时间线 |
| [04 中文 RAG 与模型草稿](04-chinese-rag.md) | 中文 BM25、版本与租户过滤、引用检查、逐题评测、可选本地模型 | 检索对照、错误切片、生成主张审核 |
| [05 完整应用服务](05-service.md) | 本机 HTTP、角色与资源权限、草稿审批、outbox 和独立下游恢复 | 身份与状态测试、跨进程操作、故障恢复 |
| [综合项目验收](capstone.md) | 参考服务之上的独立变化、模型实验与答辩门槛 | 自己的实现、实测结果、迁移与交付记录 |

资料配套：[评测数据集设计](evaluation-dataset.md) · [三个 ADR 模板与示例](architecture-decisions.md)

## 准备环境

需要 Python 3.11 或更高版本。在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m agentlab demo tools
python -m agentlab demo rag
python -m agentlab demo workflow
python -m unittest discover -s tests -v
```

Windows PowerShell 的激活命令是 `.venv\Scripts\Activate.ps1`。包没有第三方运行时依赖；首次安装构建工具可能访问包索引。若只想离线执行源代码，在仓库根目录设置 `PYTHONPATH=src` 后运行；PowerShell 使用 `$env:PYTHONPATH="src"`，POSIX shell 使用 `export PYTHONPATH=src`。

## 能证明什么

基础实验 01–03 保持小范围：脚本化 planner 不模拟模型能力；英文检索没有生成器；直接调用工作流时主体仍由可信代码保证。项目 04–05 才增加中文检索、可选模型和真实 HTTP 认证边界；其本地 bearer 角色不等同于现实身份核验。自动 smoke 的审核人是测试角色。

建议按 01→02→03→04→05 顺序完成，并穿插 [学生练习](../practice.md)。先做 [先修诊断](../prerequisites.md)，再按 [综合验收](capstone.md) 留存独立成果；只运行参考项目不算完成应用作品集。
