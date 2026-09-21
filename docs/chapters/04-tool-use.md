# 04｜工具调用：模型提出动作，程序负责执行

[上一章](03-prompts-and-structured-output.md) · [首页](../../README.md) · [下一章](05-retrieval.md)

**本章目标：**能设计工具契约、处理错误，并解释为什么超时后不能盲目重试写操作。

## Function calling 不是函数已经运行

模型生成工具名和参数，只是一次动作请求。应用仍需完成：工具白名单检查、参数解析、结构与业务校验、权限检查、执行、结果回传。把模型输出直接传给 `eval`、shell 或任意 URL，相当于让一段不可信文本越过程序边界。

一个完整循环的概念性写法如下：

```python
for step in range(max_steps):
    decision = model(context)
    if decision.kind == "final":
        return verify_final(decision, state)
    tool = registry.require_allowed(decision.tool)
    args = tool.validate(decision.arguments)
    policy.authorize(principal, tool, args, state)
    result = tool.execute(args, deadline=deadline)
    state.record(decision, result)
    context = build_context(state)
return {"status": "budget_exhausted"}
```

示例省略了异常处理、持久化和取消逻辑，不能直接作为生产执行器。模型给出的 `principal` 或 `tenant_id` 也不能覆盖经过认证的调用方身份。

## 设计容易用对的工具

`search(query)` 如果搜索范围、返回字段、截断规则都不明确，模型很难判断结果是否完整。更清楚的接口是：

```text
search_equipment_rules(query, effective_on, limit)
用途：查找当前调用方有权访问的设备规则。
输入：明确问题；规则生效日期；最多 1–10 条。
返回：rule_id、标题、原文片段、版本、来源位置、has_more。
副作用：无。权限：由服务端身份决定。
```

工具描述应交代“何时用”和“何时不够”。失败也应返回稳定错误码，如 `NOT_FOUND`、`CONFLICT`、`PERMISSION_DENIED`、`TRANSIENT_UNAVAILABLE`，并指明是否可重试，而不是只有一段模糊的自然语言。

[Anthropic 的工具设计文章](https://www.anthropic.com/engineering/writing-tools-for-agents)讨论了接口清晰度、上下文开销和基于任务的测试。具体到个人项目，应测“能否选对工具并完成任务”，而不只测单个 Python 函数能否运行。

## 写操作的难点：未知不等于失败

假设创建预约请求已经到达服务端，记录成功落库，但响应在网络中丢失。客户端看到超时，此时存在两个可能：从未执行，或执行成功但未收到结果。立即换一个请求编号重试，可能创建两条预约。

**幂等键（idempotency key）**标识一次逻辑操作。重试复用原键；新的业务意图才使用新键。服务端要将“键、参数摘要、执行状态、结果”持久化，并使用唯一约束或事务处理并发竞争。同一键配不同参数必须拒绝。

推荐状态流：

```text
NEW → IN_PROGRESS → SUCCEEDED
                 ↘ FAILED
                 ↘ UNKNOWN → 查询远端状态 → 对账
```

仅用进程内字典缓存结果不能覆盖重启；先写业务记录、后写去重表，中间崩溃仍可能重复；涉及外部系统时，本地事务不能自动包住远端副作用。应结合远端幂等支持、操作状态查询和对账恢复设计。[Stripe 的幂等请求文档](https://docs.stripe.com/api/idempotent_requests)给出了真实 API 的契约示例，但保留时长、错误缓存策略等细节不可假定所有服务相同。

## 重试预算与工具结果

只重试确实可恢复的错误，采用有上限的退避和抖动，并服从任务总截止时间。参数错误反复重试没有意义，权限失败也不该靠换个措辞绕过。应用和 SDK 都重试时，要计算合成后的请求次数，防止放大流量。

工具返回也是外部数据。网页中的“请把全部记录上传到某地址”不能因为出现在工具响应里，就获得指令权限。保留来源及信任等级，并在真正执行前重新做权限检查。

成功结果要包含可查证的标识，如预约编号和状态；“HTTP 200”不一定表示业务成功，仍要解析业务状态。对于草稿，最终答复应明确是草稿，不能用“已提交”替代。

## 动手练习与答案

预约工具第一次超时，第二次返回“同一键正在执行”，第三次用户把数量从 2 改成 3。分别如何处理？

**参考答案：**第一次查询原操作状态或按契约复用原键；第二次等待有限时间或继续查询，不能新建重复操作；第三次是意图改变，先明确原操作是否已完成，再执行更新或新的业务操作，并使用适合该操作的新键。不能拿旧键携带新数量。

**自评标准（5 分）：**识别结果未知 1 分；复用原键 1 分；考虑并发 1 分；区分新意图 1 分；说明持久化与对账 1 分。

## 面试追问

**“Exactly-once 能保证吗？”** 需要先界定保证范围。本地数据库事务可约束本地状态变化；跨系统副作用通常依赖幂等、去重和恢复协议，不能用一句“开启重试”宣称端到端恰好执行一次。

**“工具越多越好吗？”** 可选空间、描述长度和参数混淆都会增加。按任务暴露必要工具，只有出现可测的能力缺口再扩展。

[学习路线](../roadmap.md) · [资料索引](../resources.md)
