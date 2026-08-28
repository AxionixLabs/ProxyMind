# Agent Runtime 迁移计划

状态：阶段 0 已完成；阶段 1 进行中（2026-08-28）

这份计划配合 [Agent Runtime 架构基线](AGENT_RUNTIME_ARCHITECTURE.md) 使用。
它把从历史包到 `agent` bounded context 的改造拆成可回滚阶段；每一阶段都必须
有代码、测试和导入边界证据，不能以“目录已经移动”作为完成标准。

## 范围与状态权威

- 本计划只管理 ProxyMind 客户端内部 Agent Runtime 迁移。
- 阶段号只在本文档内有效，不与任何外部服务计划共用进度。
- 架构拆分、协议兼容或生命周期所有权收敛只是准备性工作；未满足
  本阶段退出条件时，不得标记为该阶段完成，也不得计入后续阶段。
- 每次状态变更必须在本文档记录日期、实现证据、测试结果和未决风险。

## 迁移原则

- 先建立运行契约，再迁移实现；先迁移一个完整用例，再扩大范围。
- 每个阶段保持 `mind.py`、CLI、MCP 和订阅入口可验证。
- `backend/` 是独立打包，整个计划不修改它、不改变它的依赖。
- 只保留必要的过渡入口，并在本文件登记删除条件和截止阶段。
- 任何状态所有权不清的代码先停止扩散，不通过共享工具函数掩盖边界问题。

## 阶段总览

| 阶段 | 状态 | 目标 | 可交付物 | 完成信号 |
| --- | --- | --- | --- | --- |
| 0. 契约冻结 | 已完成 | 固定外部行为和依赖基线 | 导入图、协议清单、风险清单 | 全部阶段 0 退出条件通过 |
| 1. Session 骨架 | 进行中 | 引入 Command/Event 和单写者 | `agent.protocol`、SessionLoop、事件游标 | 一个主动 turn 走完整闭环 |
| 2. 持久化收束 | 未开始 | 迁移事件、快照、效果和 outbox | stores 实现及恢复测试 | 强制退出后可恢复或对账 |
| 3. 能力解耦 | 未开始 | 模型、MCP、Helix、进程通过端口接入 | capabilities 和 adapters | runtime 不导入具体传输实现 |
| 4. 多入口迁移 | 未开始 | CLI/TUI/MCP/订阅统一提交命令 | adapters 全量切换 | 四类入口共享同一 Run 语义 |
| 5. 历史包退役 | 未开始 | 删除历史职责和过渡入口 | 旧包删除清单 | 生产导入图只剩 `agent` |

## 阶段 0：契约冻结

状态：已完成（2026-08-28）

### 工作项

1. 记录当前 CLI、MCP、订阅、工具结果和历史记录的外部 schema。
2. 生成非 `backend/` 的 Python 导入图，标出 `engine` 反向依赖和循环依赖。
3. 给主动 turn、工具调用、审批、取消、断线恢复建立端到端基线测试。
4. 标记现有 `Mind` 控制器中真正拥有状态的字段；禁止新增字段继续堆入控制器。

### 已完成证据

- 主动 Turn 入口已收敛到 `mind_app/runtime/turns/root.py`，但仍是旧运行时
  用例，不视为 SessionLoop 或 Command Gateway。
- 订阅、外部 MCP、本地服务、事件报告和工作区编码资源已分别收敛到
  具有完整启停语义的 runtime owner；配置服务生命周期已回到 CLI 组合根。
- 控制器已删除对应的任务、连接和资源别名，
  `tests/test_package_architecture.py` 防止已移出所有权回流。
- 当前完整测试基线为 `2808 passed, 13 skipped`；`py_compile` 和
  `git diff --check` 通过。

### 完成清单

- [x] 生成并提交非 `backend/` Python 导入图，标注循环、`engine` 反向依赖和临时跨层边。
- [x] 固化 CLI、MCP、Subscription、工具结果和历史记录的外部 schema 目录。
- [x] 为四类入站入口补齐 Command/Event 映射草图，明确身份、幂等键和终态。
- [x] 将主动 Turn、工具、审批、取消和断线恢复测试整理为可重复执行的基线矩阵。
- [x] 记录 `backend/` 的目录、导入和构建基线，证明 Agent Runtime 迁移没有改变其边界。

### 出口条件

- 基线测试在仓库虚拟环境通过；
- 每个外部入口都有对应的 Command/Event 映射草图；
- `backend/` 的目录、构建和测试结果与本阶段之前一致。

退出结果记录在 `AGENT_RUNTIME_PHASE0_BASELINE.md`。阶段 0 的全部待完成项和
退出条件已通过，因此阶段 1 可以创建首个接入主动 Turn 的顶层 `agent` 切片。

