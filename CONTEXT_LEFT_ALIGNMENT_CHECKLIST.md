# Footer Context Left 分阶段对齐清单

本文用于安排 ProxyMind 的 `context left` 对齐工作，参考根目录中的 `codex-main` 源码及本地 AppServer 源码。本文是实施清单，不定义已经存在的线上契约，也不代表功能已经完成或通过验收。

只有代码、契约和对应验收完成后才勾选；源码阅读、测试替身通过、真实终端通过分别记录，不互相替代。

客户端已按本文建议字段实现严格事件解析、会话投影与缓存、手动压缩消费和 footer。
客户端固定契约见 [docs/context-usage-protocol.md](docs/context-usage-protocol.md)，
双端共用样例见 [tests/fixtures/protocol/context_usage.json](tests/fixtures/protocol/context_usage.json)。
服务端已部署上下文事件和 `/mind-replay` 保留快照；客户端按现行 `context_usage` 嵌套结构
完成对接。P4 已开始真实服务和 Windows ConPTY 验收，具体证据见下方验证记录；未验收
场景继续保留，不将 P0–P5 或完整对齐统一标为完成。

当前验收部署为 `20260912041207-580d1437bf59-fb8475bc0492`，在原线上基线之上包含失败收尾
及分支事件信封两项修复。上下文窗口保持 100000；部署按授权清空运行数据，不执行备份。

## 目标与范围

- [ ] 在输入框 footer 右侧显示 `N% context left`，使用 dim，并处理窄窗口和特殊交互状态。
- [ ] 百分比使用最近上下文用量和服务端最终生效窗口，覆盖模型调用、压缩、会话恢复和切换。
- [ ] 区分当前上下文占用、累计用量、自动压缩阈值；三者分别具有明确语义。
- [ ] 保持服务端对实际模型上下文的权威性，客户端只保存可恢复投影并负责显示。
- [ ] 本次实现范围不包含 `backend/`；不搬入整套 Codex 压缩引擎，不借此改造无关 footer 信息。

架构依据：

- [D:/PycharmProjects/ProxyMind/AGENTS.md](/D:/PycharmProjects/ProxyMind/AGENTS.md)
- [D:/PycharmProjects/ProxyMind/ARCHITECTURE.md](/D:/PycharmProjects/ProxyMind/ARCHITECTURE.md)
- [D:/PycharmProjects/ProxyMind/ARCHITECTURE_SYSTEM.md](/D:/PycharmProjects/ProxyMind/ARCHITECTURE_SYSTEM.md)

## Codex 源码参考索引

以下链接均为本机绝对路径。实施时以符号名重新定位，避免源码变化后依赖旧行号。

| 编号 | 源码绝对路径 | 需要参考的实现 |
|---|---|---|
| C1 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/protocol/src/protocol.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/protocol/src/protocol.rs:2102) | `TokenUsageInfo`、`TokenUsage`、`BASELINE_TOKENS`、`percent_of_context_window_remaining`；最近用量与累计用量分离 |
| C2 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/protocol/src/openai_models.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/protocol/src/openai_models.rs:374) | 默认有效窗口比例 95%、`resolved_context_window`、独立的 `auto_compact_token_limit` |
| C3 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/session/turn_context.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/session/turn_context.rs:368) | `model_context_window`：解析后的窗口乘模型有效比例 |
| C4 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/session/mod.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/session/mod.rs:4008) | `record_token_usage_info`、`recompute_token_usage`、`send_token_count_event`、`last_token_info_from_rollout`；更新、估算和恢复 |
| C5 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/session/turn.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/session/turn.rs:2540) | `ResponseEvent::Completed` 记录用量；等待当前工具收束后发送 token count，取消也不能丢失已记录用量 |
| C6 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/chatwidget.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/chatwidget.rs:1136) | `set_token_info`、`apply_token_info`、`context_remaining_percent`、`context_used_tokens` |
| C7 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/bottom_pane/footer.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/bottom_pane/footer.rs:999) | `context_window_line`、`single_line_footer_layout`、`right_aligned_x`、`render_context_right`；文案、dim 和左右避让 |
| C8 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/bottom_pane/chat_composer.rs:1426) | `right_footer_line_with_context`、`set_context_window`、`context_window_pending`；特殊模式和重绘 |
| C9 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/chatwidget/status_controls.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/chatwidget/status_controls.rs:368) | 自定义 status line 的窗口选择、`Context N% left`；与默认右侧 footer 区分 |
| C10 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/compact.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/compact.rs:390) | 压缩完成后重新计算上下文用量 |
| C11 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/compact_remote.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/core/src/compact_remote.rs:304) | 远端压缩完成后的相同重算边界 |
| C12 | [D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/chatwidget/tests/status_and_layout.rs](/D:/PycharmProjects/ProxyMind/codex-main/codex-rs/tui/src/chatwidget/tests/status_and_layout.rs:2857) | context remaining、status line 和 footer 布局测试 |

