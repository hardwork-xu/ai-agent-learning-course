# 独立迁移：给共享工作台增加租户配额

[四轮学习实验](learning-lab.md) · [作品证据清单](student-value.md) · [D05 容量深挖](../interview/deep-dives.md)

这是一个**尚未在参考工程实现、需要你独立完成**的扩展任务。目标是做一项可解释、可测试的后端变化，不需要先训练模型或接入真实企业数据。先完成工作台正常流程，并能读懂 `Store.create/review/cancel` 的事务。

## 需求先于代码

两个社团共享知识工作台。每个社团是一个租户，各自有申请人与审核人。当前只限制整个服务的活动任务数，一个社团可以占满全部容量，另一个社团连任务都无法创建。

新增配置 `tenant_capacity`，表示同一租户的活动任务上限；它由服务启动配置传入，不能由请求正文、模型参数或用户自称角色指定。全局 `capacity` 仍然生效。本轮只做**入场配额**，不实现轮询调度、优先级、弹性扩容或“每个租户必定同速”的公平性承诺。

约定如下，先写进自己的需求文件：

- 活动状态为 `queued/running/needs_input/awaiting_review/approved`；其他状态不占配额。
- 全局上限 4、租户上限 2 时，campus 的第三条不同任务应被拒绝，partner 仍可接收两条。
- 多名申请人共用租户配额，不能通过更换 actor 获得新额度。
- 全局满载返回现有 `capacity_exceeded`；仅租户满载返回 `tenant_capacity_exceeded`。两者都为 HTTP 429；若同时触发，优先全局错误。
- 同键、同载荷的创建重放返回原任务，不消耗新配额；同键不同载荷仍为 409。
- 已拒绝任务重新批准会占用活动配额，必须在批准事务内重新检查。配额不足时，旧状态与版本不变。
- `tenant_capacity=None` 保持原有全局容量行为；正整数 1..1024 有效，布尔值、0、负数和非整数无效。租户上限可以大于全局上限，最终仍受全局上限限制。

## 先证明原版的问题

保存当前提交编号和工作区状态；在自己的新分支开展，保留原版结果。把下面程序保存为 `work/capacity_before.py` 并运行 `python work/capacity_before.py`。它只使用临时数据库，不需要启动 Web 服务：

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from agentlab.desk.store import DeskError, Store

with TemporaryDirectory() as directory:
    store = Store(Path(directory) / "runs.db", capacity=4)
    for index in range(4):
        store.create("campus", "requester", "合成借用咨询", f"campus-{index}")
    try:
        store.create("partner", "requester", "另一社团的合成咨询", "partner-0")
    except DeskError as error:
        assert error.code == "capacity_exceeded"
        print("before: campus占用4条，partner被全局上限拒绝")
    else:
        raise AssertionError("原版现象发生变化，先检查版本与配置")
