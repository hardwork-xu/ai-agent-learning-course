# 实战：从执行边界到服务设计

[返回首页](../../README.md) · [面试题库](../interview/README.md)

三个实验共用 Python 标准库包 `agentlab`，默认离线运行。先观察一个小系统怎样失败、怎样停止、怎样恢复，再决定是否接入真实模型和服务。

| 项目 | 已实现的内容 | 建议学习产物 |
| --- | --- | --- |
| [01 工具执行器](01-tool-agent.md) | 脚本化 planner、参数校验、只读边界、有限步骤和重试 | 一条成功轨迹与一条失败轨迹 |
| [02 有证据的检索](02-evidence-rag.md) | 词项检索、租户过滤、原文提取、拒答与离线评测 | 阈值对比及逐题错误分析 |
| [03 可恢复的审批流程](03-durable-workflow.md) | SQLite 状态、审批绑定、事务幂等、连接重启恢复 | 状态图与故障时间线 |
| [综合项目：知识与工单服务](capstone.md) | **设计规格，尚未实现 HTTP 服务及部署** | API、威胁模型、评测与部署证据 |

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

脚本化 planner 验证执行器收到某种决策时的行为，不模拟模型能力；词项检索实验不包含 embedding 与生成；审批演示的调用者是可信本地代码，尚无真实用户认证。每个教程都把现有能力与扩展任务分开。

建议按 01→02→03 顺序完成。会 Python 的读者可直接开始；如果不能解释字典、异常、函数和事务，先补齐相应基础，再对照代码逐段运行。计时不作为完成标准，验收条件才是。
