## 今日信号与洞察

- [SIGNAL] 强制执行规则被反复确认（用户 3 次以上说"without asking"、"is a must"）→ 需要将"强制执行不问"规则沉淀进项目级 CLAUDE.md 的 execution-strategy.md 中

- [SIGNAL] 规则管理体系演化：从散文式文档 → 表格式规则格式 → CLAUDE.md 元规则化。新增的"CLAUDE.md 规则格式"meta-rule 需要写入 claude-md-format.md

- [SIGNAL] 验证闭环未完全闭环：insights-share 项目执行了 plan → 编码，但 design.md 和 pm_walkthrough.md 文档写了多遍（timestamp 重复），suggest 需要一次完整的"验证 → 修复 → 复验"循环

- [SIGNAL] 重复出现的工作：insights-share demo 的 seed data（alice_pgpool.json、alice_celery_retry.json、carol_redis_eviction.json）、adapter.py、ui.py、insights_cli.py 等文件被写入多次，说明执行过程有重复或不确定

- [BLOCKER] daily-report skill 需求与实现未锁定：prompt 中多次出现"为 PM 展示"、"STAR 框架"、"tmux + claude -p"等需求，但具体如何验证交付物仍未明确

- [NEXT] insights-share 的验证框架（5 项硬性规则）需要按 validation.md → 实际测试 → 修复缺陷 → 再验 的模式走一遍完整闭环

- [NEXT] Rules progressive disclosure 系统（PPTX + paired markdown）需要完成并与 AGENTS.md 体系整合，目前只生成了 slides 的组件代码

- [SIGNAL] 环境风险信号：用户反复查询 bootstrap 状态（~/.claude/.bootstrap-output-temp），suggest 系统初始化或配置可能存在不确定性，需要确认 bootstrap 流程完整性
