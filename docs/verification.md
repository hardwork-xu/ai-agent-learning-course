# 参考实现的验证范围

[返回首页](../README.md) · [代码导读](code-guide.md) · [贡献检查](../CONTRIBUTING.md)

验证的对象是具体行为及其边界。测试通过说明已覆盖的条件成立，不代表模型可靠性、线上可用性或全面安全性已经得到证明。

## 可重复检查

```bash
python -m unittest discover -s tests -v
python scripts/check_exercises.py
python scripts/check_docs.py
python scripts/check_public_content.py
python -m agentlab demo tools
python -m agentlab demo rag
python -m agentlab demo workflow
python examples/failure_drills.py
python -m agentlab evaluate --output work/evaluation.json
```

核心测试覆盖工具白名单、严格参数验证、循环预算、有限重试、日志最小化、模型响应失败、证据返回、跨租户过滤、审批失效、事务回滚与并发幂等。四道编程题包含独立的行为测试。

2026-09-21 的本地验证使用 Python 3.12：30 项核心测试与 17 项编程题测试通过；三个 CLI 演示、故障练习通过；新生成的评测 JSON 与 [参考输出](../data/sample-evaluation.json) 一致。另在独立虚拟环境中安装 wheel，并从源码目录之外运行三条演示和评测，确认数据文件随包分发。

[CI 配置](../.github/workflows/check.yml) 对 Python 3.11、3.12、3.13 执行测试和文档检查。某个提交是否通过，应查看该提交对应的 GitHub Actions 结果。

## 每项证据的解释边界

| 证据 | 支持的结论 | 尚未证明的内容 |
| --- | --- | --- |
| ScriptedPlanner 测试 | 执行器处理指定提案的行为 | 模型能否产生正确提案 |
| HTTP stub 测试 | 模型适配器构造请求、检查响应及失败处理 | 当前账户、模型与真实服务调用是否可用 |
| 8 条公开检索测试题 | 固定合成数据上的阈值取舍 | 未见任务上的泛化质量 |
| SQLite 并发与回滚测试 | 同数据库内去重和副作用原子性 | 跨服务恰好一次、真实用户认证 |
| 文档与公开内容扫描 | 发现已配置模式和本地链接错误 | 全面隐私审查或所有外链永久有效 |

真实付费模型调用没有纳入默认验证，也没有提交真实用户输入。模型质量、API 用量、服务时延和生产负载需要独立实验，不能用离线测试的通过数替代。
