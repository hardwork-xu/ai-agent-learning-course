# 项目五：把证据问答接成可恢复的工单服务

**目标岗位：Agent 应用开发、后端开发。预计 8–12 小时，另留独立改造与答辩时间。** 时间仅用于排期，未经过学生试学验证。先完成 [中文证据问答](04-chinese-rag.md)、[持久化审批](03-durable-workflow.md)，并能解释 HTTP 状态码和 SQL 事务。

完成后应能从两个终端演示：中文问题 → 引用证据 → 工单草稿 → 审核人确认具体版本 → 创建本地工单 → 向另一个数据库投递 → 故障恢复。所有资料、角色和工单都是虚构的。不要把真实客服记录、账号、客户信息或密钥放进示例。

这个服务使用 Python 标准库和 SQLite，只监听本机 `127.0.0.1`。这是可以运行、重启和调试的教学应用；单进程 HTTP 服务、文件凭据和本地 SQLite 不能替代生产网关、身份平台、监控和容量设计。

## 1. 先看完整过程，再逐步操作

在仓库根目录按 [安装步骤](../../README.md) 安装后运行：

```bash
python scripts/smoke_service.py
```

输出应包含 `status: passed`、`http: real_loopback`、`downstream_effects: 1` 和 `outbox_recovered: true`。脚本用临时目录完成真实 HTTP 请求、中文检索、两角色流程和投递恢复，结束后清理临时数据。这里的审核人是**测试程序模拟角色**，既不代表真人确认，也不代表学生通过考核。

读完输出先回答：为什么已经创建本地工单，还需要等待投递？为什么收到同一个请求两次，不能建两个工单？带着这两个问题操作下面的持久化版本。

## 2. 在本机启动一个持久化服务

```bash
python -m agentlab.service init --config work/service-auth.json
python -m agentlab.service serve --config work/service-auth.json --database work/service.sqlite3 --port 8765
```

第一个命令生成随机凭据，文件已被 Git 忽略，终端不会打印密钥。文件已存在时初始化失败，防止意外更换身份；不要为了重跑直接覆盖正在使用的文件。POSIX 系统要求文件权限 `0600`，凭据配置只能由当前系统用户读写。Windows 的访问控制另需设置，`0600` 不是 Windows ACL 验证。

第二个命令持续运行；下面的命令在另一个终端输入。使用 `Ctrl+C` 关闭服务。重新执行 `serve` 会使用原数据库恢复状态。

配置里有两个租户 `campus`、`partner`，各有 `requester` 和 `reviewer`。角色名是演示标签。真实系统不能把全部角色凭据交给同一个使用者；这里保存在同一受限文件中，是为了单人练习。

## 3. 提问并把证据变成草稿

```bash
python examples/service_client.py query --question '退款审核期限是多少天？'
```

检查 `abstained: false`、`verification.publishable: true`、引用的 `id`、`revision` 和 `quote`。记录返回的 `query_id`。回答说的是审核期限，不能把它改述为退款到账期限。

下面的尖括号是需要替换的值，不要原样粘贴。`request-001` 是本次练习的业务请求编号，新的业务请求要换新编号。

```bash
python examples/service_client.py create --query-id '<刚才的 query_id>' --request-id request-001
python examples/service_client.py show --request-id request-001
```

`create` 根据服务端保存的证据包生成 `payload.body`。调用方不能传入 `tenant`、`state`、`role` 或 `approval_token`。无证据或需要人工核验的生成草稿不能沿这条自动证据路径创建工单：会返回 `422 evidence_required`。

此时 `state` 应为 `pending`。记录 `version` 和 `digest`。摘要是对规范化后的**完整业务负载**求 SHA-256，不只是对问题或标题求摘要。

## 4. 审核的是具体内容和版本

先切换到审核人读工单：

```bash
python examples/service_client.py --as campus-reviewer show --request-id request-001
```

实际检查 `payload` 和引用后，用此次读取的值批准：

```bash
python examples/service_client.py --as campus-reviewer approve --request-id request-001 --expected-version 1 --expected-digest '<审核时看到的 digest>' --ttl-seconds 300
```

