# 动手练习：从补全函数到独立迁移

[先修诊断](prerequisites.md) · [学习路线](roadmap.md) · [面试编程题](interview/coding-exercises.md)

练习使用虚构商品和设备编号。先按题目写自己的实现，记录一次失败，再对照反馈修改。参考实现与学生文件分开保存；仓库测试通过和自己的作业通过是两件事。

## 文件和命令

| 文件 | 用途 |
| --- | --- |
| [starter.py](../exercises/starter.py) | 复制后填写的起始文件，故意无法通过 |
| [grading.py](../exercises/grading.py) | 公开行为检查，可阅读失败对应的输入 |
| [reference.py](../exercises/reference.py) | 完整参考实现，先独立尝试再读 |
| [独立迁移任务](../exercises/transfer-task.md) | 第三关题目与接口，不含解法 |
| [评审量表](../exercises/reviewer-rubric.md) | 自评、同伴评审和变式答辩 |

在根目录创建 `work/my_answers.py` 的方法见[先修诊断](prerequisites.md)。每关用同一文件，保留已经完成的函数。

```bash
python scripts/grade.py --submission work/my_answers.py --task inventory
python scripts/grade.py --submission work/my_answers.py --task quote
python scripts/grade.py --submission work/my_answers.py --task transfer
python scripts/grade.py --submission work/my_answers.py --task all --report work/learning-report.json
```

退出码 0 表示所选公开检查全部通过，1 表示至少一项未通过，2 表示无法加载文件。报告只记录答案文件名和内容 SHA-256，不复制完整答案；失败信息来自本地执行，分享前仍应检查有无自己的路径或数据。评分程序以当前用户权限执行导入的代码，不提供隔离，只运行可信本地文件。

全部 20 项公开检查通过，只表示满足本题可自动检查的契约。测试会换商品编号和数量，但测试源码公开；这不等于未见题考核或独立完成证明。独立性由换场景后的实现和解释来检查。

## 第一关：照着数据流实现只读工具

**目标：** 接到结构化请求时，先检查边界，再调用指定操作。

先看一个只接受已校验参数的函数：

```python
def lookup_known_sku(sku, catalog):
    if sku not in catalog:
        raise LookupError("unknown sku")
    return {"sku": sku, "available": catalog[sku]}

assert lookup_known_sku("PEN-001", {"PEN-001": 8}) == {
    "sku": "PEN-001", "available": 8
}
```

它没有检查调用方传入的对象结构。自己的 `inventory_tool(request, catalog)` 要加上这一层，然后完成同样的查询。

输入示例：

```json
{"name": "inventory.lookup", "arguments": {"sku": "PEN-001"}}
```

契约如下：

1. `request` 必须是字典，恰好包含 `name`、`arguments`；工具名只能是 `inventory.lookup`。
2. `arguments` 必须是字典，恰好只有 `sku`。商品编号必须是三个大写英文字母、短横线、三个数字，例如 `PEN-001`；末尾换行也不能接受。
3. `catalog` 是调用方提供的可信字典：商品编号映射到非负整数库存。不得修改它。
4. 编号存在时返回 `{"sku": 编号, "available": 库存}`，库存 0 也应正常返回。
5. 结构、名称、类型或格式错误抛 `ValueError`；格式正确但查无商品抛 `LookupError`。

先只实现正常输入，再依次检查外层字段、内层字段、类型与编号。可以使用 `re.fullmatch`；不要通过拼接或 `eval` 根据工具名执行代码。

**检查点：** `inventory_normal_and_zero_stock` 失败时查返回结构；`inventory_exact_schema` 失败时查额外字段；`inventory_sku_and_unknown` 失败时区分非法格式与未知商品。通过后，不看代码画出“请求—校验—查询—结果”四步图。

## 第二关：补全报价工具

**变化：** 工具名变为 `inventory.quote`，参数同时包含商品编号和数量；返回报价，仍不扣库存、不创建订单。

```json
{"name": "inventory.quote", "arguments": {"sku": "PEN-001", "units": 3}}
```

实现 `quote_tool(request, prices)`。外层、编号格式和异常约定与第一关相同；内层必须恰好为 `sku`、`units`。数量是 1 至 100 的真正整数；拒绝布尔值、字符串和小数。`prices` 是可信价格表，金额单位为分，值为正整数。

返回值恰好为 `{"sku": "PEN-001", "units": 3, "total_cents": 375}`，其中示例单价为 125 分。总价高于 1,000,000 分时抛 `ValueError`，等于上限允许。不要修改价格表，也不要把客户端自行提供的 `price` 当成可信价格。

起始文件已给出外层校验、查询价格和构造结果的代码；把两处 `NotImplementedError` 替换为内层校验与总价上限检查。先在纸上列三列：输入、应返回或抛出的异常、理由。至少包含正常报价、零数量、`true`、未知商品、额外价格字段和总价越界，再写代码。

**通过后的追问：** 为什么金额采用整数分？如果同一请求在两个不同价格版本下执行，幂等指纹是否只包含商品编号就足够？先说明业务规则，再决定哪些字段必须冻结。

## 第三关：独立实现设备借用申请

暂时不看参考答案，按[独立迁移任务](../exercises/transfer-task.md)实现 `LoanBook`。新场景要求持久化和租户范围的幂等，不能把库存查询函数换个名字就交付。

先画表和事务边界，再实现正常创建、重放、冲突拒绝、重连恢复，最后测试并发与失败回滚。这里的“借用申请”只有 `pending` 状态，不代表设备真的交付或申请已获批准。

评审时请对方改一个约束，例如增加审批状态或给重试响应增加状态查询。先书面解释受影响的 API、数据和测试，再实现约定的最小范围。一次性通过公开检查后能否适应变化，是另一项能力。

## 怎样使用参考答案

确实卡住时，先保存当前版本、失败信息和自己的猜测。只看相关函数，关掉答案后重新实现，再说明改动修复了什么。如果整段照抄，诚实记为“跟做完成”，之后用变式重新验收。

维护者验证参考实现时运行：

```bash
python scripts/grade.py --submission exercises/reference.py --task all
python -m unittest discover -s tests -p test_learning.py -v
```

第一条会明确标记 `source_role=reference`。第二条还检查未改起始文件必须失败，以及把布尔值当整数、忽略字段或幂等参数冲突的错误实现会被拦住。它们是评分器的质量检查，不能作为某位学生完成作业的记录。

## 何时进入应用项目

自己的文件通过公开检查，能够解释一次失败与修复，并完成量表中的迁移答辩后，进入[毕业项目](projects/capstone.md)。练习的数据库路径和租户由可信本地调用者传入；HTTP 应用还必须建立认证、授权、输入大小限制、可观察性与恢复机制。练习中的短函数不能直接当作公网服务部署。
