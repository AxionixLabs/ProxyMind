# ProxyMind 与 codex-main 权限语义核对清单

目标：逐项核对 ProxyMind 的权限组合、MCP 审批和会话运行时行为是否与根目录 `codex-main` 当前源码一致，并用本地 TTY 完成最终验收。

审计边界：ProxyMind 当前对外提供 Read Only、Ask for approval、Approve for me、Full Access 四个预设；MCP 工具支持 `auto`、`prompt`、`writes`、`approve` 模式。`codex-main` 的 Granular 权限配置和 MCP elicitation 策略在 ProxyMind 协议层尚未接入运行时，因此作为明确的未对齐项保留，不能以现有预设的通过结果代替。

## 对照源码

- [codex-main MCP 自动审批规则](codex-main/codex-rs/codex-mcp/src/mcp/mod.rs#L87-L107)
- [codex-main MCP 审批组合测试](codex-main/codex-rs/codex-mcp/src/mcp/mod_tests.rs#L50-L130)
- [codex-main MCP 工具审批模式](codex-main/codex-rs/config/src/mcp_types.rs#L20-L42)
- [ProxyMind 权限设置](agent/domain/policies.py)
- [ProxyMind MCP 审批入口](agent/application/approvals/mcp.py)

## 核对矩阵

| 编号 | 范围 | codex-main 语义 | ProxyMind 当前状态 | 验收 |
| --- | --- | --- | --- | --- |
| P1 | `approval_policy=never` + unrestricted Full Access | MCP 审批自动通过 | 已通过 | 单测 + TTY |
| P2 | `approval_policy=never` + 受限权限 | 需要审批的 MCP 失败关闭 | 已通过 | 单测 + TTY |
| P3 | `approval_policy=on-request` + MCP `prompt/auto/writes` | 按工具策略请求审批 | 已通过 | 单测 |
| P4 | 任意全局策略 + MCP `approve` | 自动通过，不显示审批卡 | 已通过 | 单测 |
| P5 | MCP `writes` + `readOnlyHint=true` | 自动通过 | 已通过 | 单测 |
| P6 | MCP `writes` + 非只读或未知 | 需要审批 | 已通过 | 单测 |
| P7 | MCP `auto` + 只读注解 | 自动通过 | 已通过 | 单测 |
| P8 | MCP `auto` + 非只读且非安全注解 | 需要审批 | 已通过 | 单测 |
| P9 | MCP 审批拒绝 | 不执行外部 MCP 调用并返回失败结果 | 已通过 | 单测 |
| P10 | Full Access 预设 | `danger-full-access + never`，标签和有效权限一致 | 已通过 | TTY |
| P11 | Read Only 预设 | `read-only + on-request` | 已通过 | TTY |
| P12 | Ask for approval 预设 | `workspace-write + on-request` | 已通过 | TTY |
| P13 | Auto review 预设 | 审批策略和 reviewer 独立传递 | 已通过 | 单测 |
| P14 | `never` + MCP elicitation | 按 codex-main 的 elicitation 拒绝规则处理 | 未对齐：运行时未接入 elicitation 策略 | 待实现 |
| P15 | 权限切换后新 turn | 新 turn 使用最新权限，不复用旧权限 | 已通过 | 单测 + TTY |
| P16 | 会话恢复 | 恢复后权限与 codex-main 的持久化语义一致 | 部分通过：字符串权限已持久化；Granular 未接入 | 单测 + TTY |

## TTY 验收

- [x] 启动 TUI，确认权限 footer 显示当前预设。
- [x] 切换到 Full Access，确认浏览器 MCP `browser_navigate` 不出现审批卡并能返回工具结果。
- [x] 切换到 Ask for approval，确认同一浏览器 MCP 调用出现审批卡，批准后执行。
- [x] 在 Ask for approval 下拒绝调用，确认外部 MCP 服务未收到调用且 turn 正常收敛。
- [x] 切换到 Read Only，确认需要审批的 MCP 调用失败关闭且界面不崩溃。
- [x] 取消审批，确认同批后续调用不会触发 `KeyError` 或残留审批。
- [x] `/resume` 后复核 footer、有效权限和上述行为。

## 结论记录

- [x] P1–P13、P15 有源码依据和测试证据；P14、P16 的 Granular 部分已明确记录为未实现，不伪装成已对齐。
- [x] 已覆盖的 TTY 验收项全部通过：启动、权限切换、Full Access 直通、Ask for approval 批准与拒绝、Read Only、取消审批、`/resume`。
- [x] 未引入与 codex-main 不一致的权限别名、隐式回退或第二套权限状态。

## TTY 观察

- MCP 启动期间首个请求可能早于 `External MCP ready`，此时模型会报告工具尚未接入；等待 `1/1 servers · 24 tools` 后重试即可正常调用。这是启动时序问题，不是权限判定偏差，后续应单独治理。

## 审批语义回归入口

- 域层全矩阵：[test_mcp_semantics_matrix.py](tests/agent/domain/approvals/test_mcp_semantics_matrix.py)，覆盖四种 MCP 模式与 27 种注解组合，并锁定风险投影优先级。
- 集成层矩阵：[test_mcp_approval_semantics_matrix.py](tests/integration/test_mcp_approval_semantics_matrix.py)，覆盖 Full Access、受限 `never`、`on-request`、批准/拒绝、只读注解和活动生命周期。
- 既有生命周期与故障回归继续由 `tests/agent/application/approvals/`、`tests/integration/test_mcp_approval_gate.py`、`tests/integration/test_turn_fault_injection.py` 和 TTY/PTY acceptance 测试负责。
- 标准门槛：`venv\Scripts\python.exe -m pytest tests/agent/domain/approvals/test_mcp_semantics_matrix.py tests/integration/test_mcp_approval_semantics_matrix.py tests/integration/test_mcp_approval_gate.py tests/integration/test_turn_fault_injection.py -q`。
- 本次新增矩阵已验证：122 passed；后续 P14/P16 每个阶段必须先通过该门槛，再进行 TTY 验收。