请求方自行调用 `approve` 会得到 `403 role_forbidden`。旧版本或旧摘要得到 `409 stale_review`。批准令牌只保存在服务器数据库中，不通过 HTTP 返回；“我已经批准”这类正文没有审批权限。

请求方再提交审核过的同一版本：

```bash
python examples/service_client.py commit --request-id request-001 --expected-version 1 --expected-digest '<同一 digest>' --idempotency-key submit-001
```

预期包含 `state: created`、`ticket_id`、`delivery_state: pending`。这里表示**本地工单已创建**，还没有声称下游接收成功。审核已过期则要重新读取、审核，再提交；不能延长旧令牌的有效期。

重复运行同一提交命令，应返回同一个 `ticket_id`。用另一个业务请求复用 `submit-001` 会冲突；已经提交的请求改用新幂等键也会拒绝。鉴权依然每次执行，旧凭据被撤销后不能靠幂等重放读取结果。

## 5. 亲手制造“对方收到了，自己还不知道”的故障

本地提交在一个 SQL 事务里写入工单、幂等结果和 outbox 事件。投递进程将事件送到另一个 SQLite 数据库，模拟独立的下游。

```bash
python -m agentlab.service recover --config work/service-auth.json --database work/service.sqlite3 --downstream work/downstream.sqlite3 --crash-after-effect
```

此命令故意在下游提交成功、上游保存回执之前失败，退出码为 `1`，输出 `operation_failed`。这是假故障开关，不能用于正常投递。此时下游已有一条记录，上游 outbox 仍为 `pending`。

移除故障开关重试：

```bash
python -m agentlab.service recover --config work/service-auth.json --database work/service.sqlite3 --downstream work/downstream.sqlite3
python -m agentlab.service recover --config work/service-auth.json --database work/service.sqlite3 --downstream work/downstream.sqlite3
```

对刚才一个请求，第一次应报告 `delivered: 1`，第二次 `delivered: 0`。再次提交原请求可看到 `delivery_state: delivered`。下游使用事件 ID 作为唯一键，并比较租户和负载摘要，因此重送不会新增效果。

这叫 **at-least-once delivery + idempotent consumer**，不能推广成所有第三方 API 都支持“恰好一次”。真正的外部接口如果没有幂等键、结果查询或业务唯一约束，就可能只能人工核对不确定结果。

## 6. 路由和权限

所有 `/v1/*` 路由需要 `Authorization: Bearer ...`。客户端脚本从受限配置读取凭据，不需要复制密钥到命令行历史。

| 请求 | JSON 字段 | 权限与结果 |
|---|---|---|
| `GET /health` | 无 | 无需登录，仅报告进程可响应；不证明模型或存储健康 |
| `POST /v1/query` | `question` | 请求方；按凭据租户检索，保存证据包 |
| `POST /v1/requests` | `query_id, request_id, subject, priority` | 请求方；只能使用自己、同租户的查询 |
| `GET /v1/requests/{id}` | 无 | 请求方只读自己的；审核人可读同租户 |
| `PATCH /v1/requests/{id}` | `payload, expected_version` | 请求方修改自己的未提交草稿，版本加一并撤销旧审批 |
| `POST /v1/requests/{id}/approve` | `expected_version, expected_digest`；可选 `ttl_seconds` | 同租户审核人；有效期 1–3600 秒 |
| `POST /v1/requests/{id}/commit` | `expected_version, expected_digest, idempotency_key` | 原请求方；必须有有效审批，或者是同一已提交操作的重放 |
| `GET /v1/metrics` | 无 | 请求方看自己的统计；审核人看本租户统计 |

`payload` 必须恰好包含 `subject`、`body`、`priority`。修改接口是经过身份验证的人为编辑，不是模型工具；修改后审核人需要重新读完整负载。字段级证据绑定或编辑差异展示属于后续扩展。

身份只来自配置，不接受客户端 JSON 中的租户或角色。跨租户和其他请求方访问不存在与无权限资源时都返回 `404`，避免透露资源是否存在。此政策允许审核人看到本租户所有工单；若业务需要分组审核，还要加组级策略。

