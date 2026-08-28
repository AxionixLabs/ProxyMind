# Agent Runtime 迁移计划

这份计划配合 [Agent Runtime 架构基线](AGENT_RUNTIME_ARCHITECTURE.md) 使用。
它把从历史包到 `agent` bounded context 的改造拆成可回滚阶段；每一阶段都必须
有代码、测试和导入边界证据，不能以“目录已经移动”作为完成标准。

## 迁移原则

- 先建立运行契约，再迁移实现；先迁移一个完整用例，再扩大范围。
- 每个阶段保持 `mind.py`、CLI、MCP 和订阅入口可验证。
- `backend/` 是独立打包，整个计划不修改它、不改变它的依赖。
- 只保留必要的过渡入口，并在本文件登记删除条件和截止阶段。
- 任何状态所有权不清的代码先停止扩散，不通过共享工具函数掩盖边界问题。

## 阶段总览

| 阶段 | 目标 | 可交付物 | 完成信号 |
| --- | --- | --- | --- |
| 0. 契约冻结 | 固定外部行为和依赖基线 | 导入图、协议清单、风险清单 | 定向测试基线通过 |
| 1. Session 骨架 | 引入 Command/Event 和单写者 | `agent.protocol`、SessionLoop、事件游标 | 一个主动 turn 走完整闭环 |
| 2. 持久化收束 | 迁移事件、快照、效果和 outbox | stores 实现及恢复测试 | 强制退出后可恢复或对账 |
| 3. 能力解耦 | 模型、MCP、Helix、进程通过端口接入 | capabilities 和 adapters | runtime 不导入具体传输实现 |
| 4. 多入口迁移 | CLI/TUI/MCP/订阅统一提交命令 | adapters 全量切换 | 四类入口共享同一 Run 语义 |
| 5. 历史包退役 | 删除历史职责和过渡入口 | 旧包删除清单 | 生产导入图只剩 `agent` |

## 阶段 0：契约冻结

### 工作项

1. 记录当前 CLI、MCP、订阅、工具结果和历史记录的外部 schema。
2. 生成非 `backend/` 的 Python 导入图，标出 `engine` 反向依赖和循环依赖。
3. 给主动 turn、工具调用、审批、取消、断线恢复建立端到端基线测试。
4. 标记现有 `Mind` 控制器中真正拥有状态的字段；禁止新增字段继续堆入控制器。

### 出口条件

- 基线测试在仓库虚拟环境通过；
- 每个外部入口都有对应的 Command/Event 映射草图；
- `backend/` 的目录、构建和测试结果与本阶段之前一致。

## 阶段 1：Session 骨架

### 工作项

1. 实现只包含类型和序列化规则的 `agent.protocol`。
2. 实现 `SessionLoop` 和 `RunActor`，先将现有主动 turn 包装成一个命令处理器。
3. 将 `stream_turn` 的输入解析、状态转移、模型流处理、工具效果和输出投影
   分成独立的调用点；不在第一步重写模型逻辑。
4. 引入 Event Queue，让 TUI 和 CLI 通过事件投影得到结果。

### 出口条件

- 同一个 Run 的并发提交不会产生交错状态序列；
- 取消和关闭能等待 SessionLoop 收束；
- 主动 `exec` 至少有成功、工具失败、用户取消三条测试路径。

## 阶段 2：持久化收束

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
