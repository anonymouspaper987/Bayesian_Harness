# 合成世界规格 v0.1 (acmesdk)

一句话。一家虚构公司的内部 Python SDK, 按月发版三年, 漂移节奏照真实仓库
的实测统计校准, 模型对它零先验, 笔记是唯一的承重知识。

## 1. 世界骨架

| 项 | 默认值 | 说明 |
|---|---|---|
| 包名 | `acmesdk` | 虚构, 名字不与任何 PyPI 包撞车 |
| 子系统 | 12 个 | billing, auth, storage, search, notify, ledger, catalog, shipping, identity, metrics, scheduler, export |
| 每子系统 API | 30 到 40 个 | 共约 400 个公开函数/方法 |
| 函数语义 | 确定性纯逻辑 | 算价、折扣、税、日期窜位、字串格式化、列表聚合、ID 编码。每个函数带 3 条机器断言 |
| 时间轴 | 36 个月 M01..M36 | 每月一版, 版本目录独立, PYTHONPATH 切换即换代 |
| 随机种子 | 3 个世界 | 同一规格三次生成, 主表报跨世界置信区间 |

## 2. 漂移类型与比例

五种漂移, 前四种"吵" (旧代码报错), 最后一种"静" (旧代码照跑, 结果悄悄错)。
静默占比 20%, 对齐 BigCodeBench 阶梯实测的"约五分之一受害事件无报错"。

| 类型 | 占比 | 例子 | 旧笔记的下场 |
|---|---|---|---|
| 改名 | 30% | `compute_tax` -> `calc_tax` | AttributeError |
| 参数变化 | 25% | 参数改名/改序/新增必填参数 | TypeError |
| 返回结构变化 | 15% | 返回 tuple 改返回 dict | 下游取值报错 |
| 删除并给替代 | 10% | `legacy_round` 删掉, changelog 指向 `round_half_up` | AttributeError |
| 静默语义变化 | 20% | 金额单位分改元, 排序反向, 含税默认值翻转 | 不报错, 断言失败 |

## 3. 漂移节奏 (照实测校准)

三层叠加, 参数都来自 repo-facts Phase 0 与 BigCodeBench 阶梯的实测。

- 背景涓流。每个 API 每月约 1% 概率变化 (sklearn 签名变化率量级), 全年约
  400 x 12 x 1% = 48 个零散事件。
- 竖向爆发。36 个月里 6 次, 每次抽一个子系统, 40% 到 60% 的 API 同月换代
  (对应 sklearn 2023-09 ensemble 61/62 那种集中重构)。
- 横向爆发。36 个月里 2 次, 一个共享概念变化横扫所有子系统, 例如认证令牌
  参数从 `token` 改 `credential`, 或全局金额单位改变, 命中约 15% 的 API
  (对应 sklearn 2024-09 跨十几个子包的横向迁移)。

聚簇度目标。同月事件里"同子系统占比" 落在 55% 到 82% 区间, 生成后用脚本
核对, 不达标就重抽。

## 4. 外部知识与内部笔记

- 外部知识 = 世界发布的 changelog, 每月一份 markdown, 世界给的, 不是 agent
  写的。每条写清"什么变了, 新用法是什么"。维护阶段修笔记时可读, 考试阶段
  不给。
- 内部笔记 = agent 自己的, 每个 API 一张, 内容是出生时在当时版本验证通过的
  用法片段 (record 模式)。目录结构沿用 `<模型名>/{external,internal}/`,
  一张笔记一个 .md。

## 5. 考题

沿用已经证明不可作弊的"冻结断言修复题"。给一段在旧版本能跑的代码, 断言行
一字不许改, 要求在当前版本让它跑通。每道题必须满足旧版通过、新版失败的双重
行为验证才入池。每个 API 出 2 到 3 道不同变体, 避免重复遭遇时原题重考。

判分 = 断言逐行原样保留 + 当前版本运行退出码 0。不需要"必须用某个名字"的
规则, 因为不用新 API 根本过不了断言。

## 6. 流量模型 (重复遭遇)

每月约 10 次考试, 36 个月共约 360 次。被考的 API 按 Zipf 分布抽取 (指数 1.0),
少数热门 API 被反复撞上, 冷门 API 可能一次都不考。这样"历史使用量"对未来
真有预测力, 常用的先修才有意义。

## 7. 黑箱防线 (堵住上次所有漏洞)

1. 只发字节码 (.pyc), 不带源码, 没有 docstring。
2. 模块与类的 `__dir__` 返回空, `__all__` 为空, `__getattr__` 只报
   "unknown attribute", 绝不"did you mean"。
3. 所有公开函数外壳签名统一为 `(*args, **kwargs)`, 内部按隐藏规格校验,
   `inspect.signature` 看不到真实参数。
4. 报错文本统一为 "invalid arguments for <name>" 一类的泛化信息。
5. 探查 = 在当前版本跑笔记片段, 是花预算的世界动作; 生成器真值只给 oracle。

## 8. 实验条件与预算

七条件原样复用。none / static / random / ttl / bayesian_agent / ours / oracle。
维护预算按季 (或按月) 给定, 探查 1 单位, 修复 3 单位。先用 none、static、
oracle 三条件跑校准, 挑一个让 oracle 落在 60% 到 75%、none 在 5% 以下的
预算档, 再跑全部条件。ours 的因子 = 子系统归属 (所有方法都能看见)。

## 9. 验收标准

| 指标 | 目标 |
|---|---|
| none | <= 5% |
| static | <= none (旧知识有害) |
| oracle | >= 60% |
| 考试次数 | >= 300 / 世界 |
| 聚簇度 | 55% 到 82% |
| 静默事件占比 | 约 20% |

## 10. 实现清单

```
experiments/synth/
  spec.py           API 规格 DSL + 按种子生成 400 个 API 的规格表
  drift.py          三层漂移事件生成 + 聚簇度核对
  build_versions.py 把规格表编译成 36 个版本目录 (字节码 + 黑箱外壳)
  changelog.py      按版本差生成外部 changelog
  make_tasks.py     出题 + 双重行为验证 + 流量抽样
  world.py          版本切换与计费执行 (纯 PYTHONPATH, 无需 tar)
  loop.py           季度闭环 (从 masked_loop 骨架移植)
datasets/acme_sdk/seed{1,2,3}/
  versions/M01..M36/   changelog/   tasks.jsonl   truth.json   notes/
```

全部生成与验证在 CPU 上完成, GPU 只用于校准和主实验。
