# `/review` 对齐 Codex 分阶段计划

## 目标与完成定义

本计划以仓库内 `codex-main` 源码和本机 `codex` 的真实 PTY 行为为双重基线，完整对齐
ProxyMind `/review` 的菜单、目标解析、执行生命周期、工具调用、状态动画、结果展示、中断、
恢复和 AppServer 协议。源码用于解释行为，PTY 结果用于验收行为；两者不一致时先记录差异，
不得凭印象选择实现。

只有同时满足以下条件才算完成：

- 四种 Review target 都能从真实 TUI 菜单进入并完成，不以 patch 非空作为启动前提。
- 选择当前所在的 `main` 作为 base branch 时，即使 merge diff 为空也必须开始 Review。
- Review 期间展示 Codex 同构的 `Working` / `Thinking` 动画和真实 `Ran ...` 工具轨迹。
- 模型生成的中间结构化 JSON 不直接显示；最终结果只来自权威 Review 终态事件。
- Ctrl-C 能中断远端 Review，先收敛 Review 退出事件，再恢复普通输入和展示中断提示。
- Mind 和 AppServer 的正式 schema、OpenAPI、持久化、attach/replay 与终态顺序一致。
- 在 Windows ConPTY 和至少一个 POSIX PTY 上通过真实进程验收，并保留可复查证据。

## 权威基线

- Codex 菜单：`codex-main/codex-rs/tui/src/chatwidget/review_popups.rs`
- Codex 通用选择器：`codex-main/codex-rs/tui/src/bottom_pane/list_selection_view.rs`
- Codex target prompt：`codex-main/codex-rs/prompts/src/review_request.rs`
- Codex Review 上下文：`codex-main/codex-rs/core/src/session/review.rs`
- Codex Review 事件转发与退出：`codex-main/codex-rs/core/src/tasks/review.rs`
- Codex TUI 进入/退出展示：`codex-main/codex-rs/tui/src/chatwidget.rs`
- ProxyMind 客户端架构：`ARCHITECTURE.md`
- Mind/AppServer Authority：`ARCHITECTURE_SYSTEM.md`
- AppServer 正式契约：`services/llm/PROTOCOL.md`、OpenAPI 和对应 schema

禁止把截图文本、服务端日志或旧实现当成独立契约。它们只能作为发现差异和验证契约的证据。

## 目标行为模型

```text
/review
  -> 打开四项 preset 菜单
  -> 可选 branch / commit / custom 子视图
  -> 形成类型化 ReviewTarget 和 Codex 同构 target prompt
  -> 在首次网络操作前持久化本地 SubmitReviewCommand
  -> AppServer 原子登记 Review Turn 和 Review Item
  -> 客户端进入独立、只读、never-approval 的 Review 执行上下文
  -> 标准事件链驱动 Thinking / Working、工具调用和工具结果回传
  -> 隐藏 reviewer 的原始 assistant JSON
  -> review.completed / failed / cancelled
  -> turn.completed 释放执行门
  -> 退出 Review 模式并恢复普通 composer
```

Review 不是一条带“Reviewing ...”文案的普通聊天，也不是客户端先生成完整 patch 再让服务端
总结。它是拥有独立上下文和终态的只读模型轮次，但复用普通 Turn 已有的活动、工具、传输、
中断和展示基础设施。

## 四种 Target 的对齐语义

| Target | Codex prompt / 行为 | 必须允许的仓库状态 | PTY 必验工具事实 |
| --- | --- | --- | --- |
| Base branch | 解析 merge base；提示 reviewer 运行 `git diff <merge-base>` | 当前分支等于所选分支、空 diff、非空 diff | 选择 `main` 后确实进入 Review；工具轨迹包含 merge-base 对比或等价只读检查 |
| Uncommitted changes | 审查 staged、unstaged、untracked | 干净工作区、任意一种或混合改动 | 先出现 `git status --short` 或等价检查；非空时继续读取 diff/文件 |
| Commit | 审查指定 SHA 引入的改动，标题只用于提示 | root commit、普通 commit、empty commit | 工具轨迹明确检查所选 SHA，而不是当前工作区 patch |
| Custom | 原样使用去除首尾空白后的用户指令 | 干净或有改动 | 工具行为服从自定义范围，空指令不提交 |

