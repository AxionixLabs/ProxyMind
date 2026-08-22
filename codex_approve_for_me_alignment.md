# Approve for me：现状复核与 Codex 对齐方案

> 复核日期：2026-08-20  
> Codex 基线：`D:\codex-main\codex-rs`
> ProxyMind 基线：当前工作区源码（保留用户已有未提交修改）

本文重新检查 ProxyMind 已有实现后，给出可执行的差异清单。它不把已经存在的能力
重复规划，也不把 Hooks 的 content hash 保护误归入权限 preset。

## 1. 一句话结论

ProxyMind 已有完整的“权限策略 + 人工审批”主链路，以及成熟的菜单栈、原子 TOML
写入、审批串行器、Hook 决策和 turn/subagent 权限传递；当前 **Approve for me** 只是
把 `Ask for approval` 的 `PermissionSettings` 换了一个展示标签，尚未具备 Codex 所需
的独立 reviewer 状态和自动审核路由。

```text
当前：Approve for me = workspace-write + on-request + display_label
目标：Approve for me = workspace-write + on-request + auto_review reviewer
```

## 2. Codex 的精确语义

Codex 的菜单把内置 `auto` preset（workspace-write + on-request）和 reviewer 组合：

| 菜单项 | sandbox/profile | approval policy | reviewer | feature |
| --- | --- | --- | --- | --- |
| Ask for approval | workspace-write / `:workspace` | `on-request` | `user` | 不要求 Guardian |
| Approve for me | workspace-write / `:workspace` | `on-request` | `auto_review` | `guardian_approval=true` |
| Full Access | danger-full-access | `never` | `user` | 不要求 Guardian |

`Approve for me` 不是 `never`，也不是第四个 approval preset。它是同一个 `auto`
preset 的另一种 reviewer 组合。来源：

- `D:\codex-main\codex-rs\utils\approval-presets\src\lib.rs:10`
- `D:\codex-main\codex-rs\tui\src\chatwidget\permission_popups.rs:85`
- `D:\codex-main\codex-rs\tui\src\chatwidget\permission_popups.rs:102`
- `D:\codex-main\codex-rs\protocol\src\config_types.rs:159`

Codex 菜单固定文案：

```text
Ask for approval
Approve for me
Only ask for actions detected as potentially unsafe.
Full Access
Press enter to confirm or esc to go back
```

自动审核请求不是无条件批准：Guardian 返回结构化 `Allow` 或 `Deny`；超时、提示构造
失败、会话失败和解析失败均 fail-closed。审批优先级是：Hook allow/deny，之后按
reviewer 选择 Guardian 或用户。来源：

- `D:\codex-main\codex-rs\core\src\tools\approvals.rs:499`
- `D:\codex-main\codex-rs\core\src\tools\approvals.rs:548`
- `D:\codex-main\codex-rs\core\src\guardian\review.rs:303`
- `D:\codex-main\codex-rs\core\src\guardian\review.rs:444`

## 3. ProxyMind 已有能力复核

### 3.1 已有且可直接复用

| 能力 | 当前实现 | 结论 |
| --- | --- | --- |
| 权限值模型 | `PermissionSettings` 有 `sandbox_mode`、`approval_policy`、`display_label` | 已有两轴策略模型，缺 reviewer 轴 |
| 内置权限 preset | `read-only`、`auto`、`full-access` | 已有 Codex 对应三种 sandbox/policy 组合 |
| 人工审批 | `ApprovalCoordinator` 使用 `asyncio.Lock` 串行请求 | 已有，不需重写 |
| 审批策略 | `never` 直接拒绝；其他策略进入审批协调器 | 已有，不需重写 |
| Hook 审批前置 | Hook allow/deny 优先于人工审批 | 已有，语义与 Codex 顺序一致 |
| 审批一致性 | `ApprovalStore` 校验 approval id、tool、canonical arguments | 已有，继续复用 |
| 当前 turn 权限 | `TurnContext.permissions` 传入 executor、conversation 和 hook scope | 已有 |
| subagent 权限 | 子线程从父线程继承 `parent.permissions` | 已有 |
| 菜单编号/箭头 | `MenuOption` + `TuiMenu` 统一渲染 gutter、当前项和 `(current)` | core 已支持 |
| 菜单 session | `session_id`、generation、`replace_*_if_id` | core 已支持异步陈旧结果防护 |
| 窄宽布局 | `description_layout=STACK_BELOW_WHEN_NARROW`、footer 高度计算 | core 已支持 |
| Full Access 二级确认 | `permissions.py` 已有 child menu 和取消返回 | 已有 |
| 配置原子写入 | `ConfigStore` 使用临时文件、flush、fsync、os.replace | 已有 |
| 配置候选校验 | `ConfigSession.update_user()` 在写入前运行 resolver 校验 | 已有 |
| Hooks content hash | `trust_hook`/`set_hook_enabled` 要求 expected hash 并拒绝 stale 状态 | 已有，但只属于 Hooks |