## 现状与口径

客户端的 `TurnTerminalEvent.usage` 保留终态元数据职责；上下文显示只读取独立的
`context.usage.updated` 完整快照。`ContextCompactionEvent` 继续提供压缩 Item 生命周期，
压缩后的 token 占用由终态前的独立用量事件交付。footer 在既有左侧信息之外显示上下文余量。

服务端已有按请求解析窗口和阈值的逻辑，也有 `ContextBudget` 估算结果；可以复用这些实际执行数据。`test_context_observation.py` 覆盖的是诊断摘要，不能据此认定已有面向客户端的用量协议。

Codex 默认 footer 的计算口径：

```text
W = TokenUsageInfo.model_context_window（运行时生效窗口）
U = TokenUsageInfo.last_token_usage.total_tokens
B = 12000

W <= B 时，left = 0
否则：
  available = W - B
  used = max(U - B, 0)
  remaining = max(available - used, 0)
  left = round_half_up(100 * remaining / available)，限制到 0..100
```

这里的 `round_half_up` 对应 Rust 对非负数的 `.round()`。Python 实现不能直接使用具有偶数舍入行为的 `round()`。可以用整数运算 `(200 * remaining + available) // (2 * available)` 得到相同的非负四舍五入结果。

`U` 包含最近模型响应的总 token，不减去缓存命中 token，不再额外叠加已经包含在输出中的 reasoning token。累计用量不参与百分比计算。Codex Core 用于压缩判断的历史增量估算，不应直接替换 footer 所读取的 `last_token_usage`。

**窗口对齐边界：**本清单首先对齐显示算法与生命周期，`W` 取 AppServer 最终执行窗口。Codex 的默认 95% 是运行时预算策略，不能在客户端显示层再乘一次。若后续要求连这项预算策略也一致，必须先共同调整服务端预算和窗口契约，再验证输出预留及压缩阈值；不能把这项策略变化计入单纯 footer 改造。12,000 基线按 Codex 显示语义实现，不作为服务端新增硬性预留。

## 阶段总览

| 阶段 | 交付物 | 前置条件 | 完成标准 |
|---|---|---|---|
| P0 | 明确的快照、事件及显示状态契约 | 源码对照 | 真实值、估算值、窗口和恢复语义无歧义 |
| P1 | 服务端用量采集与持久事件 | P0 | 每次有效模型调用和压缩都能产生可恢复快照 |
| P2 | 客户端解析、状态归约与恢复 | P1 契约固定 | 重放幂等、会话隔离、空闲状态仍可读取 |
| P3 | footer 计算与布局 | P2 | 文案、dim、边界值和窄窗口符合参考行为 |
| P4 | 双端联调与真实终端验收 | P1–P3 | 工具循环、压缩、恢复和多模型路径均有证据 |
| P5 | 文档与发布收口 | P4 | 契约、测试、版本配套，无临时推算路径 |

## P0：固定数据契约与状态所有者

源码参考：C1、C2、C3、C6、C9。

实施位置：

- 服务端配置解析：[D:/PycharmProjects/AppServer/services/llm/llm_config_resolver.py](/D:/PycharmProjects/AppServer/services/llm/llm_config_resolver.py:135)
- 服务端预算类型：[D:/PycharmProjects/AppServer/services/llm/llm_core/llm_contracts/contract_context_budget.py](/D:/PycharmProjects/AppServer/services/llm/llm_core/llm_contracts/contract_context_budget.py:16)
- 服务端事件目录：[D:/PycharmProjects/AppServer/services/contracts/event_catalog.py](/D:/PycharmProjects/AppServer/services/contracts/event_catalog.py)
- 服务端事件信封：[D:/PycharmProjects/AppServer/schemas/mind_events.py](/D:/PycharmProjects/AppServer/schemas/mind_events.py:1264)
- 客户端协议：[D:/PycharmProjects/ProxyMind/protocol/schema/stream_events.py](/D:/PycharmProjects/ProxyMind/protocol/schema/stream_events.py:136)

实施清单：

- [x] 客户端已固定具名、不可变的上下文用量快照。以下字段已进入客户端 schema；服务端尚待配套，不能视为现有线上能力。

| 客户端固定字段 | 语义与校验 |
|---|---|
| `model_context_window` | 服务端最终执行窗口；正整数且大于 1，确实未知时为 null；不得用 HTML 原始值覆盖 |
| `last_token_usage` | 最近有效上下文用量的统一结构；仅包含非负整数 `total_tokens`，未知时为 null |
| `total_token_usage` | 从权威账本获得的累计用量；用于窗口未知时的 `N used` 等展示；不参与百分比 |
| `usage_source` | 最近用量来源，明确区分 `provider`、`estimate`、`unknown`；重算不能伪装成 provider 实报 |
| `model`、`route` | 与快照对应的生效模型和路由，用于防止切模型后误套旧窗口 |

