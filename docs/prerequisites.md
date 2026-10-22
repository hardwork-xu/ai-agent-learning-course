# 先修诊断与补课

[从这里开始](start-here.md) · [动手练习](practice.md) · [学习路线](roadmap.md)

先确认自己能写函数、遍历列表、读写字典和运行 Python 文件。本页补齐应用开发需要的几个连接点；如果这些操作仍不熟，先用 [Python 官方教程](https://docs.python.org/3.11/tutorial/)练习输入、分支、循环和函数，再回来。数学部分是阅读第二章的桥梁，不必先学完模型训练才能写第一个只读工具。

## 先运行，再判断缺哪一块

使用 Python 3.11 或更新版本，所有命令在仓库根目录执行。练习只依赖标准库，不需要 API Key、GPU 或数据库服务。

```bash
python --version
python -c "from pathlib import Path; Path('work').mkdir(exist_ok=True); content = Path('exercises/starter.py').read_bytes(); target = Path('work/my_answers.py').open('xb'); target.write(content); target.close()"
python scripts/grade.py --submission work/my_answers.py --task prerequisites
```

第二条命令只在**第一次**创建答案文件时运行；若文件已存在会报 `FileExistsError` 并保留已有答案，此时直接进入第三条。也可以在文件管理器中复制并重命名。[起始文件](../exercises/starter.py)故意留空，首次应看到 `0/9 checks passed`，退出码为 1。这说明题目尚未完成。`LOAD FAILED` 和退出码 2 才表示文件无法加载。

只修改自己的 `work/my_answers.py`。评分程序会用本机当前用户的权限导入并执行该文件，和直接运行 Python 一样；它不是安全沙箱，只运行自己信任的本地代码。`work/` 已被 Git 忽略，个人笔记与评分报告放在其中。

| 输出中失败的前缀 | 补课位置 | 回到主线的条件 |
| --- | --- | --- |
| `json_` | 下方 Python 与 JSON | 三项检查通过，并解释为什么 `true` 不能当数量 |
| `http_` | 下方 HTTP 与重试 | 三项检查通过，能解释一次 POST 超时的两种可能 |
| `sql_` | 下方 SQL 与事务 | 三项检查通过，能证明第二次写入失败后第一次也未留下 |

可以分块修改后反复运行同一命令。不要为了让检查变绿修改评分脚本。它检查明确列出的行为，不会证明代码由谁完成，也不能替代口头解释。

## Python 与 JSON

JSON 是交换数据的文本格式；Python 字典是程序里的对象。`json.loads` 负责把文本变为对象，业务校验负责判断对象是否符合接口。能解码不等于能接受。[Python JSON 文档](https://docs.python.org/3.11/library/json.html)

```python
import json

data = json.loads('{"units": 3}')
assert data["units"] == 3
assert set(data) == {"units"}  # set(dict) 取所有字段名
```

`{"units": "3"}` 的值是字符串，`{"units": true}` 的值是布尔值。Python 中 `isinstance(True, int)` 为真；若契约要求真正的整数，使用 `type(value) is int`。不要自动把字符串、布尔值转换为数量，否则调用方的错误会被悄悄接受。

**要完成的函数：** `parse_units(text: str) -> int`。箭头标记返回值类型，本身不会自动做校验。

1. 输入必须是字符串，并能解析为 JSON 对象。
2. 对象只能有 `units`，不能缺字段、出现额外字段或重复键。
3. `units` 必须是 1 至 100 的整数，包含两端；拒绝布尔值、浮点数、字符串和空值。
4. 返回该整数；不合法时统一抛 `ValueError`。

| 输入 | 期望 |
| --- | --- |
| `{"units": 3}` | 返回 `3` |
| `{"units": 3.0}` | 抛 `ValueError` |
| `{"units": true}` | 抛 `ValueError` |
| `{"units": 3, "admin": true}` | 抛 `ValueError` |
| `{"units": 2, "units": 7}` | 抛 `ValueError` |

重复键在转成字典后可能已被覆盖。可给 `json.loads` 传入 `object_pairs_hook`，它接收到的是键值对列表，例如 `[('units', 2), ('units', 7)]`；在构造字典之前查重复。初次实现先通过正常输入，再加字段和类型检查，最后处理重复键。

**不看代码解释：** 为什么 JSON 数组也可以解析成功，却不能作为这个接口的输入？如果把 `units` 改成“转账金额”，仅有类型校验还缺哪些业务约束？

## HTTP 与重试

把一次调用分成三步：客户端发请求、服务端执行、客户端收到响应。超时可能发生在任意两步之间。客户端没有收到成功响应，无法据此断言服务端没有写入。

例如借用设备的 POST 已经创建申请，响应在网络中丢失。再次提交一个新请求，可能多出第二张申请。幂等键的作用是让服务端识别“这是同一次业务操作”，并核对参数后重放原结果；仅添加一个名为 `Idempotency-Key` 的请求头，不会自动带来这个能力。HTTP 方法的幂等语义见 [RFC 9110 第 9.2.2 节](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2)。

**要完成的函数：** `retry_action(method, status, has_key) -> str`。

本题采用下表的**教学策略**，不是通用网络库。`status=None` 表示没收到响应；`has_key=True` 的前提是服务端已经实现持久幂等，并且重试沿用同一键与相同参数。

| 输入情况 | 返回值 | 含义 |
| --- | --- | --- |
| GET；状态为 `None / 429 / 502 / 503 / 504` | `retry` | 可以进入有预算的重试流程 |
| POST；上述状态且 `has_key=True` | `retry` | 按已确认的幂等协议重试 |
| POST；上述状态且 `has_key=False` | `reconcile` | 先查询、核对执行状态；本题保守地统一处理 |
| 其余合法状态，包括 2xx、400、401、403、404、409、500 | `stop` | 此策略不自动重试，交给成功处理或错误处理 |

仅接受字符串 `GET`、`POST`；状态为 `None` 或 100 至 599 的真正整数；`has_key` 必须是布尔值。其他输入抛 `ValueError`。`stop` 不等于成功，调用者还应区分 200 和 403。

**先手算再写函数：** `POST, None, False` 应返回什么？同一次申请带服务端支持的幂等键又应返回什么？为什么 403 不能靠多试几次解决？

这个函数不发请求、不等待，也不处理 `Retry-After`。后续的[有限重试编程题](interview/coding-exercises.md)再加入总预算、退避和模拟时钟。真实策略还必须根据下游服务契约决定哪些错误可重试。

## SQL 与事务

两条写入属于同一件事，就应一起成功或一起回滚。唯一约束可以阻止重复值，但如果每条 SQL 后都提交，第二条失败不会撤销已经提交的第一条。

先在临时内存数据库运行这个小例子。问号是参数占位符；数据和 SQL 结构分开传递，包含引号的文本也按数据存储。

```python
import sqlite3
from contextlib import closing

with closing(sqlite3.connect(":memory:")) as connection:
    connection.execute("CREATE TABLE notes(label TEXT UNIQUE NOT NULL)")
    with connection:  # 正常退出提交；异常退出回滚
        connection.execute("INSERT INTO notes(label) VALUES (?)", ("sample",))
    print(connection.execute("SELECT label FROM notes").fetchall())
```

注意单元素元组的逗号：`('sample',)` 是一个参数，`('sample')` 仍是字符串。连接的 `with` 负责事务，不负责关闭连接；例子外层的 `closing` 负责关闭。[Python sqlite3 文档](https://docs.python.org/3.11/library/sqlite3.html)

**要完成的函数：** `record_pair(connection, labels) -> None`。评分器提供没有未提交事务的连接、表 `diagnostic_events(label TEXT UNIQUE NOT NULL)`，以及恰好两个字符串组成的元组。函数必须把两项放在同一事务中，使用参数绑定；正常时提交，失败时回滚并继续抛出原始 `sqlite3.IntegrityError`。不要关闭调用者提供的连接。

手工追踪以下两种情况：

| 初始数据 | 本次 labels | 最终数据 |
| --- | --- | --- |
| 空 | `('alpha', 'beta')` | 两条都在 |
| 只有 `'exists'` | `('new-row', 'exists')` | 仍然只有 `'exists'`，异常被调用者看到 |

**返回条件：** 诊断的 SQL 检查通过；自己再加一个含单引号的 label，说明为什么它不会变成 SQL。随后读 [SQLite 事务说明](https://www.sqlite.org/lang_transaction.html)，理解第三关为什么先取得写事务再查幂等键。

## 数学桥梁：先算一个小例子

以下内容帮助阅读 [LLM 基础](chapters/02-llm-basics.md)。应用主线先掌握输入、输出与代价；训练与算法岗位需要另外系统学习线性代数、概率和机器学习。

**向量与矩阵。** 向量是一列有顺序的数；矩阵可把多个向量排成表。行向量 `[2, 1]` 乘矩阵 `[[3, 0], [4, 5]]`，结果是 `[2×3+1×4, 2×0+1×5] = [10, 5]`。先看形状：`1×2` 乘 `2×2` 得 `1×2`，中间维度必须相等。

**条件概率。** 设第一个词为 A 的概率是 0.3，已知 A 后第二个词为 B 的概率是 0.8，词序列 AB 的概率就是 `0.3×0.8=0.24`。第二个概率依赖前面的词；不能把每个位置最常见的词随意拼接成“概率最高的句子”。

**Softmax。** 输入两个分数 `[0, ln(3)]`，先取指数得到 `[1, 3]`，再除以总和 4，得到权重 `[0.25, 0.75]`。这些权重相加为 1，但并不能因此被当作经过校准的答案正确率。

**一次最小注意力计算。** 令查询 `q=[1]`，两个键分别为 `[0]`、`[ln(3)]`，两个值分别为 `[2,0]`、`[0,4]`。点积后分数仍为 `[0,ln(3)]`，此例键维度为 1，缩放因子 `sqrt(1)=1`。套用上面的权重，输出为 `0.25×[2,0]+0.75×[0,4]=[0.5,3]`。它展示的是按相关性加权聚合，不是检索器在数据库里查一行。

**抽样。** `8/10` 和 `80/100` 的观察比例都是 80%。样本越小，换几个问题就越容易改变比例；如果两组题目难度不同，还不能仅比较比例。评测时先报告分子、分母和题目组成，再讨论不确定性。

完成以下三问再读公式；答案可在独立计算后展开。

1. `[1,3]` 乘上述矩阵的结果是什么？
2. 第一个词概率 0.5，第二个词条件概率 0.2，序列概率是多少？
3. 注意力的两个分数相同，两个值仍为 `[2,0]` 和 `[0,4]`，输出是什么？

<details>
<summary>核对结果</summary>

1. `[15,15]`；逐列计算，不是对应位置相乘。
2. `0.1`；第二个概率已经以第一个词为条件。
3. 权重各为 `0.5`，输出 `[1,2]`。

</details>

## Git 与运行环境的小验收

会使用终端切换到仓库根目录、确认解释器版本，并读懂异常最后一行。`FileNotFoundError` 先查相对路径；`ModuleNotFoundError: agentlab` 先按首页安装课程包；练习评分器本身无需安装课程包。

在自己的练习副本中修改一行，使用 `git diff` 确认只改了预期文件。`work/` 被忽略，因此自己的答案不会出现在正常 diff 中；需要留作作品时，先审查内容，再复制到自己项目的受版本管理目录，不要把环境文件、数据库或个人信息一并提交。

通过诊断后，进入[第一关只读工具](practice.md)。不需要等所有高级数学概念都熟悉才开始动手。