## 7. 定位故障和紧急停写

```bash
python examples/service_client.py metrics
python examples/service_client.py --as campus-reviewer metrics
python -m unittest discover -s tests -p 'test_service.py' -v
```

每次请求返回随机 `trace_id`。数据库 `service_events` 只保存身份标签、归类后的路由、HTTP 状态、错误码和耗时，不保存 Bearer 值、问题或回答。业务证据包另存于 `service_queries`，因此数据库仍然必须当作业务数据保护；“日志不含正文”不等于“数据库不含正文”。演示只允许使用合成数据。

| 症状 | 首先检查 |
|---|---|
| `401 unauthorized` | 凭据是否被移除，客户端是否选择了正确配置 |
| `403 role_forbidden` | 是否用请求方身份操作审核接口 |
| `409 stale_review` | 是否在修改草稿后继续使用旧版本或摘要 |
| `409 approval_required_or_stale` | 是否尚未批准、批准已过期、修改后未重审 |
| `409 idempotency_conflict` | 是否把同一个操作键用于另一业务请求 |
| `422 evidence_required` | 检索是否拒答，生成结果是否仍需人工核验 |
| `503 retrieval_unavailable` | 先在 RAG 项目独立复现；检查本地模型进程、模型名与响应格式 |
| `503 auth_config_unavailable` | 配置 JSON、重复身份、文件所有者或权限不符合要求 |
| `503 storage_unavailable` | 路径权限、数据库锁或磁盘问题；确认是否已提交后用原键重试 |

编辑 `work/service-auth.json`，把 `write_enabled` 设为 `false`，服务无需重启即拒绝下一次写请求；读取工单和指标仍可用，CLI 投递也拒绝执行。此开关不会撤销已成功提交的工单，也不取消已经开始的请求。编辑配置时推荐先写临时文件、保持 `0600`、再原子替换；错误或部分写入会使鉴权暂时失败。

移除某个 principal 或更换它的 token，可撤销旧凭据。每个请求重新读取配置。不要把配置文件传给其他同学，不要上传截图中的凭据，不要提交整个 `work/`。

回滚代码前先停写并停止投递进程，保存数据库备份和对应版本；没有数据库降级迁移时不能假设旧代码可读取新状态。不要删除已经投递的记录来“重跑演示”。如果只是开始一次全新练习，使用新的数据库和请求编号。

## 8. 阅读代码的顺序

1. [客户端](../../examples/service_client.py)：怎样组装一个 HTTP 请求，怎样明确区分失败响应。
2. [服务](../../src/agentlab/service.py) 的 `Application.dispatch`：从角色、查询所有权走到业务动作；不要跳过边界校验。
3. [工作流](../../src/agentlab/workflow.py) 的 `approve` 和 `execute`：为什么要在事务内再检查版本，为什么幂等重放能晚于审批有效期。
4. `deliver_outbox`：两次独立提交中间为什么会出现未知结果，为什么下游需要自己的去重表。
5. [HTTP 测试](../../tests/test_service.py)：把每个预期失败与具体防线对应起来，而不只看测试数量。

服务拒绝非回环地址、重复 JSON 键、未知字段、超过 16 KiB 的请求体、跨站 Host 和分块请求体；套接字读取超时为 5 秒。不开放 CORS。它没有 TLS、SSO、限流、多进程容量控制、凭据轮换管理后台或生产级可观测性。单个长时间模型调用会阻塞其他请求；不要对外开放这个服务器。可选模型适配器自己的超时与模型限制见 [RAG 项目](04-chinese-rag.md)。

## 9. 独立任务：证明能改，才能放进作品集

先不看参考实现，交付下面三项改造。每项提交实现、失败前后证据、运行命令和设计说明。

