# 上下文压缩：客户端设计稿与分阶段验收清单

本轮只修改 AppServer 服务端代码并编写本清单，不实施客户端压缩动画。
客户端每阶段必须分别完成实现、定向测试和规定的真机验收才能勾选，源码复核或测试替身通过不能代替真机通过。
原有 context left 的显示条件、右侧留白和稳定布局继续按 [CONTEXT_LEFT_ALIGNMENT_CHECKLIST.md](CONTEXT_LEFT_ALIGNMENT_CHECKLIST.md) 执行。

## 依据与职责

- 客户端内部职责以 [ARCHITECTURE.md](ARCHITECTURE.md) 为准，跨系统职责以 [ARCHITECTURE_SYSTEM.md](ARCHITECTURE_SYSTEM.md) 为准。
- 线上事件以 [AppServer/docs/PROTOCOL.md](../AppServer/docs/PROTOCOL.md) 的 `context.compaction.*` 为准；本设计不自行新增 UI 专用服务端事件或别名。
- [Codex StatusIndicatorWidget](https://github.com/openai/codex/blob/main/codex-rs/tui/src/status_indicator_widget.rs) 提供标题动画、整数秒计时、下方说明和同行附加状态。
- [Codex ChatWidget](https://github.com/openai/codex/blob/main/codex-rs/tui/src/chatwidget.rs) 区分可原位更新的活动单元与已提交历史；[replay.rs](https://github.com/openai/codex/blob/main/codex-rs/tui/src/chatwidget/replay.rs) 恢复压缩完成记录。
- 2026-09-12 已核对 GitHub 主分支；上述代码可作为交互机制参考，未在当前主分支找到本需求完整的 `Context compacting / Making room to continue.` 文案。以下文案是本需求的目标设计，不宣称逐字复制该分支。
- AppServer 拥有压缩执行、replacement 提交、结果和耗时；客户端拥有动画相位、布局、输入提示及视觉交接。后台终端数量来自现有终端注册表，不能固定显示示例中的 2。

## 服务端基础与字段

成功顺序：

```text
context.compaction.started       同一 item_id，in_progress
context.usage.updated            replacement 提交后发布的上下文用量
context.compaction.completed     同一 item_id，completed，latency_ms
```

失败使用 `context.compaction.failed`，携带 `error_type` 和 `retryable`；失败不生成假的 completed 或压缩后用量。
自动压缩属于当前 Turn，压缩完成后模型可以继续生成，只有 `turn.completed` 才能结束 Turn。

| 字段                                 | 语义与客户端用途                                                           |
|--------------------------------------|----------------------------------------------------------------------------|
| `cid`、`sid`、`turn_id`              | 限定所属会话和 Turn，禁止跨会话接管动画                                    |
| `item_id`                            | 同一次压缩的稳定身份；用于开始、完成、失败配对及去重                       |
| `event_seq`、`presentation_epoch`    | 使用现有 Canonical Item 顺序与代次归约，拒绝重放和迟到旧事件抢占画面       |
| `phase`                              | 自动压缩为 `pre_turn` 或 `mid_turn`；手动压缩为 `standalone`               |
| `trigger`                            | `automatic` 或 `manual`                                                    |
| `item_status`                        | `in_progress`、`completed`、`failed`                                       |
| `latency_ms`                         | 完成事件的非负整数毫秒；本次服务端补齐实际发送，客户端完成态与历史使用此值 |
| `before_items`、`after_items` 等统计 | 继续保留结构化数据，不将条目数挤入完成态主标题                             |

`latency_ms` 使用服务端单调时钟，从压缩流程开始计时，覆盖摘要生成、replacement CAS 提交及用量发布，到开始提交完成事件之前结束；不包含完成通知的后续传输、客户端绘制及客户端 PostCompact Hook。
进行态由客户端单调时钟刷新，不能使用整轮 Turn 的耗时，也不能重置整轮 Turn 的计时。
已有历史事件未提供耗时时保持未知，只显示完成文案；不显示伪造的 `0s`，不通过日志或当前时间倒推。
该字段已在既有协议统计字段中声明，本次服务端不改变端点、数据库结构或压缩策略。

中断边界必须准确：

- 自动压缩：Esc 使用当前 Turn 的既有中断入口；请求被接受后仍等待权威 Turn 终态，不把 HTTP 204 当成压缩已停止。
- 手动 `/compact`：关闭观察连接只停止客户端等待，服务端继续执行。目前没有独立压缩取消接口。显示 `esc to stop waiting`，不能显示或声称已中断服务端压缩。

## 设计稿

以下使用等宽字符示意。项目现有中性状态样式负责圆点与标题动画，说明、耗时及快捷键使用次级颜色。
不闪烁整行，不反复追加新的进度行；动画只更新当前活动区域。

### D1：自动压缩进行态，宽窗口

```text
• Context compacting (1m 38s • esc to interrupt) · 2 background terminals running · /ps to view · /stop to close
  └ Making room to continue.

› 后续消息草稿
  tab to queue message                                                               39% context left
```

- 第一行固定结构为：状态标题 → 本次压缩耗时及按键 → 真实后台终端信息。
- 第二行固定说明 `  └ Making room to continue.`，不展示摘要正文。
- 无后台终端时省略全部终端附加信息；1 个时使用单数。
- footer 仍只在 Turn 运行且有待排队草稿时显示右侧 context left；压缩动画不能改变这个条件。

### D2：手动压缩进行态

```text
• Context compacting (8s • esc to stop waiting)
  └ Making room to continue.

›
```

PreCompact Hook 尚未放行或服务端尚未确认开始时，可沿用独立的准备状态；不将准备耗时冒充服务端压缩耗时。
手动压缩仍使用现有前台操作屏障，不建立伪 Turn。

### D3：完成态及原位交接

```text
交接前的行 R：
• Context compacting (1m 26s • esc to interrupt)
  └ Making room to continue.

交接后的同一行 R：
• Context compacted  • 1m26s
```

- 文案是 `• Context compacted  • 1m26s`，不追加句号、条目数或重复的 completed 通知。
- 正式完成耗时由 `latency_ms` 向下取整为秒：`0s`、`59s`、`1m00s`、`1m26s`、`1h00m00s`。
- 不因客户端进度计时与服务端耗时不同而修改服务端结果。
- 同一视觉事务提交稳定完成单元、替换活动槽并撤下说明文字；交接帧保留标题的行锚点，将原说明行纳入完成记录之后的正常间隔，不通过额外空通知或永久空块占位。标题不能先清空、移位后再重建。
- 最后一个进行态帧与第一个完成态帧之间，不能存在两者均缺席的帧；同一 Item 也不能同时出现进行态和完成态两个标题。
- 若开始与完成落在同一绘制周期，允许直接显示完成态；不人为延迟完成以强行播放动画。
- 自动压缩完成后，Thinking 或后续正文从完成记录下方接续；完成记录只留存一次，不能覆盖它或重新播放已完成动画。
- 后台终端仍运行时，继续由现有状态区显示；不把实时终端数量固化进历史完成记录。
- PostCompact Hook 或后续 continuation 被阻止时，压缩已完成的事实仍保留，另显示后续步骤被阻止，不能改写为压缩失败。

### D4：失败与中断

```text
摘要失败：
• Context compaction failed
  └ <按服务端 error_type 映射的简短原因>

自动压缩所属 Turn 已确认中断：
• Context compaction · interrupted

手动模式用户停止等待：
• Stopped waiting for context compaction; server continues.
```

失败和中断必须经相同活动槽交接，不能残留旋转状态或伪完成耗时。
压缩失败但服务端允许继续时，只结束压缩活动，不结束整个 Turn；不可重试、网络失败、空历史及 CAS 冲突保留各自真实语义。

### D5：窄窗口与尺寸变化

```text
• Context compacting (8s • esc to interrupt)
  └ Making room to continue.
```

- 按实际显示列宽裁剪；优先保留标题、耗时和当前模式的 Esc 语义，再保留后台终端信息。
- 行尾附加信息不足以完整显示时先省略 `/stop`、`/ps` 提示，再省略终端附加段；不要截出误导性的半个命令。
- 极窄窗口允许按项目既有裁剪规则截短状态行，不覆盖输入框，不制造软换行导致的布局抖动。
- 说明只占规定的一行，必要时以省略号裁剪。宽度恢复后从状态重新投影完整内容。
- 草稿内容、选区、输入焦点及 context left 右侧留白不受计时刷新影响；同一宽度下输入区域不能随动画相位上下跳动。

## 分阶段实施与逐项验收

### P0：确认服务端契约

- [x] 核对自动/手动压缩的 started、completed、failed、稳定 Item 身份及用量先于 completed 的顺序。
- [x] 服务端实际发送 `latency_ms`，完成日志使用同一取值。
- [x] 覆盖自动 pre_turn、自动 mid_turn、手动 standalone 的计时、事件收集和协议投影；定向服务端测试通过。
- [x] 部署服务端补丁，真实 SSE 与回放的同一完成事件均为 `latency_ms=7731`。
- [x] Windows ConPTY 运行真实客户端 `/compact`，窗口 100000，完成事件耗时 8305 ms，19 项压缩为 6 项。
- [x] 真实服务触发自动 pre_turn、mid_turn 压缩失败，确认失败不误发 completed，所属 Turn 仍正常完成。
- [x] 自动压缩开始后通过正式中断接口请求停止，收到所属 Turn 的权威 `interrupted` 终态。
- [x] 手动压缩 started 后关闭真实 SSE，服务端仍完成并在回放中提供耗时 10855 ms。
- [x] 真客户端工具输出触发自动 mid_turn 压缩成功，完成事件耗时 14508 ms，随后 Turn 继续并完成。
- [ ] 自动 pre_turn 成功分支的独立真机验收；已验证其真实触发、失败与中断，成功计时目前由定向测试覆盖。

### P1：客户端类型与耗时传递

- [ ] 复用 `protocol/schema/stream_events.py` 中已有 `ContextCompactionEvent.latency_ms`，验证整数、非负、可空边界。
- [ ] 自动路径通过 `build_context_compaction_view` 保留耗时和身份；手动路径补齐 `CompactEvent → CompactResult → Transcript` 的耗时传递，不能在适配器中丢弃。
- [ ] 完成态共用一个耗时格式化实现，覆盖 0、999、1000、59999、60000、86420、3600000 毫秒及未知值。
- [ ] 测试失败无伪完成、旧历史无伪计时、Hook 后续阻止不改写已完成事实。

验收入口：`tests/integration/test_manual_compaction_protocol.py`、`tests/integration/test_context_compaction_presentation.py`、相关协议解析测试。

### P2：活动状态与计时归属

- [ ] 自动压缩事实由 `TurnActivityProjector` 发布，经 `reduce_turn_surface → TuiTurnSurfaceCoordinator` 进入唯一前景活动投影。
- [ ] 以 scope、Item ID、代次拒绝重复和迟到事件；同一 Item 重复 started 不重置计时，新 Item 才开启新压缩计时。
- [ ] 手动 `/compact` 沿用已有前台操作生命周期，不启动虚假 Turn 或独立于当前屏幕生命周期的后台 timer。
- [ ] 压缩独立计时，不清零整个 Turn 的计时；审批、恢复、关闭、切换会话时释放或抑制对应活动。
- [ ] 根据真实模式显示 Esc 操作语义；后台终端信息使用现有数据源。

验收入口：`tests/frontends/tui/runtime/test_tui_turn_surface.py`、`tests/frontends/tui/core/test_tui_activity.py`。

### P3：动画与完成态原位替换

- [ ] 完成 D1–D5 设计；自动压缩与 `/compact` 完成态采用相同标题和耗时样式。
- [ ] 复用活动 lease、`activity_handoff` 和 `screen.visual_update`；旧动画撤下与稳定单元提交必须在同一视觉事务中完成。
- [ ] 真实 renderer 帧断言：标题首行交接坐标相同、无中间空帧、无重复标题、无完成后复活动画。
- [ ] 覆盖瞬间完成、长时间压缩、提交阶段 await、说明行收起、多次连续压缩和后台通知同时到达。
- [ ] 完成后继续 Thinking/正文；无重置 Turn elapsed、重复分隔线、错误释放输入屏障的问题。
- [ ] 宽/窄窗口、中文多行草稿、后台终端 0/1/2 个时符合设计，状态变化不影响 context left 布局。

验收入口：`tests/frontends/tui/runtime/test_tui_frame_contract.py`、`tests/frontends/tui/features/test_tui_compact.py`、输入布局与后台终端相关测试。

### P4：回放、恢复与收尾

- [ ] replay 只恢复历史完成记录，不播放历史 started 动画；追平后仅恢复仍活动的压缩 Item。
- [ ] 回放完成耗时等于服务端事件值；缺失耗时时显示未知，不按离线时长累计。
- [ ] 断开重连、取消、失败、Turn 终态、退出及切换会话均释放 timer 和 lease。
- [ ] 迟到旧 completed 不覆盖当前新 Item；重复 completed 不增加第二条完成记录。
- [ ] 手动停止等待后再恢复会话，能读取服务端真实最终状态，不将本地停止等待持久化为远端已取消。

验收入口：恢复/历史相关测试、协议 Item reducer、现有断线恢复场景。

### P5：回归检查

- [ ] 激活仓库虚拟环境后，先运行以上受影响模块的定向 pytest。
- [ ] 若新增活动契约或改变依赖边界，再执行 `python -m pytest tests/test_package_architecture.py tests/architecture -q`。
- [ ] 执行 `python -m compileall agent protocol frontends infrastructure observability metadata` 和 `git diff --check`。
- [ ] 保留既有 context left 默认可见性、模型切换/恢复、右侧留白和稳定性测试通过。

### P6：客户端真机验收矩阵

使用真实 `mind.py`、真实部署服务和真实模型请求；主上下文窗口设置为 100000。
自动触发测试可在隔离验收配置中临时降低自动压缩阈值，但必须保持窗口为 100000，并在结束后恢复该配置。
逐帧检查使用真实 Windows Terminal/ConPTY 的渲染提交与原始终端输出，截图只能辅助，不能单独证明没有空帧。

| 勾选 | 场景                         | 必须观察到的结果                                         |
|------|------------------------------|----------------------------------------------------------|
| [ ]  | 手动 `/compact` 成功         | 两行进行态，真实计时，服务端完成后原位显示权威耗时       |
| [ ]  | 自动 pre_turn                | 位于所属 Turn 开始后，完成后继续模型生成                 |
| [ ]  | 自动 mid_turn                | 已有正文/工具记录保留，完成记录在当前尾部原位接续        |
| [ ]  | 真实快速完成                 | 无强制等待、无空白帧、只有一条完成记录                   |
| [ ]  | 压缩失败                     | 显示真实原因；无假成功，后续行为与服务端一致             |
| [ ]  | 自动压缩期间 Esc             | 中断登记后等待权威终态，再完成中断态交接                 |
| [ ]  | 手动压缩期间 Esc             | 仅提示停止等待；恢复后能核对服务端实际结果               |
| [ ]  | 后台终端 0、1、2 个          | 数量与单复数正确，完成后终端仍可通过 `/ps`、`/stop` 操作 |
| [ ]  | 120→80→50→25→120 列          | 裁剪与恢复正确，无输入焦点/草稿丢失或持续抖动            |
| [ ]  | 有草稿、无草稿、中文多行草稿 | context left 仍遵守原有可见条件，计时刷新不移动输入位置  |
| [ ]  | 压缩开始后断开并重连         | 历史动画受抑制，追平后状态准确，完成态去重且耗时一致     |
| [ ]  | 已完成会话冷启动恢复         | 不播放压缩动画，只恢复完成记录及原始服务端耗时           |
| [ ]  | 连续两次压缩                 | 每个 Item 计时独立，第一项迟到事件不覆盖第二项           |
| [ ]  | 压缩期间退出/切换            | 无残留 timer、spinner、后台任务或下一会话污染            |

验收结论必须注明实际终端、服务版本、模型、窗口/阈值、会话/Item ID 与成功/失败结果。
服务端事件真实验收不等于客户端视觉验收；本轮未实现客户端动画，上表不能提前勾选。

## 本轮服务端验证

- 服务端改动位置：`services/llm/llm_flows/flow_compaction.py`；正式说明：`docs/PROTOCOL.md`。
- 已通过 123 项压缩、上下文预算、窗口策略及 Item 状态测试。
- 已通过 85 项事件平面、回放、事件存储及 Python 文件约定检查；编译和差异格式检查通过。
- 真实部署版本：`20260912054730-580d1437bf59-b1449531b920`，在原部署上仅替换本次压缩流程代码并重建 HTTP/Worker 镜像，两个容器的文件 SHA-256 均为 `b1449531b9209abaea8d2afc5bdcdde2d86df4250a660e114ca0b0151e3afb08`。
- 真实环境：Windows ConPTY + 部署服务器 `192.168.2.81`；OpenAI Profile、`gpt-5.6-sol`、`chat_completions`；上下文窗口 100000。自动失败与中断测试仅在请求副本中将阈值改为 1；真客户端自动成功测试通过启动覆盖将阈值设为 20000，未改变用户主配置。
- 验收会话：`cid_tl8igo_5043ed82 / sid_tl8igo_mtxwgv1u_e5d787`。

| 验证方式                   | Item / Turn                    | 实际结果                                                                                                                     |
|----------------------------|--------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| 真客户端手动压缩           | `compaction_mtxyw3ms_7ce8c919` | seq 36 started → 37 usage → 38 completed；8305 ms；19→6 项                                                                   |
| 真实 SSE 与回放对照        | `compaction_mtxyzd64_b7e953fc` | seq 39→40→41；两次读取的完成耗时均为 7731 ms                                                                                 |
| 自动 pre_turn 失败         | `compaction_mtxz0gwk_945765d6` | `no_gain`，没有伪 completed                                                                                                  |
| 自动 mid_turn 失败         | `compaction_mtxz0m94_284d932d` | `summary_failed`；所属 `compaction_acceptance_auto_20260912` 在 seq 50 正常完成                                              |
| 自动压缩中断               | `compaction_mtxz1qzr_3cd62094` | started seq 52；`compaction_acceptance_interrupt_20260912` 在 seq 53 确认为 interrupted                                      |
| 手动关闭观察连接           | `compaction_mtxz2q0v_6ef8a9de` | 关闭 started 后的 SSE，服务端仍在 seq 56 完成，耗时 10855 ms                                                                 |
| 真客户端自动 mid_turn 成功 | `compaction_mtxz67k5_6bd9212c` | 真实 `exec_command` 输出触发，seq 64 started → 65 usage → 66 completed；14508 ms；10→8 项；Turn `aazmw2punbcq` 继续到 seq 70 |

- 客户端设计及 P1–P6：待实施；不以服务端测试结果替代客户端验收。