- [x] 快照沿用现有 `cid`、`sid`、`turn_id`、`event_seq`、`presentation_epoch` 信封。`event_seq` 作为已提交快照的先后依据，不引入另一套递增序号。
- [x] 服务端正式目录和信封登记 `context.usage.updated`；客户端按 `context_usage` 嵌套快照严格解析，手动压缩保留 Session 的空 Turn 身份。
- [x] 该事件不创建正文 Item，不是 Turn 终态，也不取得工具、审批、重试或等待状态行的控制权。
- [x] 服务端拥有实际上下文和计数；客户端 agent/application 拥有已确认事实的展示投影；TUI 只消费投影，不在 renderer 中读取配置文件、日志或数据库。
- [x] 定义 `initial`、`pending`、`known`、`unknown` 四种本地展示状态：新会话可显示 100%；恢复未完成时隐藏；已知时显示比例或绝对用量；无可靠值的旧会话不伪装成新会话。
- [x] 确定累计用量缺失时的行为：允许明确未知，禁止把恢复后的局部求和当成完整累计值。压缩重算只替换最近用量，不凭空增加累计计费用量。
- [ ] 确认服务端收窄窗口、输出预留和自动压缩阈值均在原有配置链生效。footer 不修改这些策略，不重复扣输出预留。

验收条件：

- [ ] 用固定输入说明“最新占用 / 累计消耗 / 压缩触发阈值”的区别，保证双端契约测试采用相同样例。
- [x] 对布尔值、浮点数、数字字符串、负数、null、缺失字段分别定义处理，不能靠宽松转换掩盖协议错误。
- [x] 明确哪些属于 Codex 原样行为，哪些属于本系统多 provider 计数或现有窗口策略的差异；未对齐项不得标记为完整数值复刻。

## P1：服务端采集、压缩重算与持久化

源码参考：C4、C5、C10、C11。

实施位置：

- 模型循环：[D:/PycharmProjects/AppServer/services/llm/llm_flows/flow_session.py](/D:/PycharmProjects/AppServer/services/llm/llm_flows/flow_session.py)
- 最终 provider 预算：[D:/PycharmProjects/AppServer/services/llm/llm_core/llm_transports/transport_base.py](/D:/PycharmProjects/AppServer/services/llm/llm_core/llm_transports/transport_base.py:58)
- 压缩和预算收敛：[D:/PycharmProjects/AppServer/services/llm/llm_flows/flow_context_budget.py](/D:/PycharmProjects/AppServer/services/llm/llm_flows/flow_context_budget.py)、[D:/PycharmProjects/AppServer/services/llm/llm_flows/flow_compaction.py](/D:/PycharmProjects/AppServer/services/llm/llm_flows/flow_compaction.py)
- 协议投影：[D:/PycharmProjects/AppServer/services/domain/runtime/contracts/protocol.py](/D:/PycharmProjects/AppServer/services/domain/runtime/contracts/protocol.py)
- 事件缓冲：[D:/PycharmProjects/AppServer/services/llm/llm_flows/flow_event_store.py](/D:/PycharmProjects/AppServer/services/llm/llm_flows/flow_event_store.py)
- 权威事件提交：[D:/PycharmProjects/AppServer/services/domain/runtime/events/event_plane.py](/D:/PycharmProjects/AppServer/services/domain/runtime/events/event_plane.py)
- 事件仓储和恢复：[D:/PycharmProjects/AppServer/services/infra/db/repositories/runtime_event.py](/D:/PycharmProjects/AppServer/services/infra/db/repositories/runtime_event.py)、[D:/PycharmProjects/AppServer/services/domain/runtime/events/event_replay.py](/D:/PycharmProjects/AppServer/services/domain/runtime/events/event_replay.py)

实施清单：