```

这段程序预期退出 0，表示成功复现缺少租户配额的现象；不是新需求已经满足。不要把四条任务的数量当作吞吐测量。

## 把变化限定在一条清楚的调用链

先实现 `Store(..., tenant_capacity=None)`，再接到 `create_app(..., tenant_capacity=None)` 与 `serve --tenant-capacity`。后三个接口是本练习要求新增的接口，原版尚不接受该参数。不开启参数时，已有用法和测试应保持兼容。

阅读顺序：

1. [Store.create](../../src/agentlab/desk/store.py)：先做幂等回读，再检查容量，最后创建任务。计数和写入应保持在同一个 `BEGIN IMMEDIATE` 事务内。
2. 同文件的 `Store.review`：`rejected → approved` 也会重新进入活动集合；只改创建入口会漏掉它。
3. [create_app](../../src/agentlab/desk/api.py) 与 [CLI](../../src/agentlab/desk/__main__.py)：从受信配置构造 Store；API 的任务正文仍不允许 `tenant` 或 `tenant_capacity`。
4. 页面错误提示：为新的拒绝原因提供能理解的文案，说明处理已有任务或稍后再试；不建议用户换身份绕过配额。

可以复用一个集中定义的活动状态集合与容量检查，但自行决定代码组织。禁止先读计数、离开事务再写任务；单线程测试可能通过，并发时仍会超额。暂不需要 Redis、消息队列或第二个服务。

## 新测试必须覆盖什么

在自己分支新建 `tests/test_student_capacity.py`。下面是验收表，不是已经存在的检查器；不要直接运行 `scripts/grade.py` 冒充这个扩展的评分。

| 场景 | 操作 | 预期与要保存的证据 |
| --- | --- | --- |
| 保留全局界限 | 全局 3、租户 2；campus 两条，partner 一条后再创建 | 全局拒绝，活动总数仍为 3 |
| 租户不能占满其他人的位置 | 全局 4、租户 2；campus 试建三条，再由 partner 建两条 | campus 第三条 429；partner 两条成功；总数 4 |
| 换申请人不能绕过 | campus 的两个 actor 各建一条，再让第三个 actor 创建 | 同租户拒绝，总数仍为 2 |
| 满载时幂等仍可用 | 在租户已满时重放第一条请求，再改同键载荷 | 同载荷返回同 ID；不同载荷 409；无新记录 |
| 释放与重新入场 | 取消一条后新建；拒绝一条、填满后尝试重新批准 | 取消释放配额；重新批准被拒，状态和 version 不变 |
| 并发不会超额 | 租户配额 2，用至少 8 个真实线程／独立连接同时创建不同键 | 恰好 2 条成功，其余为租户容量拒绝；不只统计返回值，还查数据库 |
| 请求字段不能成为配置 | 带 `tenant_capacity` 或伪造 `tenant` 的 POST | 请求被拒；服务端配额与租户不变 |
| 配置与兼容 | 不传新参数、传 None、合法整数以及 True/0/负数/字符串 | 默认行为兼容；非法值明确拒绝，不产生半初始化任务 |

用原版参考测试学会如何建立临时库与并发屏障，再自己写断言。并发结果和异常代码要分类记录，不能把任何异常都当作“正确拒绝”。初版可能先满足顺序场景，再修复并发或重新批准反例；保留这一修复过程。

```bash
python -m unittest discover -s tests -p 'test_student_capacity.py' -v
python -m unittest discover -s tests -p 'test_desk_*.py' -v
python scripts/check_docs.py
python scripts/check_public_content.py
```

第一条在你创建测试前可能显示 `Ran 0 tests`，这不算通过。API 相关测试若显示 skipped，先按[启动说明](README.md)安装应用依赖；不能把跳过等同于已经测试接口。

实现 CLI 后，在**新的合成工作区**验证 `--capacity 4 --tenant-capacity 2`，避免旧数据库里的待审任务改变计数。记录实际解析的配置、每租户活动数、拒绝原因和原有四条边界：跨租户读取、过期批准、重复提交、资料撤回仍须有效。

## 交付与答辩

在自己的分支提供代码、新测试和一份简短 `docs/my-capacity-change.md`；原始数据库和日志留在忽略的 `work/`。说明至少包含：

1. 参考提交与需求：为什么总容量不能代替租户配额。
2. 个人改动：改了哪些事务入口，为什么 API 不允许传配额。
3. 结果：上表逐项运行与否、成功/拒绝计数、旧版反例、仍失败的条目。
4. 限制：没有调度公平、没有生产压测、没有真实使用收益；不要填写未测的延迟或吞吐。
5. 帮助记录：读过哪些参考、接受过什么帮助，以及自己如何验证。

请同学临时把约束改为“同租户有两个 actor”或“拒绝后重新批准时容量已满”，现场定位影响并修改一条测试。一个人在完成时先作自查；找到参与者后再补独立答辩记录，不虚构同伴验收。

这项变化可以证明应用事务、并发和权限配置的工程能力；它本身不证明模型规划、训练能力或真实商业收益。面试时结合 D03、D05、D06、D08 解释，再按目标 JD 补足其他能力。