- **新业务迁移**：把退款咨询换成虚构校园设备报修。资料里同时存在保修期和响应时间，答案不能混用；新增至少一个不可回答的问题。业务字段改变后，旧审批必须失效。
- **审核冲突**：两个页面读同一版本，其中一个页面修改草稿。另一个页面批准时必须失败，并能重新读取和审核；增加真实 HTTP 测试，不能只测试内部函数。
- **运维演练**：下游提交后丢失回执，停止并重启服务，再恢复投递。用两边数据库记录证明下游只产生一个效果，并说明没有去重能力的外部 API 会怎样。

作品集验收还要求：新环境按 README 能启动；正常、拒答、越权、过期、并发或陈旧版本、重试和重启都有记录；能解释每个 HTTP 状态、SQL 事务范围与资源所有权；能指出哪些指标是实测，哪些只是目标。参考服务、固定 smoke 和模拟审核只能帮助复现，不能替代个人改造或真人试学结论。

可选进阶：接 FastAPI 并保持接口与验收测试不变；引入真实身份验证与审核界面；把投递进程独立部署；做有限并发与尾延迟实验。先证明需求，再引入框架。

## 10. 备份与恢复：使用新文件，不覆盖旧数据

先完成一次下游投递，使 `work/service.sqlite3` 和 `work/downstream.sqlite3` 都已存在。备份前把配置的 `write_enabled` 设为 `false`，停止 HTTP 服务，并确认所有 `recover` 进程已经退出。停写开关不取消已经开始的请求，所以要等正在执行的操作结束。

两个数据库必须在**同一段没有写入和投递的时间里**依次备份。SQLite 在线备份 API 可以正确读取已提交的 WAL 内容，但两次独立备份并不是分布式原子快照；不能只备份工单数据库，忽略下游已有的效果。

```bash
python -c "from pathlib import Path; Path('work/backups').mkdir(parents=True, exist_ok=True)"
python scripts/backup_service.py --source work/service.sqlite3 --destination work/backups/service-snapshot.sqlite3
python scripts/backup_service.py --source work/downstream.sqlite3 --destination work/backups/downstream-snapshot.sqlite3
```

两条命令都应输出 `status: backup_created` 和 `integrity_check: ok`。源数据库以 SQLite URI 的只读模式打开；目标必须是新文件，POSIX 权限为 `0600`。若目标已存在，命令会以 `destination_exists` 失败，保留原文件。再次备份应换一对名称，不能删除旧备份后盲目覆盖。文件完整性检查只能说明 SQLite 结构通过检查，不说明业务数据或两库时点一致。

恢复时仍保留原服务、下游和备份文件，另建一对工作数据库：

```bash
python scripts/backup_service.py --source work/backups/service-snapshot.sqlite3 --destination work/service-restored.sqlite3
python scripts/backup_service.py --source work/backups/downstream-snapshot.sqlite3 --destination work/downstream-restored.sqlite3
python -m agentlab.service serve --config work/service-auth.json --database work/service-restored.sqlite3 --port 8765
```

此时配置仍应停写。用另一个终端的客户端读取工单、核对状态和指标；身份配置没有包含在数据库备份里，恢复时应使用原有受限配置，否则角色主体变化可能导致无法访问原资源。凭据文件也需要另外安全保管，不要提交到 Git。

确认恢复的是约定的演练快照后，再恢复 `write_enabled: true`。投递命令必须同时指定这对**恢复后的文件**：

```bash
python -m agentlab.service recover --config work/service-auth.json --database work/service-restored.sqlite3 --downstream work/downstream-restored.sqlite3
```

若快照取自“下游已成功、上游回执尚未保存”的时刻，重放后仍应只有一条下游效果。若快照已全部送达，则没有待投递事件。不要混用恢复后的服务库与继续发生过新写入的原下游库；演练结束后保留两组记录，停止当前进程再选择后续要使用的数据库路径。

[备份脚本](../../scripts/backup_service.py)与[恢复测试](../../tests/test_backup.py)验证：旧目标不被覆盖、拼错的源路径不会创建空数据库、损坏源文件的失败清理、WAL 已提交数据、恢复后原幂等结果及下游一次效果。异常退出时只对本次新建的部分目标文件尝试清理；如果文件被其他进程替换则保留它，需人工确认。这些操作限于本地合成演练，不是对真实外部退款或通知的回滚。