- [ ] 在每次已完成且归属确定的模型响应处取得规范化 usage，而不是只在整个 Turn 的最后一次响应取得。
- [ ] 覆盖 Responses、Chat Completions、Anthropic Messages。缓存读取、缓存写入及 reasoning 的包含关系在 provider adapter 中归一化，避免重复相加。
- [ ] 窗口从该次调用已冻结的配置和预算取得。快照中的窗口、模型和最近用量作为同一版本提交，禁止新窗口配旧用量。
- [ ] provider 没有提供 usage 时，在服务端基于实际上下文和已有估算器生成估算快照；无法可靠估算时明确 unknown。客户端不使用字符数除以常数代算。
- [ ] 重试、推测性响应和工具循环分别明确提交点。丢弃的响应不能覆盖当前占用；已实际消耗但被丢弃的调用是否进入累计值，按权威计费口径处理。
- [ ] 快照与对应上下文提交或恢复 checkpoint 保持一致，并沿现有事件事务、outbox、worker lease 和去重机制持久化，先提交再通知。
- [ ] 检查 `BufferedEventStore` 的缓冲压缩行为，防止新事件被丢掉，也防止把未提交的推测用量提前发布。
- [ ] 对齐 C5 的更新时机：模型响应结束后记录；存在等待用户或工具结果的边界时，在既有收束点发布，避免额外事件破坏暂停语义。取消前保留已完成响应的用量事实。
- [ ] 自动压缩成功后，基于已提交的 replacement 重新估算并发布快照；压缩失败时保留原上下文用量，不把百分比重置为 100%。
- [ ] 手动压缩同样提交快照，并确保客户端在接收压缩终态、关闭 HTTP 流前已经得到该快照。建议在 replacement 提交后、压缩完成事件前交付；事件顺序和崩溃恢复必须有测试。
- [ ] 为最新快照明确持久保留和读取方式：优先使用既有事件仓储的派生查询或现有恢复响应；历史事件裁剪后仍能恢复最新值。若需新增投影，它只能由权威事件重建，不能成为第二个计数权威。
- [ ] 断流、重复投递、重启后读取到相同快照，且不会为刷新 footer 重新调用模型或重新压缩。

验收条件：

- [ ] 两次模型采样产生两个最近用量快照；第二个替换第一个占用值，累计值按约定累加且不因重放重复增加。
- [ ] 服务端将客户端窗口收窄时，事件返回收窄后的值。
- [ ] 大上下文压缩为小上下文后立即得到新的估算值，下一次 provider 实报再替换估算值。
- [ ] 缺失 usage、多模态输入、重试失败、人工审批等待、取消和 worker 重启均有覆盖。

优先扩展现有测试：

- [D:/PycharmProjects/AppServer/tests/test_model_context_policy.py](/D:/PycharmProjects/AppServer/tests/test_model_context_policy.py)
- [D:/PycharmProjects/AppServer/tests/test_context_budget.py](/D:/PycharmProjects/AppServer/tests/test_context_budget.py)
- [D:/PycharmProjects/AppServer/tests/test_conversation_compact.py](/D:/PycharmProjects/AppServer/tests/test_conversation_compact.py)
- [D:/PycharmProjects/AppServer/tests/test_flow_event_store.py](/D:/PycharmProjects/AppServer/tests/test_flow_event_store.py)
- [D:/PycharmProjects/AppServer/tests/test_runtime_event_plane.py](/D:/PycharmProjects/AppServer/tests/test_runtime_event_plane.py)
- [D:/PycharmProjects/AppServer/tests/test_event_replay.py](/D:/PycharmProjects/AppServer/tests/test_event_replay.py)

## P2：客户端类型、会话投影与恢复

源码参考：C4、C6；重点参考最近用量覆盖、累计用量独立和恢复时 pending 的处理。

实施位置：

- 线上解析：[D:/PycharmProjects/ProxyMind/protocol/schema/stream_events.py](/D:/PycharmProjects/ProxyMind/protocol/schema/stream_events.py)
- 流及确认游标：[D:/PycharmProjects/ProxyMind/protocol/client/chat.py](/D:/PycharmProjects/ProxyMind/protocol/client/chat.py)、[D:/PycharmProjects/ProxyMind/agent/adapters/protocol/client.py](/D:/PycharmProjects/ProxyMind/agent/adapters/protocol/client.py)
- 本地事件边界：[D:/PycharmProjects/ProxyMind/agent/protocol/events.py](/D:/PycharmProjects/ProxyMind/agent/protocol/events.py)
- 应用层投影：[D:/PycharmProjects/ProxyMind/agent/application/turns/lifecycle.py](/D:/PycharmProjects/ProxyMind/agent/application/turns/lifecycle.py)、[D:/PycharmProjects/ProxyMind/agent/application/views/contracts.py](/D:/PycharmProjects/ProxyMind/agent/application/views/contracts.py)
- 会话生命周期：[D:/PycharmProjects/ProxyMind/agent/harness/sessions/conversation.py](/D:/PycharmProjects/ProxyMind/agent/harness/sessions/conversation.py)、[D:/PycharmProjects/ProxyMind/agent/harness/sessions/owner.py](/D:/PycharmProjects/ProxyMind/agent/harness/sessions/owner.py)
- 已有持久与恢复入口：[D:/PycharmProjects/ProxyMind/agent/stores/runs/store.py](/D:/PycharmProjects/ProxyMind/agent/stores/runs/store.py)、[D:/PycharmProjects/ProxyMind/agent/stores/sessions/history.py](/D:/PycharmProjects/ProxyMind/agent/stores/sessions/history.py)
- 手动压缩：[D:/PycharmProjects/ProxyMind/protocol/client/compact.py](/D:/PycharmProjects/ProxyMind/protocol/client/compact.py:37)、[D:/PycharmProjects/ProxyMind/agent/application/turns/compact_result.py](/D:/PycharmProjects/ProxyMind/agent/application/turns/compact_result.py)