代码证据：

- `mind_core/permissions.py:22`
- `mind_app/approval/coordinator.py:13`
- `mind_app/approval/policy.py:132`
- `mind_app/runtime/turns/stream.py:951`
- `mind_app/runtime/subagents/thread.py:127`
- `mind_app/tui/core/menu.py:685`
- `mind_core/config_store.py:351`
- `mind_app/controller.py:909`

### 3.2 目前只完成展示，不完成语义

`mind_app/tui/features/permissions.py` 的关键路径是：

```python
if value == "approve-for-me":
    return preset_permissions("auto", display_label="Approve for me")
```

随后 `/permissions` 只做：

```python
self.state.permissions = selected
self.state.apply_prompt_context(self.runtime)
```

这说明：

- 选项文字和成功提示已经存在；
- 下一轮 TUI 模型调用会使用新的 `state.permissions`；
- 当前 `Mind.permissions`、配置文件和其他已存在的 runtime/subagent 快照不会自动同步；
- `display_label` 设置 `compare=False`，因此 Ask/Approve 在值相等比较中完全相同；
- 运行时没有 reviewer 字段，审批请求仍只能进入现有 `InteractionPort.request_approval()`。

对应证据：

- `mind_core/permissions.py:22`
- `mind_core/permissions.py:49`
- `mind_app/tui/features/permissions.py:69`
- `mind_app/tui/session/dispatch.py:192`
- `mind_app/tui/session/loop.py:239`

## 4. 与用户验收项的逐项对应

| 验收项 | 当前状态 | 复核结论 |
| --- | --- | --- |
| 标题、数字、箭头、描述和 footer | 部分满足 | core 会统一渲染编号/箭头；权限 feature 的单测只验证 option 对象，尚未有 Codex 快照级渲染断言 |
| `Ask for approval` 与 `Approve for me` 区分 | 不满足 | 两者都是同一个 `PermissionSettings`，只靠不参与比较的 label 区分 |
| `t` 快捷键 | 有通用钩子 | `TuiMenu` 只有 request 提供 `on_t` 才执行；权限菜单没有 `on_t`，不应擅自绑定 Codex 的其他页面含义 |
| Space/Enter 不误切换 | 基础满足 | disabled option 不会被 Enter 选择；Space 只有显式 `on_space` 才有动作。权限菜单尚未定义 reviewer/不可用状态 |
| 自动持久化 | 权限选择不满足 | `ConfigStore`/`ConfigSession` 已能持久化，但 `/permissions` 当前没有调用它们 |
| content hash 防外部更新 | Hooks 满足，权限不适用 | hash 是 Hook definition 内容保护；权限模式没有内容对象，不能照搬 Hook hash |
| 异步结果受菜单 session 保护 | core 满足，权限应用未接入 | menu generation/session API 已有，权限选择仍是同步返回值，不具备配置提交任务的 session 绑定 |
| 长命令/长路径/窄宽度 | Hooks 菜单已有专项基础 | 权限菜单有窄宽布局基础，但缺少 Approve 专项快照 |
| 不新增第二个 Application | 满足 | 当前路径复用单一 `TuiRuntime`/`TuiMenu` |
| 不把领域逻辑放入 core | 满足 | 权限逻辑在 `tui/features/permissions.py`，core 只提供通用菜单机制 |
| 现有测试通过 | 当前基线通过范围有限 | 权限、审批、运行时已有测试；新增 reviewer 后必须扩大核心主流程覆盖 |

