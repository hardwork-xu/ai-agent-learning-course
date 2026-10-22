# 四轮实验：从看懂到独立交付

[工作台入口](README.md) · [面试深挖](../interview/deep-dives.md) · [作品集要求](../projects/capstone.md)

不要求第一次读懂全部代码。每轮只沿一条数据流前进，先写预测，再运行，最后解释预测和结果的差异。建议两人结对：一人改代码，一人只按验收条件操作；交换后在新分支完成迁移。时间因基础而异，以下内容不承诺几天掌握或录用。

## 第一轮：一条任务从哪里来，到哪里去

先按入口跑通“直接检索”。阅读顺序是 `api.submit → Store.create → Store.claim → Engine.run_once → BaselinePlanner.next → Store.finish`。浏览器的任务 ID、事件中的 run ID 和数据库中的主键指同一条任务。

**先预测：** 同一个创建键提交两次，任务数是多少？键相同但问题不同会怎样？换 `partner` 的审核人能否看见？正常请求的 `tenant` 应由登录身份决定；另外构造带伪造 `tenant` 字段的恶意请求，验证接口会拒绝，而不是接受用户自报租户。

**实验：** 浏览器完成一条正常任务；运行 `python scripts/smoke_desk.py`；阅读 [API 测试](../../tests/test_desk_api.py) 中的角色与重放案例，然后只改变一项输入复现冲突。保存请求字段、状态码和事件阶段，报告删除访问凭据、机器路径与任何真实用户内容。

不知道怎样发出接口反例时，在仓库根目录新建 `work/check_first_request.py`，填入以下代码；先在另一个终端保持服务运行。该脚本只使用默认本机地址和自己的合成工作区凭据，关闭代理和重定向，不输出 token。它会创建并最终取消一条练习任务，不会批准或提交工单。

```python
from pathlib import Path
from uuid import uuid4
import httpx
from agentlab.service import load_config

BASE_URL = "http://127.0.0.1:8766"
DATA_DIR = Path("work/casedesk")
principals = load_config(DATA_DIR / "auth.json")["principals"]
tokens = {item["name"]: item["token"] for item in principals}
body = {"question": "银行卡退款获批后多久到账？", "idempotency_key": uuid4().hex}
with httpx.Client(base_url=BASE_URL, timeout=5, trust_env=False, follow_redirects=False) as client:
    def request(method, path, payload=None, identity="campus-requester"):
        return client.request(method, path, json=payload,
            headers={"Authorization": "Bearer " + tokens[identity]})

    first = request("POST", "/api/runs", body)
    assert first.status_code == 202, first.status_code
    run_id = first.json()["id"]
    try:
        replay = request("POST", "/api/runs", body)
        assert replay.status_code == 202 and replay.json()["id"] == run_id
        conflict = request("POST", "/api/runs", {**body, "question": "另一个问题"})
        assert conflict.status_code == 409, conflict.status_code
        forged = request("POST", "/api/runs", {**body, "tenant": "partner"})
        assert forged.status_code == 422, forged.status_code
        foreign = request("GET", "/api/runs/" + run_id, identity="partner-reviewer")
        assert foreign.status_code == 404, foreign.status_code
        print("replay=same_run conflict=409 forged_tenant=422 other_tenant=404")
    finally:
        cancelled = request("POST", "/api/runs/" + run_id + "/cancel")
        assert cancelled.status_code == 200, cancelled.status_code
```

运行 `python work/check_first_request.py`。先解释每项预期，再把一个身份或字段改成你自己选择的值，预测结果并核对服务端判断位置。断网、限流或其他状态与预期不符时，先记录现象；不要删掉失败断言。“本段参考脚本通过”仍不算独立实现。

最少记住下面几个接口契约，字段详情可对照 [api.py](../../src/agentlab/desk/api.py)：

| 接口 | 谁调用 | 输入与结果 |
| --- | --- | --- |
| `POST /api/runs` | 申请人 | `question`、`idempotency_key`、可选 `mode`；返回 202 和任务 ID |
| `GET /api/runs/{id}` | 所有者或同租户审核人 | 读取当前 `state/version/citations`；不能猜版本 |
| `POST .../reply` | 所有者 | `text/expected_version`；只有待补问任务能继续 |
| `POST .../review` | 同租户另一审核人 | `expected_version/answer/citations/decision`；每条引文需 `id/revision/quote` |
| `POST .../commit` | 所有者 | `expected_version/idempotency_key`；只有有效批准可产生工单 |

`review` 的引文应来自该任务实际观察的证据，不能从别的任务复制一个看起来存在的 ID。CaseDesk 关闭了通用 Swagger 页面，直接阅读上述契约和请求测试，不依赖 `/docs`。

**交付：** 用自己的话画出“请求快速返回 → 后台执行 → 人审 → 工单”四段；解释为什么 HTTP 202 不能被记成业务成功。能独立找到拒绝越权的服务端位置，才进入下一轮。

## 第二轮：资料变化之后，旧答案还能不能用

阅读 `KnowledgeStore.ingest/search/get/withdraw`，再看 `Engine._validate_citations`。用全新的文档 ID 导入两段原创合成政策：一段写适用条件，一段写例外。设计一个必须同时引用两段才能完整回答的问题。

