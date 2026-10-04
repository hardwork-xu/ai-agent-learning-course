# 验证记录与证据边界

[返回首页](../README.md) · [综合验收](projects/capstone.md) · [贡献检查](../CONTRIBUTING.md)

验证对象必须明确：参考实现是否符合契约、学生提交是否通过行为检查、真实模型是否表现良好、初学者是否学会，是四个不同问题。

## 可重复检查

在仓库根目录、已安装课程包的环境执行：

```bash
python -m unittest discover -s tests -v
python scripts/check_exercises.py
python scripts/grade.py --submission exercises/reference.py
python scripts/check_docs.py
python scripts/check_public_content.py
python scripts/smoke_service.py
python -m agentlab demo tools
python -m agentlab demo rag
python -m agentlab demo workflow
python examples/failure_drills.py
python -m agentlab evaluate --output work/evaluation.json
python -m agentlab.rag_eval --output work/zh-evaluation.json
```

单独验证起始练习尚未完成：

```bash
python scripts/grade.py --submission exercises/starter.py
```

这条命令**应返回退出码 1，0/20 检查通过**。不能为了让整批命令变绿而把它改成通过。维护者测试还会替换成故意错误的实现，检查评分器确实拒绝布尔数量、非法编号、重复 JSON 键、幂等冲突与跳过测试。

## 2026-10-05：0.2.0 本地验证

本轮在 Python 3.12 环境执行，使用当前源码：

| 对象 | 观察结果 | 解释 |
| --- | --- | --- |
| 源码测试 | 110 项通过 | 包括工具、模型协议、RAG、评分器、真实 HTTP、事务、备份与恢复，以及旧编码终端输出 |
| 原面试参考解 | 17 项测试通过 | 验证四份提供的参考答案，不是学习者成绩 |
| 新练习参考解 | 20/20 契约检查通过；空起始文件 0/20 | 验证评分器的正反例；公开测试不证明独立性 |
| 完整应用 smoke | 真实本机 HTTP；一张本地工单；故障恢复后一个下游效果 | 审核身份为测试模拟；没有实际外部工单系统 |
| 独立边界复核 | 租户政策分离、冲突资料和待语义审核草稿不能创建申请 | 只支持已执行的这些合成案例，不是全面安全证明 |
| 备份／恢复 | 只读源、新文件恢复、已有目标保留、损坏副本清理、双库重放测试通过 | 两份备份不是跨库原子快照，必须停写并停止投递后协调操作 |
| 中文公开回归 | 候选 29/30，覆盖 22/30；保留一条引用真实但不切题的错误 | 12 dev / 30 test 全部公开，编写实现时见过 test，非独立泛化测量 |
| wheel 安装 | 在仓库外新虚拟环境安装 0.2.0，未设置源码 PYTHONPATH；三个 demo、中文评测、HTTP 查询通过 | 四份 fixture 随包分发；安装包源码与对应工作区逐字节一致 |
| Windows 兼容修复 | 实际 CI 暴露的参考练习 SQLite 连接未关闭、RAG 演示输出编码问题已修复 | 另用初始 cp1252 编码的子进程检查中文结果、失败提示和重定向输出；库导入不改变输出流 |
| 文档与公开内容 | 本地链接、编码、代码围栏、配置的隐私模式检查通过；人工复核待提交文件 | 模式扫描不等于全面隐私或外链永久有效性证明 |

中文结果的配置、代码／数据摘要与失败题见 [保存的实测摘要](../data/zh-example-summary.json)。工具质量反例见 [test_quality.py](../tests/test_quality.py)：结束但答案错误、虚构副作用、缺少观察、计算错参数都不会通过固定任务评分。

## 持续集成

[CI 配置](../.github/workflows/check.yml) 对 Ubuntu 的 Python 3.11/3.12/3.13 和 Windows 的 Python 3.12 执行同一组检查。是否在某个提交通过，以该提交实际的 GitHub Actions 记录为准；本地 Python 3.12 通过不冒充其他平台运行结果。

模型测试使用 HTTP 替身和本地临时服务器，默认 CI 不读取模型凭据、不拉取模型、不产生付费调用。公开内容检查忽略 `work/`，发布前还应检查 Git 待提交列表，避免强行加入本地运行资料。

## 仍未建立的证据

- **真实模型质量：** 没有本轮真实 Ollama 或 Claude 运行成绩。调用入口、结构约束、失败用量保留与预算由协议测试验证；模型标签是否可用和实际效果需独立运行。没有用生成器替身的结果补齐 G6。
- **真人教学效果：** 已提供 [试学材料](teaching/pilot.md)，尚无真实参加者、完成时间、帮助程度或迁移结果。代码测试不能替代此项。
- **公网与规模：** 本机单进程标准库 HTTP 服务不是互联网生产部署；未做生产负载、SSO、TLS、多副本或真实第三方下游验证。
- **生成语义：** 原文匹配只检查引用完整性。模型改写保留为待审核草稿，不把引用存在当成语义蕴含或问题相关性证明。

课程与作品集记录应分别标注“参考复现、独立完成、真实模型实测、真人试学”。未知结果保持未知，设计目标不填写成测量结果。