实施清单：

- [x] 增加正式的类型化上下文用量事件及不可变值，先校验外部 payload，再进入业务层。不要把任意 `usage` 字典直接塞进 TUI。
- [x] 新事件经过既有身份和重放校验，但不生成 Canonical Item、不输出到聊天正文、不改变 Turn 成败。
- [x] 在应用层建立按 `cid + sid` 隔离的快照归约逻辑，使用现有事件确认规则防止重复和旧事件覆盖。不要在 renderer 内另写一套事件排序器。
- [x] 用量投影绑定会话生命周期，跨越单个 `OutputSession`。Turn 结束后保留显示值；真正切换会话时加载目标会话；退出会话时关闭订阅。
- [x] 明确活跃内存投影与本地持久缓存的职责。缓存是远端已确认事实的副本，不能从历史条目数、终态局部 usage 或日志重建缺失权威数据。
- [x] 通过既有应用展示契约提供无 UI 依赖的值；TUI adapter 订阅并更新 footer。优先扩展现有生命周期和组合点，仅在确有独立职责时新增具名模块。
- [x] 实时模式与 replay 都归约数据；replay 期间抑制中间画面，在目标水位确认后一次提交最终 footer 状态。
- [x] 冷恢复先展示 pending，不短暂显示另一会话的值或默认 100%；事件保留缺口通过 P1 的权威快照恢复方案处理，未知保持明确未知。
- [x] Review 使用独立上下文时隔离统计；退出后恢复主会话快照。子代理用量不能覆盖主会话 footer。
- [x] 切换模型或配置后，不把旧用量配到新窗口；新配置尚未执行时保留明确归属的旧快照或进入 pending，按 P0 固定的状态规则处理。
- [ ] fork 按分叉边界恢复或重算上下文，不能复制源会话最新快照冒充目标分支占用。新会话清空上一会话统计。
- [x] 修改手动压缩流的类型和解析入口，使其能接收正式用量事件；用量事件不参与压缩 Item 的终态判断。继续保证交付压缩终态前关闭 HTTP 资源。
- [x] 所有订阅、任务和缓存均有明确关闭或淘汰边界；不为 footer 增加周期 HTTP 轮询。

验收条件：

- [ ] 顺序事件、重复事件、旧事件、跨会话事件、跨模型事件分别有测试。
- [ ] 恢复后与断线前最后一份已提交快照一致，不依赖是否重新生成过模型回答。
- [ ] 关闭单个 Turn 不清空 footer；关闭会话不遗留监听器。
- [ ] CLI、MCP、Subscription 可正常消费新协议事件，不出现 unknown event 错误或额外正文输出。

优先扩展现有测试：

- [D:/PycharmProjects/ProxyMind/tests/protocol/schema/test_stream_event_protocol.py](/D:/PycharmProjects/ProxyMind/tests/protocol/schema/test_stream_event_protocol.py)
- [D:/PycharmProjects/ProxyMind/tests/protocol/client/test_chat_stream.py](/D:/PycharmProjects/ProxyMind/tests/protocol/client/test_chat_stream.py)
- [D:/PycharmProjects/ProxyMind/tests/agent/adapters/protocol/test_session_event_cursors.py](/D:/PycharmProjects/ProxyMind/tests/agent/adapters/protocol/test_session_event_cursors.py)
- [D:/PycharmProjects/ProxyMind/tests/agent/adapters/protocol/test_model_capability.py](/D:/PycharmProjects/ProxyMind/tests/agent/adapters/protocol/test_model_capability.py)
- [D:/PycharmProjects/ProxyMind/tests/integration/test_manual_compaction_protocol.py](/D:/PycharmProjects/ProxyMind/tests/integration/test_manual_compaction_protocol.py)

## P3：计算函数与 footer 渲染

源码参考：C1、C6、C7、C8。C9 是自定义 status line 的另一种入口，不与默认右侧显示混为一套文案。

实施位置：