## 5. 需要补齐的最小模型

### 5.1 增加 reviewer，而不是扩展 preset 名称

在 `mind_core.permissions` 增加中性类型：

```python
ApprovalReviewer = typing.Literal["user", "auto_review"]
```

`PermissionSettings` 增加：

```python
approvals_reviewer: ApprovalReviewer = "user"
```

推荐保持 `PermissionPreset` 的三项 preset 不变；用联合模式函数决定 UI label：

```text
(workspace-write, on-request, user)       -> Ask for approval
(workspace-write, on-request, auto_review)-> Approve for me
```

`display_label` 可以继续作为展示覆盖，但不能继续承担 reviewer 语义，也不能参与
权限模式判定。

### 5.2 配置 schema

当前 `mind_core.config` 的 root 字段和字符串字段只有 `sandbox_mode`、
`approval_policy`，没有 `approvals_reviewer`。应增加：

```toml
approvals_reviewer = "user"       # 缺省
approvals_reviewer = "auto_review" # Approve for me
```

要求：

- 新写入只使用 `user` 或 `auto_review`；
- 可选地读取旧别名 `guardian_subagent` 并规范化为 `auto_review`；
- 非法值在 `validate_config_value()` 阶段拒绝；
- `resolve_permissions()` 同时解析 reviewer；
- 本地 `TurnContext`、subagent graph snapshot 和恢复逻辑保留 reviewer；
- 只有远端协议明确支持并负责自动审核时才把 reviewer 加入请求 payload；若自动审核
  由本地审批协调层负责，`mind_nova` payload 继续只传 sandbox/policy。

### 5.3 feature capability

当前 `FeatureSettings` 只有 `js_repl`、`subagents`，不存在 `guardian_approval`。
因此不能像 Codex 一样以 feature 条件显示 Approve for me。

最小实现有两个合法选择：

1. **能力未实现阶段**：隐藏或禁用 `Approve for me`，显示不可用原因；
2. **能力接入阶段**：增加 `guardian_approval` 配置/能力探测，并仅在 reviewer 路由可用
   时显示该项。

不能保留“可选但仍走人工审批”的假完成状态。

## 6. 应复用的现有审批链路

不需要重新创建审批卡、串行锁或 approval store。应在现有协调器上增加 reviewer
分派边界：

```text
工具审批请求
  -> HookRuntime / permission hook
       allow/deny：沿用现有 hook 结果
  -> approval_policy=never：沿用现有 policy decline
  -> reviewer=user：沿用 ApprovalCoordinator -> InteractionPort
  -> reviewer=auto_review：新增自动审核端口
       Allow -> accept
       Deny -> decline + reason
       timeout/parse/session failure -> decline（fail-closed）
```

现有 `ApprovalCoordinator` 是用户交互协调器，不应偷偷把 `approval_source="user"`
改成自动审核。推荐新增显式 reviewer port 或在协调器上增加注入的 reviewer resolver，
以便 telemetry/展示能区分 `user`、`policy`、`hook`、`auto_review`。
当前 `mind_app.presentation.models.ApprovalSource` 只有 `user/hook/policy`，自动审核接入
时必须显式增加新来源，不能冒充 `policy` 或 `user`。

Codex 的自动审核本身已有明确语义可作为协议基线：

- `Allow` / `Deny` 结构化结果；
- 失败关闭；
- 审批来源单独记录为 automated reviewer；
- 拒绝具有 rationale；
- 连续拒绝可触发当轮熔断；
- 拒绝后的人工恢复是单独的 retry/approve-denied-action 流程，不是当前请求隐式升级。

## 7. 权限选择的事务边界

