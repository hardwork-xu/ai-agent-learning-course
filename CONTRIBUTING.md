# 贡献指南

欢迎修正错误、补充反例、改进练习和提交能复现的实验。先说明读者遇到的具体困难，再提交解决它的最小改动。

## 内容质量

一篇新章节应回答：是什么、为什么需要、如何实现、何时失败、怎样验证。至少包含一个具体例子、一项取舍、一题练习和参考答案。关键技术结论链接原论文、协议或官方文档；变化快的 API 写明版本或核验日期。

面试题需要直接答案、推导或例子、追问和评分要点。不要收录来源不明的“某公司真题”，也不要编造招聘趋势、薪资、成功率或生产案例。

实验结果附命令、数据版本、配置、分母和失败样例。教学假设、离线测试与真实模型结果应明确区分。优化不能只展示最好的单次运行。

## 代码与文档检查

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python scripts/check_exercises.py
python scripts/check_docs.py
python scripts/check_public_content.py
python -m agentlab evaluate --output work/evaluation.json
```

文档使用 UTF-8，标题表达具体问题。链接优先使用仓库相对路径，图片必须有替代说明。示例命令从仓库根目录执行。保持默认运行无网络、无凭据、无外部写入。

修改 CaseDesk 时，先安装独立的 Web 依赖，并验证实际服务链路：

```bash
python -m pip install -r requirements-desk.lock
python -m pip install -e . --no-deps
python -m unittest discover -s tests -p "test_desk*.py" -v
python scripts/smoke_desk.py
python -m agentlab.desk.evaluate --output work/desk-retrieval.json
```

下载依赖可能联网；默认运行不调用模型或外部业务系统。真实模型实验必须显式选择已安装模型，并完整保存配置、版本、用量与失败。不得把新的报告覆盖到旧配置的结果文件。参考代码、维护者检查、学生自述与经过观察的真人试学分别记录。

## 公开内容边界

仅提交自有或许可明确的内容，数据优先使用合成样例。不要提交密钥、个人联系方式、真实客户记录、聊天记录、机器绝对路径、浏览器状态、原始生产日志或未经授权的课程材料。

公开扫描只是辅助检查，不能代替人工阅读。提交前查看 `git diff --cached`，确认提交作者使用愿意公开的身份与邮箱。请勿在公开 Issue 中粘贴敏感漏洞利用数据或凭据。

## Pull Request 说明

说明改动解决的问题、受影响的读者路径、验证命令和结果，以及仍然存在的限制。涉及数值结论时附可复核的实验产物；涉及行为变更时更新教程和测试。

## 新手路径与求职证据

新增练习必须给出学生入口、稳定接口、补课位置和行为检查；起始代码应失败，参考实现应通过，至少验证一种常见错误实现会被拒绝。参考答案测试与学生作业检查分别命名，不以 CI 通过代替学习者完成。

应用项目需说明已实现、未验证与扩展设计；真实模型运行、合成 HTTP 测试、真人试学分开记录。修改接口后同步教程和完整 smoke。不得提交学习者作业、身份、凭据、数据库或未经许可的试学记录；试学流程见 [教学验证](docs/teaching/pilot.md)。