## 阶段 1：Session 骨架

状态：进行中

现有 `root.py`、Turn executor 和各 runtime owner 只是本阶段的输入边界，
新切片只接管了主动 `exec` 的命令和状态外壳；其余入口仍由旧运行时直接编排。

### 工作项

1. 实现只包含类型和序列化规则的 `agent.protocol`。
2. 实现 `SessionLoop` 和 `RunActor`，先将现有主动 turn 包装成一个命令处理器。
3. 将 `stream_turn` 的输入解析、状态转移、模型流处理、工具效果和输出投影
   分成独立的调用点；不在第一步重写模型逻辑。
4. 引入 Event Queue，让 TUI 和 CLI 通过事件投影得到结果。

### 当前切片证据

- [x] `SubmitTurnCommand` 和 `RunEvent` 只依赖标准库，载荷经过 JSON 校验、
  深复制和冻结；本地身份不复用线上 `cid/sid/turn_id/event_seq`。
- [x] `SessionLoop` 使用唯一 worker 串行执行 `RunActor`，按 command id 或
  idempotency key 去重，并为每个 Run 生成从 1 开始的连续事件序号。
- [x] CLI `exec` 已通过 `agent.application.submit_turn` 提交命令，现有
  `run_root_turn` 作为注入能力执行；成功、失败和用户取消路径均有测试。
- [x] `RunActor` 终态事件携带完整 `RunResult` 协议快照，application 校验唯一
  终态、连续序号、状态和退出码；CLI `exec` 的退出码已改从该事件投影读取。
- [x] `stream_turn` 的回调校验、请求参数固定、执行环境/skills 解析和输出会话
  装配已收敛到 `stream_setup.py`，当前请求与停止 Hook 续跑参数分开持有。
- [x] `stream_turn` 的协议终态、工具中断、对账要求和本地异常已统一写入
  `StreamTurnOutcome`；会话记录、Stop Hook、观测和 `RunResult` 使用同一终态优先级。
- [x] 新 runtime 核心没有导入 `mind_app`、`mind_core`、`mind_nova`、`engine`
  或 `server`；生成导入图只新增 `mind_app -> agent`。
- [ ] 继续将 `stream_turn` 的模型流、工具效果和展示投影拆成独立调用点。
- [ ] 让 TUI 从 Event Queue 投影结果，并把长生命周期 Session 的关闭、并发提交
  和取消统一交给 application 组合；CLI 的流式展示仍使用现有 OutputSession。

### 出口条件

- 同一个 Run 的并发提交不会产生交错状态序列；
- 取消和关闭能等待 SessionLoop 收束；
- 主动 `exec` 至少有成功、工具失败、用户取消三条测试路径。

## 阶段 2：持久化收束

状态：未开始

### 工作项

1. 将 `LocalEffectJournal` 的状态机提升为正式 store，并为每个效果分配稳定
   `effect_id` 和请求指纹。
2. 写入事件、快照和 outbox 的本地事务；定义事件游标和快照版本。
3. 将 Agent Graph 检查点与 Session/Run 历史分开存储，避免恢复时互相污染。
4. 增加进程终止、网络超时、未知结果和重复 dispatch 的恢复测试。

### 出口条件

- 重启后可以恢复 queued/running/waiting/paused 状态；
- 未知外部结果进入人工对账，不自动重复高风险动作；
- 最终消息、工具结果、审批决定和证据引用可读取；流式 token 不作为恢复前提。

## 阶段 3：能力解耦

状态：未开始

### 工作项

1. 定义 model、MCP、Helix、process、filesystem 的最小 capability port。
2. 将 `mind_nova` 的传输请求实现拆到 capability adapter；类型留在 protocol。
3. 将 `engine` 的平台能力向 capability adapter 收口，移除协议层对 `engine` 的
   导入。
4. 为每个 capability 提供 fake/in-memory 实现，用于 domain/runtime 测试。

### 出口条件

- `protocol`、`domain`、`runtime` 可脱离网络和 TUI 执行测试；
- capability 失败能转换为具名、可持久化的错误事件；
- Helix 的启动、连接和回收不被模型轮次代码隐式触发。

## 阶段 4：多入口迁移

状态：未开始

### 工作项

1. CLI 将参数、stdin、resume 和退出处理映射到 application command。
2. TUI 只订阅事件投影，保留现有窄 capability port 约束。
3. MCP server 将每个请求交给 Command Gateway，不直接构造控制器或模型。
4. Subscription handler 只处理 open/ws/resume、去重、mailbox 和确认；任务执行
   通过同一个 application command。