`workspace.patch/files` 不再作为前三种 target 的启动门禁。若协议保留 workspace revision，空
workspace 也必须使用规范 revision 并通过校验；本地 Git 实际内容由只读工具在 Review Turn 中
观察。Base branch 的 merge-base 是客户端 Git authority 产生的冻结派生事实，必须进入正式请求
字段或正式 target resolution，不能塞进 UI 文案、metadata 私货或兼容别名。

## 菜单与交互规格

### Preset 菜单

顺序和文本固定为：

1. `Review against a base branch`，描述为 `(PR Style)`
2. `Review uncommitted changes`
3. `Review a commit`
4. `Custom review instructions`

标题为 `Select a review preset`。Enter 接受当前项；Esc 返回上一层或关闭根菜单；子视图取消
不得误关闭父视图；子视图接受后关闭整组 Review 菜单。

### Branch、Commit 与 Custom 子视图

- Branch 标题为 `Select a base branch`，搜索占位为 `Type to search branches`。
- Branch 行显示 `<current> -> <candidate>`，搜索值只使用 candidate branch。
- Commit 标题为 `Select a commit to review`，搜索占位为 `Type to search commits`。
- Commit 最多加载最近 100 条，显示 subject，搜索同时匹配 subject 和 SHA。
- Custom 标题为 `Custom review instructions`，占位为 `Type instructions and press Enter`。
- 异步 branch/commit 查询结果只能更新发起它的 menu session；关闭或换层后的迟到结果丢弃。

### 布局、滚动与样式

- 标题与搜索行之间、搜索行与候选列表之间、候选列表与 footer 之间的空行以 Codex PTY
  Screen 为准，不允许通过删除空行压缩高度。
- 可搜索菜单不显示数字前缀；选择箭头为 `›`，箭头和选中正文使用正常高亮色，不能继承 dim。
- 标题、占位文字、未选中项、选中项、箭头和 footer 分别做 terminal cell 样式断言。
- 滚动阈值由当前终端高度、header/search/footer 和实际换行后的候选 viewport 共同计算，禁止
  写死“第 8 项”。选择箭头到达当前 viewport 的最后一项之前不得提前滚动；越过边界时仅移动
  保持选中项可见所需的最小行数。
- 使用 7、8、9、12、100 条 commit，以及 20/28/40 行终端覆盖边界；长中英文 subject 和
  80/100/160 列终端覆盖截断与换行。
- 输入搜索、Backspace、上下箭头、PageUp/PageDown、Enter、Esc、Ctrl-C 和粘贴均在真实 PTY
  中验收，过滤后 selection 与 scroll offset 必须重新规范化。

## Review 运行时与展示规格

- 进入事件展示精确文本 `>> Code review started: {hint} <<`，采用 Codex review status 的强调色，
  不展示 `• Reviewing ...` 占位行。
- 选择完成后立即进入前台 Turn 生命周期；首个远端事件等待期间也必须显示 `Working` 动画。
- `turn.thinking`、provider retry、tool batch 和 tool result 必须交给普通 Turn 的活动投影器，不能
  被 Review 专用循环静默丢弃。
- Reviewer 只得到明确 allowlist 的本地只读工具。至少覆盖 shell/exec 及其会话轮询；禁止
  `apply_patch`、写文件、权限升级、web search、view image、goals、collaboration 和 subagent。
- Review context 固定 `sandbox_mode=read-only`、`approval_policy=never`；任何工具声明
  `readOnlyHint=true` 前必须由真实能力策略证明只读，禁止给任意工具伪造该 annotation。
- 工具开始、增量输出和完成使用现有 TUI tool presentation，产生 Codex 同构的 `• Ran ...`、
  折叠摘要和 `ctrl + t to view transcript` 行为。
- Reviewer 的 assistant delta、assistant item completed 和原始 Review JSON 不上屏；思考与工具
  事件仍正常转发。最终正文只从 `review.completed.output` 解析并展示。