当前菜单选择是本地状态更新，目标应增加一个 controller/application service 入口。
不应在 `tui/core` 或菜单 option callback 内直接写配置。

推荐事务：

```text
菜单返回完整 PermissionSettings
  -> controller 校验 reviewer capability 和 config constraints
  -> 原子写入 sandbox_mode + approval_policy + approvals_reviewer
  -> 重新 resolve 有效配置
  -> 更新 Mind.permissions
  -> 更新 TuiSessionState.permissions 和 footer
  -> 新建/后续 TurnContext 使用新 reviewer
  -> 输出 Permissions updated to ...
```

注意当前架构的实际边界：

- `TuiSessionState` 负责本次 TUI 后续输入使用的权限；
- `Mind.permissions` 负责 controller/runtime 默认权限；
- 已经创建的 `TurnContext` 是不可变快照，不应被菜单选择原地修改；
- 正在等待中的审批请求必须继续使用发起该请求时的 reviewer；新设置从下一明确 turn/context
  边界生效；
- subagent thread 已在创建时继承父权限，切换后不应回写已存在的子线程快照。

`ConfigSession.update_user()` 已有原子写入与 schema 校验，但当前没有：

- expected revision/content hash/CAS；
- 检查用户写入是否被 CLI/profile/更高层配置覆盖；
- 权限选择成功后的统一 live-state 更新。

若产品要求“防止外部配置更新覆盖”，应在权限事务增加配置 revision（例如文件内容
hash + 写入前再次读取）或复用专门的 CAS；不要复用 Hook definition 的 content hash
字段。

## 8. 菜单实现边界

### 8.1 已有 core 能力

`MenuOption` 已支持 `is_current`、`disabled_reason`、`selected_detail`、
`selected_footer_hint`；`MenuRequest` 已支持 `description_layout`、
`show_all_options`、`on_space`、`on_t`；`TuiMenu` 已支持：

- disabled option 不执行 Enter；
- Space/t 只有当前 request 显式提供 callback 才执行；
- session id 和 generation 检查；
- stale view 不替换当前菜单；
- 超过 `VISIBLE_ROWS` 时围绕当前项滚动，`show_all_options=True` 时显示全部。

因此标题不对齐、选项没有编号/箭头、footer 换行等问题，优先修复
`features/permissions.py` 的 request 数据和对应快照测试，不要在 core 添加权限分支。

### 8.2 权限菜单必须提供的字段

每个选项由完整 settings 生成：

```text
value                 = PermissionSettings(..., approvals_reviewer=...)
label                 = Ask for approval / Approve for me / Full Access
detail                = Codex 对齐描述
is_current            = profile + policy + reviewer 联合比较
disabled_reason       = capability/constraint 错误（若不可用）
dismiss_on_select     = 普通模式 true，Full Access 走确认 child
```

当前所有 `is_current=False` 的实现必须改为联合状态判定；否则菜单无法表达用户当前
正在使用自动 reviewer。

## 9. 实施顺序

### 阶段 A：只修正数据语义

1. 增加 `ApprovalReviewer`、配置字段和规范化；
2. 修改 `PermissionSettings`、preset 构造和 label 判定；
3. 更新本地 turn snapshot、subagent graph 序列化；仅在远端协议确有 reviewer 字段时
   更新 payload；
4. 让 Ask/Approve 的 equality、恢复和 current 判定可区分。

此阶段不显示可用但未接通的自动审核。

### 阶段 B：接通权限持久化和 live state

1. 在 controller/application service 统一应用权限选择；
2. 原子更新三项配置并处理约束覆盖；
3. 更新 controller、TUI session、footer 和下一轮 turn 默认值；
4. 只在事务成功后写 history/status。

### 阶段 C：接通自动 reviewer

1. 定义自动审核 port 和 `Allow/Deny/Failed` 结果；
2. 接入现有审批请求分派，保留 Hook 和 `never` 优先级；
3. 记录 reviewer/source/rationale；
4. 实现失败关闭、取消和拒绝恢复边界；
5. 增加 capability，菜单按能力显示/禁用。

