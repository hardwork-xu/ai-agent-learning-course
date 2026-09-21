# 参考资料与阅读顺序

[返回首页](../README.md)

优先读与正在解决的问题相关的原始资料。下面的入口在 2026-09-21 核验；论文按原版本理解，持续更新的文档在实际接入前重新检查。资源选择围绕教学主题，不作为流行度排名。

## 第一轮：建立概念

| 资料 | 带着什么问题阅读 | 对应内容 |
| --- | --- | --- |
| [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) | 固定流程与动态决策如何区分？ | 第 01、07 章 |
| [Attention Is All You Need](https://arxiv.org/abs/1706.03762) | 自注意力怎样组合表示？ | 第 02 章 |
| [ReAct](https://arxiv.org/abs/2210.03629) | 环境反馈如何进入下一轮决策？ | 第 04、07 章 |
| [Retrieval-Augmented Generation](https://arxiv.org/abs/2005.11401) | 参数知识与外部检索如何结合？ | 第 05 章 |

## 第二轮：实现契约

| 资料 | 用途 |
| --- | --- |
| [JSON Schema 文档](https://json-schema.org/understanding-json-schema/) | 理解类型、必填项和约束，与业务验证分开 |
| [Claude Messages API](https://platform.claude.com/docs/en/api/messages/create) | 可选真实模型适配器的请求与工具返回契约 |
| [MCP 2025-11-25 规范](https://modelcontextprotocol.io/specification/2025-11-25) | 本仓库协议教学的固定版本 |
| [MCP 工具规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) | 能力发现、调用和错误处理 |
| [MCP 授权](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) | 理解传输与授权边界 |
| [A2A 核心概念](https://a2a-protocol.org/latest/topics/key-concepts/) | 理解跨 Agent 任务与产物 |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | 将状态机知识映射到编排运行时 |
| [Google ADK](https://adk.dev/) | 对照另一种框架的组件边界 |

框架没有唯一学习顺序。先完成原生代码项目，再选一个框架复刻同一任务，比较状态恢复、调试和依赖复杂度。

## 第三轮：评估与研究

| 资料 | 值得追问的地方 |
| --- | --- |
| [Reflexion](https://arxiv.org/abs/2303.11366) | 文本反馈与参数学习有何区别？ |
| [SWE-bench](https://arxiv.org/abs/2310.06770) | 测试通过与真实任务满足的边界在哪里？ |
| [τ-bench](https://arxiv.org/abs/2406.12045) | 交互、工具和业务规则如何共同判定成功？ |
| [Voyager](https://arxiv.org/abs/2305.16291) | 技能积累与环境设计怎样影响探索？ |

章节还包含与具体结论直接相关的数据库、可靠性和安全资料，应在遇到对应工程问题时阅读。

## 如何判断一条资料是否值得收录

能否定位作者、版本和原始证据？是否说明任务、预算与局限？代码与数据是否能够按许可复现？结论有没有合理基线？读完能否设计一个更好的实验或实现？

来源不明的“高频面试真题”、没有口径的排行榜截图和无法运行的示例不适合作为核心教材。技术博客可以提供经验，但经验应与论文结论、协议要求和本项目的教学建议分开。