- 如服务端发布会话标题更新，沿用普通 Turn 的 `Session renamed ...` 展示和 resume 坐标，不在
  Review 层重新猜标题。
- 退出事件展示精确文本 `<< Code review finished >>`，然后展示结构化 findings 或无发现结论。
- Ctrl-C 的顺序固定为：发送远端 interrupt -> Review child 收敛 -> 展示 finished -> 展示
  `Review was interrupted. Please re-run /review and wait for it to complete.` -> 展示通用 conversation
  interrupted 提示 -> 恢复 composer。中断期间不得创建第二个 Review 身份。

## AppServer 协议与持久化规格

- 协议标识继续使用 `mind-review/1`，不以新增 v2 回避当前契约问题。
- `execution.llm_conf` 只能包含正式 wire 字段；客户端本地 provider 的 `name/kind/enabled` 以及
  顶层 `hosted_tools` 配置不得泄漏到请求，四种 target 都不得再触发 422 extra-forbidden。
- `/mind-review` 接受带只读 client tools 的规范空 workspace；四种 target 的空 patch/files 都按
  相同 revision 规则校验，不再只给 custom 特例。
- AppServer 根据正式 target resolution 构造 Codex 同构 prompt，不把空 snapshot 描述成待审查
  的权威代码内容，也不要求模型凭空推断另一 target。
- `ReviewOutput` 始终严格校验。使用工具观察仓库时，finding path 校验规范相对路径；若请求携带
  明确文件集合，再额外限制到该集合，不能把“空 snapshot”误判成所有路径非法。
- 事件顺序保持 `turn.started -> review.started -> thinking/tool* -> review terminal -> turn.completed`。
  Review Item 终态不能代替 Turn 终态；客户端断线后 Worker 可继续，恢复只能 status + attach/replay。
- 同一 request fingerprint 的重试返回 idempotent；提交结果未知时不得换 request/turn 身份重投。
- 更新双方 schema、OpenAPI、协议文档、迁移或约束测试；删除旧的 empty-diff 门禁和被替代的
  snapshot-only prompt，不保留专用 fallback、字段别名或静默 `pop`。

## PTY 对照验收方法

### Oracle 采集

- 使用 `Get-Command codex` 解析本机真实 Codex 入口，在一次性 Git fixture 中通过原生
  PTY/ConPTY 启动，不修改 `codex-main`。
- 对每个场景保存按键时间线、原始 ANSI 字节、`pyte` Screen、终端尺寸、Git fixture 描述和
  规范化截图。session id、绝对路径、版本、计时和模型自由文本在比较前规范化。
- 菜单和固定 lifecycle 文本做逐 cell 比较；模型思考内容不做字面比较，只断言事件顺序、状态、
  工具目标和可见性。

### Mind 真实 PTY

- 复用 `tests.pty.spawn_terminal` 启动仓库真实 TUI 进程，不直接调用 menu renderer 或 handler。
- 每个按键都通过 PTY 输入：输入 `/review`、上下移动、搜索、Enter、Esc、Ctrl-C；不得直接修改
  Python menu state 来伪造交互。
- 每个阶段的确定性验收使用真实 TUI + 可控协议场景，验证屏幕与 wire 请求；最终验收使用真实
  AppServer、PostgreSQL、provider 和客户端工具闭环。
- 失败时保存 raw output、screen dump、请求/事件时间线和服务端 trace id；成功产物不得包含
  token、密码、Authorization header 或未脱敏的用户配置。

### 最终场景矩阵