### 阶段 D：视觉和回归

1. 增加权限菜单渲染快照：标题、编号、箭头、列对齐、footer；
2. 增加窄宽、长描述、长 disabled reason 测试；
3. 增加 current 切换和 feature disabled 测试；
4. 运行现有 permissions、approval、turn execution、subagent、menu 测试。

## 10. 验收清单（按当前复核重写）

### 已满足，可作为回归保护

- [x] 单一 TUI Application、对象菜单栈和 core/features 分层。
- [x] Enter 不选择 disabled option；Space/t 无 callback 时不产生领域动作。
- [x] 菜单 session/generation API 可拒绝陈旧异步更新。
- [x] Full Access 有独立确认 child 和 Esc 返回。
- [x] `never` 策略拒绝审批请求。
- [x] Hook allow/deny 在人工审批前生效。
- [x] 审批 ID、工具名和 canonical arguments 有一致性校验。
- [x] 配置文件采用临时文件 + fsync + 原子替换。
- [x] Hooks trust/enable 修改已有 content hash 防外部内容替换。

### 部分满足，需要补测试或接入

- [ ] Codex 标题、编号、箭头、列顺序、描述和 footer 的权限快照。
- [ ] 权限菜单的唯一 `(current)` 状态。
- [ ] 窄宽度下权限描述和 footer 不破坏布局。
- [ ] 权限选择写入 ConfigSession 后刷新当前 controller/runtime。
- [ ] profile/CLI 覆盖导致“写入成功但未生效”的显式反馈。
- [ ] 菜单异步应用任务绑定 menu session。

### 明确缺失，不能以 UI 标签替代

- [ ] `PermissionSettings.approvals_reviewer` 独立字段。
- [ ] `approvals_reviewer` 配置 schema、解析、持久化和恢复。
- [ ] `guardian_approval` capability/requirements 语义。
- [ ] 自动 reviewer port 和审批路由。
- [ ] 自动审核 Allow/Deny、rationale、失败关闭和取消语义。
- [ ] 自动 reviewer source 的审批 telemetry/历史展示。
- [ ] 自动 reviewer 拒绝后的独立人工恢复流程（若产品需要）。

## 11. 必须新增的核心测试

1. `tests/test_permissions.py`
   - user/auto_review 解析和规范化；
   - Ask/Approve equality、label、current 判定；
   - turn snapshot 和 subagent graph 保留 reviewer；
   - reviewer 为本地路由时，payload 不错误扩散该字段。
2. `tests/test_tui_permissions.py`
   - feature 可用时显示 Approve，不可用时禁用/隐藏；
   - Codex 文案、编号、箭头和 footer 快照；
   - Ask ↔ Approve 切换不改变 workspace-write/on-request；
   - 选择 Full Access 仍只进入确认 child。
3. 新增权限应用服务测试
   - 原子写入成功后同步 controller/session；
   - 写入失败不提前更新 footer/history；
   - 外部 revision 变化时拒绝覆盖并重新读取。
4. 审批路由测试
   - Hook allow/deny 优先；
   - `never` 不调用 reviewer；
   - user 调用现有 `InteractionPort`；
   - auto_review 调用自动 reviewer；
   - Allow、Deny、timeout、parse/session failure 的结果与 source 正确。
5. 运行回归
   - `tests/test_approval_coordinator.py`
   - `tests/test_permissions.py`
   - `tests/test_tui_permissions.py`
   - 相关 `tests/test_run_result.py`
   - 相关 TUI menu、subagent、turn execution 测试

## 12. 完成定义

只有以下链路全部成立，才能称为“实现了 Approve for me”，而不是“显示了
Approve for me”：

```text
菜单模式
  -> 独立 reviewer 状态
  -> 配置可恢复且受约束校验
  -> controller/session/下一轮 turn 同步
  -> 审批请求真实路由到自动 reviewer
  -> 自动 reviewer 失败关闭并可追踪来源
  -> 当前菜单和 footer 正确反映状态
  -> 核心回归测试通过
```