- TUI 展示入口：[D:/PycharmProjects/ProxyMind/frontends/tui/adapters/presentation.py](/D:/PycharmProjects/ProxyMind/frontends/tui/adapters/presentation.py)
- TUI 会话状态：[D:/PycharmProjects/ProxyMind/frontends/tui/session/state.py](/D:/PycharmProjects/ProxyMind/frontends/tui/session/state.py)
- TUI 运行时：[D:/PycharmProjects/ProxyMind/frontends/tui/core/runtime.py](/D:/PycharmProjects/ProxyMind/frontends/tui/core/runtime.py)
- footer 调用方：[D:/PycharmProjects/ProxyMind/frontends/tui/core/screen.py](/D:/PycharmProjects/ProxyMind/frontends/tui/core/screen.py:2254)
- footer 渲染：[D:/PycharmProjects/ProxyMind/frontends/tui/rendering/screen/surfaces.py](/D:/PycharmProjects/ProxyMind/frontends/tui/rendering/screen/surfaces.py:46)
- 样式解析：[D:/PycharmProjects/ProxyMind/frontends/tui/core/styles.py](/D:/PycharmProjects/ProxyMind/frontends/tui/core/styles.py)

实施清单：

- [x] 将百分比计算实现为纯函数，输入具名快照，输出明确的展示值；复用本文件定义的舍入和边界语义。
- [x] 默认右侧文案为 `N% context left`；窗口未知但累计用量可靠时采用 Codex 的紧凑 `N used` 文案；pending 不生成百分比文本。
- [x] 整段采用相同 dim 样式，不加粗、不高亮数字；通过现有语义样式和终端能力解析器处理颜色，不直接写死 ANSI 序列。
- [x] 按终端显示列宽计算左右间距，正确处理中文、emoji 和宽字符路径，禁止用 Python 字符串长度代替显示宽度。
- [x] 在已有 footer 行内右对齐，预留边距和至少一列左右间隔，不增加输入框高度或改变滚动区域。
- [x] 将 C7 的左右避让规则映射到现有 footer 模式：普通模式在可用宽度内缩短左侧内容；排队模式尝试完整和简短队列提示，仍放不下时隐藏 context，不能牺牲关键操作提示。
- [ ] 历史搜索、退出确认、回看提示、菜单及隐藏模式遵守既有优先级，不能叠字。Shell 模式和未来自定义 status line 也须有唯一右侧内容来源。
- [x] 只有显示值或可用宽度改变时触发必要重绘；不增加逐 token 估算，不在动画 tick 中请求服务端数据。
- [ ] 保留既有品牌、模型、权限和工作区信息，验证其截断次序；不把本功能扩大为完整替换 Codex status line。

计算验收样例：

| 有效窗口 W | 最近用量 U | 预期 |
|---:|---:|---|
| 100000 | 0 | `100% context left` |
| 100000 | 12000 | `100% context left` |
| 100000 | 20000 | `91% context left` |
| 100000 | 99560 | `1% context left`；覆盖恰好 0.5 的舍入 |
| 100000 | 100000 | `0% context left` |
| 100000 | 120000 | `0% context left`，不出现负数 |
| 12000 | 0 | `0% context left`，保持 Codex 小窗口边界语义 |
| 已知窗口 | 最近用量缺失 | 按明确状态处理，不能将缺失自动转换为 0 |
| 未知窗口 | 仅累计用量可靠 | 显示紧凑 `N used`，不显示百分比 |

- [x] 用独立期望值验证公式，不通过复制生产函数计算测试期望。
- [x] 覆盖宽度临界值、中文路径、排队提示、恢复 pending、无色模式、ANSI 16 和 TrueColor。
- [x] 检查最终解析样式属性确实为 dim、非 bold，而不只检查字符串中是否写了 `dim`。

优先扩展现有测试：

- [D:/PycharmProjects/ProxyMind/tests/frontends/tui/rendering/test_tui_input_layout.py](/D:/PycharmProjects/ProxyMind/tests/frontends/tui/rendering/test_tui_input_layout.py)
- [D:/PycharmProjects/ProxyMind/tests/frontends/tui/rendering/test_tui_resize_reflow.py](/D:/PycharmProjects/ProxyMind/tests/frontends/tui/rendering/test_tui_resize_reflow.py)
- [D:/PycharmProjects/ProxyMind/tests/frontends/tui/rendering/test_tui_backgrounds.py](/D:/PycharmProjects/ProxyMind/tests/frontends/tui/rendering/test_tui_backgrounds.py)
- [D:/PycharmProjects/ProxyMind/tests/frontends/tui/features/test_tui_compact.py](/D:/PycharmProjects/ProxyMind/tests/frontends/tui/features/test_tui_compact.py)

## P4：双端联调与真实终端验收

源码参考：C5、C7、C8、C12；同时核对 P1 的持久事件与 P2 的归约结果。