**实验：** 在待审、批准、提交三个时点分别更新或撤回资料。用旧版本更新同一份文档，应出现版本冲突；撤回后重导相同正文，旧引用不能复活。把正常问题改成没有政策支持的问题，观察检索是否仍有命中。若在提交后才撤回，已经产生的工单是历史事实，撤回不能替代撤销业务；同键重放应返回既有回执。

**交付：** 给出两份原始 Markdown、分块/版本标识、问题、必需证据和实际检索列表。运行 `python -m agentlab.desk.evaluate --output work/retrieval.json` 查看内置合成语料对照；该命令不会评测刚刚导入工作台的新资料。用 `--database work/casedesk/knowledge.sqlite3 --tenant campus` 可以在自己的索引上运行内置题，但新业务还需要自己编写问题和证据标签。解释 top 5 为什么提高覆盖也增加噪声与注入曝光。先报告失败，再提出一次只改变一个因素的改进；没有运行向量模型就不要写“混合检索已完成”。

**独立迁移：** 把退款场景换成社团器材借用，含预约、归还、超时与损坏四类规则。至少一题缺少必要条件，一题出现新旧规则冲突。编号、正文、问题都重新设计，不能只替换名字。

## 第三轮：程序出错时仍能交接

阅读 `claim/save_decision/save_observation` 和 [恢复测试](../../tests/test_desk_engine.py)。区分任务版本（防止旧表单覆盖）、worker fence（防止旧执行者回写）、幂等键（重放同一操作）和引用 revision（绑定资料版本）。四者不能互相替代。

**实验：** 运行恢复测试后，自己构造一个“已保存决策但未保存工具结果”的崩溃点。重启时记录恢复事件，确认只读查询允许重做，工单副作用仍为零。另构造执行中取消；承认正在进行的同步模型调用可能继续到它的超时，但它不能再推动任务状态。

**交付：** 故障前后状态、取消时间线、旧 fence 被拒绝的证据、工单和 outbox 数量。取消时尚未返回的调用用量应先记未知，晚到的用量可以补记，但不得恢复被取消的任务。再按[运维说明](operations.md)做一次备份恢复，必须在新的目标目录验收，不能只证明生成了备份文件。

**面试追问：** 为什么不能把远程发送邮件放在可重放的只读工具里？如果对方接口不支持幂等键，未知回执如何交接？回答要给出查询、对账或人工处置步骤，而不是“再重试一次”。

## 第四轮：做一项能够答辩的独立变化

从下面选择一项作为自己的增量。第一次选题可直接做[租户配额迁移任务](tenant-capacity-lab.md)，范围限定为一项后端需求，附原版失败复现和验收表。其他选题也先写一页需求与验收，再实现。完成参考代码、通过已有公开测试只能算复现；新增变化才是作品的主要论据。

| 独立变化 | 最小实现 | 必须打破的错误实现 |
| --- | --- | --- |
| 公平排队 | 在总容量限制之外增加租户配额或公平调度 | 一个租户占满队列后，另一租户始终无法处理 |
| 可回答性判断 | 区分有关键词命中与证据覆盖问题条件 | 无依据问题靠公共词语得到高分就被回答 |
| 资料导入质量 | 支持一种真实复杂结构，如带表头的 Markdown 表格 | 表头和单元格拆开，答案引用失去条件 |
| 可核查的新只读工具 | 新业务对象、授权过滤、schema、超时和轨迹 | 模型参数可指定别人的租户，或错误结果被当成功 |

每项至少保留一个旧实现会失败的反例、一组改动后的结果与一项仍未解决的限制。模型相关变化要在同一语料、任务与预算下比较固定流程和真实模型；测试替身只用于控制故障。测量检索覆盖、任务质量、延迟、token 和人工修改量，缺哪项就留空，不能互相代替。

最后做 45 分钟答辩：5 分钟业务与边界，10 分钟数据和状态流，10 分钟复现故障，10 分钟现场改约束，10 分钟质疑实验。由同学挑一个你未提前看到的约束，例如“撤回后恢复原文”“同键不同参数”“审批恰好过期”，当场指出受影响代码和验证方法。


## 每轮完成后，怎样接到面试

不要做完实验后重新从头背 60 道题。先从[深挖场景](../interview/deep-dives.md)选与你已有证据对应的题，口头讲 90 秒，再打开真实材料展开：

| 手上的材料 | 先练哪几题 | 必须指给对方看的证据 |
| --- | --- | --- |
| 第一轮身份与重放反例 | D03、D07 | 请求字段、拒绝位置、同键重放和不同载荷冲突 |
| 第二轮文档与检索失败 | D01、D09 | 原文、分块、缺失证据、同条件前后结果 |
| 第三轮取消与恢复 | D04、D06、D11 | 时间线、版本/fence、晚到用量、恢复后效果数 |
| 第四轮租户配额或其他改动 | D05、D08、D12 | 个人 diff、旧版失败、新版结果、仍未实现的能力 |

问到没有实际运行的内容时，展示设计或说明未验证，不补造数字。完成一轮不等于达到某家公司的录用门槛；按[岗位映射](../job-readiness.md)另补通用 CS 与目标技术栈。
