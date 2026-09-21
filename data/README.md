# 数据与评测边界

所有资料、组织、政策、工单和身份都为手工编写的虚构样例，不包含个人信息或真实业务规则。

数据随 Python 包分发，只有一份来源，避免“评测读一份、演示读另一份”：

| 数据 | 位置 | 内容 |
| --- | --- | --- |
| 语料 | [corpus.json](../src/agentlab/fixtures/corpus.json) | 7 条文档，包含两个虚构业务租户和一个攻击测试租户 |
| 查询与标签 | [evaluation.json](../src/agentlab/fixtures/evaluation.json) | 12 个手工样例，dev 4 个、test 8 个 |
| 离线参考输出 | [sample-evaluation.json](sample-evaluation.json) | 当前实现的 baseline/candidate、逐例结果、分子/分母和配置 |

`relevant_ids: []` 表示当前租户没有足以回答的问题资料。test 文件完全公开，不能当成未见过的秘密测试集。修改语料、分词或阈值后应重新生成报告；不要手改指标。

```bash
python -m agentlab evaluate --output work/evaluation.json
```

示例阈值 `0.6` 会减少无关回答，也会漏掉表达不同但实际可答的问题。报告特意保留这种权衡。阈值 `0` 的 baseline 仍要求至少一个词项相交，空查询和无交集不会强行返回文档。

参考输出只证明这个微型词项检索器在这些样例上的行为，不证明大模型、向量检索、生成答案、提示注入防御或求职能力。`citation_precision_answered` 只按文档 ID 标签计算；文档正确也不自动意味着答案中的每条陈述都有依据。

扩充数据时，先按“可答、不可答、近似词但错意图、跨租户、恶意资料、过时资料”分层，再增加人工标签；独立保留未用于调参的测试集。真实数据需经过合法取得、授权和去标识处理，不能直接提交聊天记录或工单导出。
