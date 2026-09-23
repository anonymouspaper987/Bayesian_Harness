# acmectl: 第三个知识领域 (命令行工具用法笔记)  2026-09-22

## 目的
论文引言说的是智能体的记忆、工具、技能、流程, 两个数据集却都是 "Python 库的 API 用法". 这个世界把同一套
漂移换成 **命令行工具** 的表面, 用来证明 "有效性和质量要分开估" 不是 Python 特有的.

## 设计原则: 只换表面, 不换世界
- 复用 data/acmesdk/seed{1,2,3} 的 36 个版本 (字节码包)、真值、漂移事件和变更日志. 不重新生成.
- `acmesdk/cli/acmectl` 是这套包的命令行外壳: `acmectl <子系统> <命令> [位置参数(JSON)...] [--旗标 值 ...]`,
  参数按 JSON 解析 (解析不了当字符串), 输出紧凑 JSON (键排序). 没有 --help 列表, 报错泛化, 和包一样是黑箱.
- 漂移一一对应: 函数改名 = 子命令改名; 参数改名 = 旗标改名; 返回形状 = JSON 输出形状; 删除接班 = 子命令删除并给接班;
  语义变化 = 输出值变化 (单位、取整等).
- 笔记 = shell 用法片段 (三行: `out=$(acmectl ...)`, `test "$out" = '<期望 JSON>'`, `echo Success`).
  考试 = 修一个跑不通的 shell 脚本, 冻结的是 `test` 行. 判分: test 行按顺序原样保留 + 提交里出现 acmectl + 调用了当前名字 + 沙箱退出码 0.
- 出题用 make_tasks.py 同一个随机序列 (make_tasks_cli.py). 实测 seed 1 的 1236 道题逐月断裂矩阵与 Python 世界完全相同
  (启动器只是薄外壳), 于是直接复用 Python 世界的 exams.jsonl 作为 exams_cli.jsonl: 两种知识类型考同一批 (题, 月), 可按题配对.
- 判分和笔记片段都依赖 `set -e` 语义: 片段首行 `set -e`, 沙箱用 `bash -e` 执行 (shell 里 test 失败不会像 assert 那样中止脚本).
- 变更日志在喂给修复提示词时按命令行措辞转写 (cli_surface.changelog_cli): `auth.pull_scope` -> `acmectl auth pull_scope`,
  "parameter `x`" -> "flag `--x`", 用法签名 -> `acmectl sub name <a> [--b 0.1]`.

## 与 Python 表面的关键差别 (这是它有价值的地方)
- 输出形状/单位变化在 shell 里要用管道 (`python3 -c` / `sed`) 补偿, 比 Python 里改一行难, 行为类漂移不再饱和.
- 笔记是 shell 一行命令而不是 Python 代码, 模型对 "工具用法" 的先验和对 "库 API" 的先验不同.

## 文件
- `cli/acmectl` 启动器 (真实沙箱用); `cli_surface.py` 渲染 / 进程内模拟 (与启动器共用同一份参数解析) / test 行校验 /
  提示词 / 变更日志转写 / CliEnv 沙箱 (PATH 加 cli 目录, run_sh).
- `make_tasks_cli.py` -> seed 目录下 `tasks_cli.jsonl` `exams_cli.jsonl` `notes_cli_M01.jsonl`.
- loop.py 用 `--surface cli` 切到这个表面, 默认 `--surface py` 行为不变.
- 未做: cheapen 的 name 模式 (lowq.degrade_text) 只认 Python 片段; 命令行表面暂不支持便宜修复 (主表不需要).
- 结果文件前缀 `cli_qwen27b_mb{12,24,48}_{cond}.jsonl`; 汇总沿用 analyze.py (事件真值相同).

## 跑法
Qwen3.5-27B x 预算 12/24/48 x 种子 1-3 x 七策略 (none, static, random, ttl, bayesian_agent, ours_hhmm, oracle) = 63 次,
与 Table 2 同格式. 只需在 run_acmesdk.sh 的 loop.py 调用上加 `--surface cli`.