| 场景 | Codex PTY | Mind PTY | 核心断言 |
| --- | --- | --- | --- |
| Preset 打开/取消 | 必须 | 必须 | 四项顺序、空行、footer、Esc 返回 |
| Branch 搜索与 `main` | 必须 | 必须 | 当前分支行、搜索、干净 `main` 仍启动 |
| Commit 7/8/9/100 条 | 必须 | 必须 | 动态 viewport、末行滚动阈值、箭头非 dim |
| Commit 搜索与接受 | 必须 | 必须 | subject/SHA 搜索、提交身份正确 |
| Custom 空/非空 | 必须 | 必须 | 空值不提交、非空原样进入 Review |
| Uncommitted 混合改动 | 必须 | 必须 | staged/unstaged/untracked 均被工具检查 |
| Thinking + 多次工具调用 | 必须 | 必须 | `Working` 持续、`Ran ...` 顺序、JSON 隐藏 |
| 正常完成 | 必须 | 必须 | started/finished/结果/普通 composer 顺序 |
| Ctrl-C 中断 | 必须 | 必须 | 远端取消、精确中断提示、无重复身份 |
| 断线 attach/replay | 行为参考 | 必须 | 不重提交流程、不重复工具副作用、终态一致 |
| 80x20 与 160x40 resize | 必须 | 必须 | 无重叠、选择与滚动稳定、动画不破坏菜单 |

## 分阶段实施清单

### Phase 0：冻结 Codex Oracle 与差异账本

- [ ] 建立不污染用户仓库的一次性 Git fixture 生成器，覆盖干净 main、混合工作区、多个分支、
  root/普通/empty commit 和 7/8/9/100 条 commit。
- [ ] 用本机 Codex 的真实 PTY 采集菜单、滚动、搜索、四 target、正常完成和 Ctrl-C 的基线。
- [ ] 记录 Codex commit、Codex 版本、终端尺寸和规范化规则，形成可复跑证据索引。
- [ ] 建立逐项差异账本；既有 ProxyMind 修复只记录为“候选”，不提前标记通过。

阶段门禁：Oracle 证据可复现；fixture 前后 `git status --short` 一致；PTY 退出后光标、bracketed
paste、focus reporting 和 synchronized output 全部恢复。通过后单独提交并推送测试基础设施。

### Phase 1：收拢通用菜单架构

- [ ] 让 Review preset/branch/commit/custom 只组装 `MenuRequest`，选择、过滤、viewport、样式和
  view stack 全部由通用 menu owner 管理。
- [ ] 对齐固定文本、空行、footer、箭头和 dim/bright 语义。
- [ ] 以渲染高度计算最后一个可见候选和滚动阈值，删除任何绝对“第 8 项”逻辑。
- [ ] 收敛异步 catalog generation 和父子菜单接受/取消语义。
- [ ] 增加逻辑帧测试和真实 PTY 7/8/9/100 条 commit 矩阵。

阶段门禁：菜单 PTY Screen 与 Codex 规范化基线一致；全部菜单定向测试通过；`git diff --check`
通过。复核通过后提交并推送 ProxyMind。

### Phase 2：正式化 target resolution 和跨端协议

- [ ] 定义四种 target 的唯一 prompt resolution；Base branch 冻结 merge-base 成正式派生事实。
- [ ] 移除客户端非 custom empty-diff 拒绝和 snapshot-only 执行语义。
- [ ] 让规范空 workspace + 只读工具成为四种 target 的合法请求。
- [ ] 修正 `llm_conf`、tools annotations、输出路径校验和服务端 prompt。
- [ ] 更新 ProxyMind/AppServer schema、契约文档、OpenAPI 和请求指纹测试，不升级协议标识。

阶段门禁：四 target 请求 fixtures 在双方独立解析结果一致；真实 AppServer 对四请求均返回 accepted
或 idempotent，日志无 422；AppServer 定向测试、OpenAPI diff 和 ProxyMind 协议测试通过。复核
通过后分别提交并推送 AppServer 与 ProxyMind。

### Phase 3：复用标准 Turn 工具与活动生命周期

- [ ] 用一个 Review stream source 接入标准 Turn event pump，不复制第二套工具状态机。
- [ ] 建立独立 Review execution context，固定 read-only + never，并过滤出经过证明的只读工具。
- [ ] 接通 `turn.thinking`、retry、tool batch、tool result delivery、effect reconciliation 和 cleanup。
- [ ] 首个事件之前启动 Working 动画；工具执行期间切换状态，完成和异常时幂等停止。
- [ ] 保持 Review 原始 assistant JSON 静默，只转发思考、工具和 lifecycle 事件。