| 场景 | 操作与实施细节 | 验收要求 |
|---|---|---|
| 新会话 | 未发送消息时进入 TUI，随后发送一条短消息 | 首屏状态符合约定，首个可靠快照后切换为计算值 |
| 长对话 | 连续发送多轮消息，保存各次最新及累计用量 | footer 使用最新占用；累计值增长不会让百分比重复扣减 |
| 工具循环 | 同一 Turn 至少发生两次模型采样和一次工具调用 | 不必等整个 Turn 结束才更新；更新不破坏工具等待和审批状态 |
| 自动压缩 | 在隔离测试配置中使用合法的小窗口与阈值触发压缩 | 使用压缩后的上下文估算更新，不强制跳到 100% |
| 手动压缩 | 执行 `/compact`，同时观察 SSE 关闭时机 | 终态前取得最新用量，资源关闭正确，不丢最后一次更新 |
| 压缩失败 | 注入确定的压缩失败 | 旧上下文仍有效，footer 不错误清零；不额外触发模型调用 |
| 服务端收窄窗口 | 客户端配置大窗口，服务端设置较小的同模型上限 | 分母使用服务端最终窗口，不使用客户端原始输入 |
| 模型切换 | 在窗口不同的两个模型间切换 | 不发生旧用量与新窗口混配，下一次快照归属明确 |
| 断线与冷恢复 | 收到快照后断开、重启客户端并恢复 | pending 期间无错误百分比；恢复值与权威快照一致 |
| 重放与事件保留 | 重放重复事件，并模拟历史前缀被裁剪 | 不重复累计；最新快照恢复路径可用，不通过重新提交补统计 |
| 会话与分支 | 切换两会话、执行 fork、进入并退出 Review | 数据互不污染，分支按所选历史边界恢复 |
| 终端布局 | 在真实 TUI 拖动窗口并测试中文路径、输入法和排队输入 | 右对齐、dim、无换行和闪烁，不覆盖输入或关键提示 |
| 缺失 usage | 使用受控 provider 替身，再对可用真实 provider 核对 | 估算或未知状态明确，不伪造 provider 实报 |

- [ ] 每项保存最少可验证证据：脱敏事件、解析后的窗口与 token 值、期望百分比、实际终端画面或 PTY 帧。
- [x] 用固定测试数据核对数学结果，再在真实客户端连接真实服务验证链路；测试替身不能替代真实终端验收。
- [x] 真实场景不得读取日志文字驱动 footer；日志仅用于对照，不记录凭据、正文或完整 provider payload。
- [ ] 无法取得真实画面时将该项保留为未验收，明确已完成的是协议或渲染测试，不标记为完整通过。

## P5：验证命令、文档与发布收口

客户端执行目录：`D:/PycharmProjects/ProxyMind`。先激活仓库虚拟环境，再按实际改动范围运行；以下均是实施后的待执行命令，不是本清单已运行的记录。

```powershell
.\venv\Scripts\Activate.ps1
python -m pytest tests/protocol/schema/test_stream_event_protocol.py tests/protocol/client/test_chat_stream.py tests/agent/adapters/protocol/test_session_event_cursors.py tests/agent/adapters/protocol/test_model_capability.py -q
python -m pytest tests/integration/test_manual_compaction_protocol.py tests/integration/test_context_compaction_presentation.py -q
python -m pytest tests/frontends/tui/rendering/test_tui_input_layout.py tests/frontends/tui/rendering/test_tui_resize_reflow.py tests/frontends/tui/rendering/test_tui_backgrounds.py tests/frontends/tui/features/test_tui_compact.py -q
python -m compileall -q agent protocol frontends infrastructure observability metadata
git diff --check
```

新增纯函数或会话投影测试也必须加入实际测试命令。跨层契约或依赖变化完成后以及发布收口时执行架构审计；仅修改本文档不需要运行：

```powershell
python -m pytest tests/test_package_architecture.py tests/architecture -q
```

服务端执行目录：`D:/PycharmProjects/AppServer`。先阅读该仓库和改动目录的 `AGENTS.md` 并激活其实际虚拟环境，再运行定向测试：

```text
python -m pytest tests/test_model_context_policy.py tests/test_context_budget.py tests/test_conversation_compact.py tests/test_flow_event_store.py tests/test_runtime_event_plane.py tests/test_event_replay.py -q
git diff --check
```

- [x] 同步服务端正式事件目录与 schema、客户端 `protocol/schema` / `protocol/client` 及契约测试。
- [x] 在 [docs/interactive-mode.md](docs/interactive-mode.md) 说明 footer 的估算性质、更新时间、未知状态和压缩后的变化。
- [x] 仅在真实边界或状态所有权变化时更新架构文档；不把调试流水账、未生效提案或兼容猜测写成架构事实。
- [x] 不保留直接读取日志、使用 HTML 原始窗口、按字符数客户端推算、按累计用量扣百分比等临时路径。
- [ ] 确认客户端和服务端版本配套、混合版本行为明确；不为未知服务端字段自行增加别名和宽松回退。
- [ ] 定向测试、跨层审计、编译检查、差异检查和 P4 的真实终端验收分别完成并记录结果。
- [ ] 复核 `backend/` 无改动、用户原有改动未被覆盖，且没有未关闭的测试进程或订阅。
- [ ] 发布前将窗口策略差异明确列出：显示与生命周期已对齐，不等于跨 provider 的 token 数值逐位一致，也不等于已经采用 Codex 的 95% 预算策略。

