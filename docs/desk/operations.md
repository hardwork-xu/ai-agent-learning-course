# 启动、停写、恢复与部署

[项目入口](README.md) · [数据规则](data.md)

当前交付单进程、小团队实验规模的应用。学习部署时先用本机和合成材料；公网服务还需要身份系统、TLS、反向代理限流、运行监控、密钥管理、数据保留策略和真实容量验证。下面每个命令都对应现有功能，不把未来部署设计算作已上线。

## 启动参数与边界

```bash
python -m agentlab.desk init --data-dir work/casedesk --demo
python -m agentlab.desk serve --data-dir work/casedesk --workers 2 --capacity 16
```

`workers` 是同一进程内的调查线程数，不是 Uvicorn 进程数。任务先落 SQLite，再由固定数量 worker 领取；容量包括排队、运行、补问、待审和已批准任务，避免人审积压无界增长。一个租户仍可能占满全局容量，租户公平调度是[独立实践题](learning-lab.md)，当前不能宣称已实现。

每个认证身份默认每分钟 120 次请求。限流计数在内存中，重启清空；它不替代网关的连接和未认证流量限制。请求体最多 160,000 字节，接收体总时限 5 秒；中文字符的字节数会更大，表单字符上限不意味着一定能用满。数据库事务会阻塞短时间，繁重检索使用同步线程池；不能据此宣称无限并发。

`/health` 只检查进程和 worker 存活，不证明模型可用、磁盘可写或答案正确。`/api/metrics` 需要凭据，并按租户/申请人过滤。状态总数覆盖该身份可见的全部任务；延迟、错误和调用用量只统计最近最多 1,000 个任务。延迟样本从首次领取到调查完成，包含补问等待；审批后不会把样本丢掉。未完成、取消和仍在补问的任务没有成功完成耗时，不能从这些样本计算总体请求成功率。它没有把 HTTP、排队、活跃模型时间和审核时间混成一个 SLO。

取消会持久化并使旧 fence 失效，防止后续状态推进；正在进行的同步调用可能继续到自身截止时间。每个调查回合默认 90 秒，进程恢复不重置该回合截止时间；用户补充信息后开启新的调查回合，任务累计决策步数仍受 8 步限制。调用尝试会独立保存：尚无回执时用量未知，取消后的晚到用量仍能补记，不能把取消误算成零消耗。停止服务使用 Ctrl-C；持久化的未完成工作由下次启动按租约恢复。

## 本地消费者与丢失回执

```bash
python -m agentlab.desk deliver --data-dir work/casedesk --downstream work/sink.sqlite3
```

这是独立 SQLite 下游模拟器，不会发邮件或调用公司的工单系统。上游提交工单与 outbox 在同一事务；消费者先记录事件唯一键与载荷摘要，再确认上游。如果消费者已成功、上游回执丢失，重投仍只能产生一条下游记录。`scripts/smoke_desk.py` 实际注入此故障。接入真实下游时需要对方幂等契约、查询回执和对账流程，不要照搬“数据库里一次”到任意远程 API。

## 一次完整的备份恢复演练

1. 在本地 `auth.json` 把 `write_enabled` 改为 `false`，新的 API 写入会被拒绝。此开关不停止已领取的 worker。
2. 用 Ctrl-C 停止服务，并停止任何独立的 `deliver` 进程。确认服务不再响应；不得在两份数据库仍变化时复制它们并称为一致快照。
3. 执行备份。目标必须不存在，备份命令检查两库完整性，全部成功才生成 manifest。

```bash
python -m agentlab.desk backup --data-dir work/casedesk --destination work/casedesk-snapshot
python -m agentlab.desk init --data-dir work/casedesk-restored
```

4. 在仍未启动恢复服务的情况下，把快照中的 `desk.sqlite3` 和 `knowledge.sqlite3` 复制到新目录，替换初始化时的空库。旧服务的数据目录保留。不要复制原目录的 WAL/SHM 文件到快照。
5. 初始化生成了新的凭据。按需要在本地维护原来的 tenant/actor 标识；凭据本身不在快照中。**先离线核对，再启动服务**：`write_enabled=false` 只阻止 API 写入，启动服务的 worker 仍可能恢复排队任务，不能把此模式当成只读恢复验证。
6. 可把下面代码保存为 `work/inspect_snapshot.py`，执行 `python work/inspect_snapshot.py`，与停服前保留的合成任务 ID、状态、资料版本和工单编号对照。它用只读连接检查恢复目录，不启动 worker；若使用真实数据，输出也只保留在本地。

```python
from contextlib import closing
from pathlib import Path
import sqlite3

root = Path("work/casedesk-restored")
checks = {
    "desk.sqlite3": [
        "SELECT id,state,version,ticket_id FROM runs ORDER BY id",
        "SELECT run_id,COUNT(*) FROM events GROUP BY run_id ORDER BY run_id",
    ],
    "knowledge.sqlite3": [
        "SELECT document_id,revision,active FROM documents ORDER BY tenant,document_id",
    ],
}
for name, queries in checks.items():
    uri = (root / name).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for query in queries:
            print(name, connection.execute(query).fetchall())
```

7. 核对完成后，检查新凭据和访问权限，开启写入再启动恢复服务。它会继续处理未完成任务；对同一 outbox 重投至原有下游，检查效果数没有增加。不要把原生产下游换成空库后，仍声称已验证重复投递去重。

备份命令不能替操作者证明其他进程已停止。多个文件的快照不提供跨库原子备份，必须遵守上述停服务步骤；目录无 manifest 时视为未完成备份。真实资料还需要另行配置加密、权限、保留周期和恢复演练负责人。

## 容器中的直接检索模式

仓库提供 [Dockerfile](../../Dockerfile) 和 [compose.yaml](../../compose.yaml)。本地需要 Docker；首次构建访问 Python 镜像和包索引。镜像不包含资料库、凭据或模型权重，使用非 root 用户。

```bash
docker compose build
docker compose run --rm desk init --data-dir /data --demo
docker compose up -d
docker compose logs --tail 30 desk
docker compose down
```

端口只绑定宿主机 `127.0.0.1:8766`；命名卷保存资料与随机凭据。通过本地受控方式读取卷内 `auth.json`，不要将其粘贴到 issue。`down` 保留卷；`down -v` 会删除数据，不属于普通停服步骤。

这个 Compose 配置运行固定检索基线。本机模型适配器限定回环地址；容器的回环地址指向容器本身，因此宿主机 Ollama 不会自动可用。需要模型实验时使用原生 Python 启动路径。容器构建与健康检查是否成功，以对应提交的 CI 运行记录为准。

实现依据：[FastAPI 的同步与异步执行](https://fastapi.tiangolo.com/async/)、[Uvicorn 设置](https://uvicorn.dev/settings/)、[SQLite 备份 API](https://www.sqlite.org/backup.html)。这些文档说明机制，实际本项目的验证见[验证记录](../verification.md)。