阶段门禁：真实 PTY 中出现持续更新的 Working/Thinking 和实际 `Ran git ...`；Review 前后工作树内容
哈希一致；普通 Turn 全量回归无变化；取消、工具失败和 transport failure 不泄漏动画/PTY/session。
复核通过后提交并推送 ProxyMind。

### Phase 4：完成、标题与中断展示

- [ ] 对齐 started/finished 的文本、顺序、颜色和空行。
- [ ] 只从 active Review Item 输出 findings 和 overall result，验证 raw JSON 从未进入可见 transcript。
- [ ] 复用正式 session title 事件展示 `Session renamed ...` 和 resume 坐标。
- [ ] 对齐 Ctrl-C 的远端 interrupt、Review 退出、通用中断提示和 composer 恢复顺序。
- [ ] 覆盖完成、无发现、结构化输出失败、服务端失败、cancelled 和 reconciliation_required。

阶段门禁：正常完成和 Ctrl-C 两条真实 PTY transcript 与 Codex 固定部分一致；事件日志证明
`review terminal` 先于匹配的 `turn.completed`；恢复输入后可立即执行下一条普通消息。复核通过后
提交并推送双方受影响仓库。

### Phase 5：持久恢复和故障矩阵

- [ ] 覆盖 POST 成功但响应丢失、attach 超时、客户端退出、Worker 重启和 terminal replay。
- [ ] 恢复时用冻结请求中的工具名重建同一只读能力集合，不增加新工具。
- [ ] 历史事件只归约不重做本地工具副作用；replay 水位之后的事件继续正常执行。
- [ ] 中断和重连竞争最终只产生一个 Review terminal 和一个 Turn terminal。
- [ ] 清理旧 Review consumer、旧 snapshot-only 分支和不再可达的错误码/文档。

阶段门禁：durable/fault 定向矩阵通过；真实 PTY 强制断线后能够恢复到唯一终态；数据库核对无
重复 request/turn/review item/tool result。复核通过后提交并推送双方仓库。

### Phase 6：最终双实现 PTY 验收与发布收口

- [ ] 在相同 Git fixtures、终端尺寸和按键时间线上重跑 Codex 与 Mind 全部场景矩阵。
- [ ] 对固定 UI 做规范化 Screen/cell diff，对动态模型阶段做事件和工具事实 diff。
- [ ] Windows ConPTY 全矩阵通过；POSIX PTY 核心矩阵通过。
- [ ] 运行受影响测试、架构审计、compileall、OpenAPI 生成检查和 `git diff --check`。
- [ ] 更新 `ARCHITECTURE.md`、`ARCHITECTURE_SYSTEM.md` 和 AppServer `PROTOCOL.md` 为最终事实。
- [ ] 删除临时采集脚本、未脱敏日志和 smoke 文件，只保留可维护的自动验收与必要 fixture。
- [ ] 逐项关闭差异账本；所有未完成项必须有明确 blocker，不能以“基本一致”关闭。

最终门禁：本清单全部勾选，两个仓库工作树仅含用户原有改动，远端分支包含每个已验收阶段的
独立提交，真实端到端运行不再出现 `request validation failed`、静态 `Reviewing ...` 占位或缺失
Working/Thinking 的状态。

## 每阶段固定复核与推送规则

1. 开始前记录两个仓库的 `git status --short` 和 HEAD，保留用户改动。
2. 先跑最小定向测试，再跑该阶段声明的 PTY/协议/故障门禁。
3. 对照 Codex 源码和本阶段 Oracle 证据人工复核，不以测试数量代替行为核对。
4. 更新本清单的实际结果、命令、artifact 索引和剩余差异；不得预先批量勾选。
5. `python -m compileall ...` 和 `git diff --check` 通过后，只暂存本阶段文件。
6. 提交后检查 `git show --stat`，确认未夹带用户文件；随后立即推送当前阶段。
7. 推送失败时保留提交并解决传输问题，不继续把多个未验收阶段堆到同一提交。

当前未跟踪的 `TUI_INLINE_VIEWPORT_REFACTORING_CHECKLIST.md` 属于用户工作树，本计划和后续 Review
实现均不得擅自暂存、修改或删除它。