## 客户端验证范围

| 验证层次 | 结果与限制 |
|---|---|
| 源码对照 | 已核对本地 Codex 的基线公式、紧凑计数格式、footer 布局和更新生命周期；不代表服务端预算策略一致 |
| 协议和测试替身 | 本轮协议、adapter、会话生命周期、CLI、TUI 及手动压缩定向回归 672 项通过；补充后的权威快照恢复文件 12 项通过，覆盖分页、会话隔离、空保留历史、未知和脱敏错误 |
| 架构与静态检查 | 架构审计 138 项通过；编译检查、`git diff --check` 通过；`backend/` 无改动 |
| 扩大回归 | `test_observed_replay_does_not_repeat_completed_client_tool` 存在既有失败：活动批次数实际为 3、期望为 1；使用修改前的事件消费实现亦可复现 |
| 真实服务和终端 | 2026-09-12 已使用 Windows 原生 ConPTY 启动真实 mind.py，连接部署后的 AppServer 与真实 gpt-5.6-sol；窗口为 100000。已核对正常调用、长输入、工具循环、自动和手动压缩、冷恢复及终端 resize 的实际 VT 帧；未声称完成输入法或可见窗口手动拖动验收 |

本轮真机记录（`total_token_usage` 均为服务端明确的 `null`）：

| 场景 | 服务端最近占用与实际 footer |
|---|---|
| 正常调用 | provider 6161 → `100% context left` |
| 长输入 | provider 22218 → `88% context left` |
| 同轮工具循环 | 执行 `123 * 456` 得到 56088；同一 Turn 两次采样分别为 22325、22896，均显示 88% |
| 手动压缩 | estimate 14481 → 97%；空 turn_id，用量 seq 24 先于压缩完成 seq 25；终态后保持显示 |
| 自动压缩 | estimate 20896 → 90%，后续 provider 6295 → 100%；没有把压缩成功直接当成 100% |
| 冷恢复 | 删除隔离验收库中的本地用量缓存后，恢复远端 14481 → 97%；修复首屏默认 100% 闪现，实际帧先隐藏再显示目标值 |
| 最新版本分支与切换 | 源会话手动压缩为 14161 → 98%；`/fork` 成功后保持未知，分支首次 provider 用量 6257 → 100%；通过 `/resume` 切回源会话恢复 98% |
| 最新版本无缓存恢复 | 清除源会话的一条隔离用量缓存，退出并按 SID 冷恢复；可见帧只有 98%，没有默认 100% 或模型事件 |
| 终端宽度与样式 | 原生终端列数 120、70、30、20，prompt_toolkit 可用列数 119、69、29、19；可见时单行右对齐并保留一列边距，19 列隐藏，放宽后恢复；实际解析样式 dim、非 bold |
| 超窗失败收尾 | 压缩后仍超出窗口时显示 0%。原服务端内部失败已产生，但终态提交曾等待 191.4 秒；修复中断监听生命周期后，同样输入无需 Esc，压缩完成到失败终态的客户端间隔为 0.58 秒，数据库间隔为 0.41 秒 |

服务端失败收尾修复经过修改前失败、修改后通过的回归验证；执行、恢复、取消、调度与预算
103 项通过，服务端架构检查 17 项通过。另修复 `/fork` 缺少正式信封造成的 500，分支、
事件存储与回放 85 项通过。客户端最后对实际调整文件运行 112 项定向回归，全部通过。
服务端最终窗口依然是 100000；当前输出预留
32768，因此输入预算为 67232，footer 的显示公式不等于请求准入预算。

尚未完成的真实场景包括不同模型和服务端收窄窗口、受控 provider 缺失 usage、网络断线与
历史保留清理、Review、中文输入法及排队输入；对应单元测试不能替代这些真机项目。

## 最终完成判定

- [ ] P0–P5 的必需项全部完成，待新增字段已成为双端正式契约。
- [ ] 正常调用、工具循环、自动和手动压缩、恢复、切会话均使用同一条权威数据链。
- [ ] footer 在宽窄真实终端中可见、可读且不干扰输入；数学边界与参考实现一致。
- [ ] 缺失数据和模型差异不被包装成确定的百分比；不以仅渲染出 `100% context left` 作为对齐完成依据。