5. server 若继续独立运行，只依赖协议与 application 的公开入口。

### 出口条件

- 主动执行和订阅执行共享 Run、Tool、Approval、Effect 语义；
- WebSocket 回调中没有模型轮次或工具调用；
- TUI、CLI、MCP 和 subscription 的结果都可从 Event 游标重放。

## 阶段 5：历史包退役

状态：未开始

只有全部条件满足后才能删除 `mind_app`、`mind_core`、`mind_nova` 中已经迁移的
职责：

- 非测试生产代码不再导入待删除模块；
- `mind.py` 和所有外部入口已经切换到 `agent.composition`；
- 旧配置、历史、报告和订阅数据完成版本迁移；
- 主流程、失败路径、恢复、协议兼容和构建测试通过；
- 每个过渡入口都有删除记录，没有长期转发 facade。

历史包的物理目录可以分批删除，但每一批都必须保持可构建、可启动、可恢复。
不得用一次性 `Move-Item` 或批量改名替代上述出口条件。

## 过渡入口登记

| 入口 | 保留原因 | 删除条件 | 所属阶段 |
| --- | --- | --- | --- |
| `mind.py` | 稳定启动方式 | 无需删除，改为组合根调用 | 4 |
| 旧 CLI 导入路径 | 外部脚本兼容 | 所有内部调用改走 application，完成兼容窗口 | 4/5 |
| 旧协议类型别名 | 数据和客户端迁移 | 新旧 schema 均有版本识别且无旧生产消费者 | 4/5 |
| 旧历史读取器 | 读取存量会话 | 历史数据迁移并完成回读校验 | 2/5 |
| `mind_app/cli/dispatch.py -> agent.application/protocol` | 首个主动 `exec` 入站切片 | CLI adapter 迁入 `agent.adapters.cli` 且入口只依赖公开组合根 | 4 |

## 风险与处理

### 并发状态竞争

风险：原有 controller、stream 和 subagent 控制器可能同时写状态。

处理：先让所有写入口经过 RunActor，再拆分实现；测试检查同一 Run 的 sequence
单调递增和唯一写者。

### 外部副作用重复

风险：进程在请求已发出但结果未返回时退出。

处理：Effect Journal 标记 `unknown`，用 fingerprint 对账；高风险效果没有明确
结果时不自动重放。

### 过渡层永久化

风险：旧包 facade 逐渐成为新的共享杂物层。

处理：每个过渡入口必须有删除条件、负责人和阶段；新代码禁止反向依赖旧包。

### 文档与实现漂移

风险：协议、CLI、MCP 和订阅行为只更新代码。

处理：架构文档、专题文档、manifest 和生成页随契约变更一起校验。

## 每个变更的最小验证集

```text
.\venv\Scripts\python.exe -m pytest <受影响测试>
.\venv\Scripts\python.exe -m py_compile <受影响源码>
python website/mind/scripts/check_docs.py
```

修改共享协议、控制器、恢复、配置或公开入口时，扩大到完整测试套件并检查：

- import graph 是否出现 domain/protocol -> adapter 反向依赖；
- `backend/` 是否保持无外部导入；
- 事件游标、快照版本和 outbox 是否向后兼容；
- Windows、Linux、macOS 的路径和子进程行为是否仍通过显式平台端口。

## 变更记录

| 日期 | 阶段 | 结果 | 未决项 |
| --- | --- | --- | --- |
| 2026-08-28 | 阶段 0 | 已完成主动 Turn 入口和五类 runtime 所有权收敛；全量测试 `2808 passed, 13 skipped` | 导入图、外部契约目录、Command/Event 映射、基线矩阵和 `backend/` 证据待收口 |
| 2026-08-28 | 阶段 0 | 提交导入图、外部契约、Command/Event 映射、测试矩阵和 backend tree 基线；定向验收 `222 passed`，其余静态检查通过 | `engine -> mind_core -> engine` 循环登记到阶段 3；阶段 0 无未决项 |
| 2026-08-28 | 阶段 1 | 主动 `exec` 已接入类型化 Command、SessionLoop、RunActor 和 Event Queue；全量测试 `2816 passed, 13 skipped`，导入图和静态检查通过 | `stream_turn` 内部职责拆分、CLI/TUI Event 投影和长生命周期 Session 接管待完成 |
| 2026-08-28 | 阶段 1 | CLI 改从终态 Event 投影退出码；`stream_turn` 已拆出请求准备和唯一终态写者；全量测试 `2825 passed, 13 skipped` | 模型流、工具效果、展示投影、TUI 和长生命周期 Session 接管待完成 |
