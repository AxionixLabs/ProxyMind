# ProxyMind 测试架构分阶段改造清单

本文定义 ProxyMind 客户端测试集的目标结构、归属规则、自研测试设施边界、执行分层和分阶段
改造门槛。客户端内部职责仍以 `ARCHITECTURE.md` 为准，跨系统契约仍以
`ARCHITECTURE_SYSTEM.md`、服务端正式契约及 `protocol/schema/`、`protocol/client/` 为准；
本文不重新定义产品架构或线上协议。

本文只作为改造期间的接力清单和验证记录。所有长期有效的测试归属、设施边界和运行方式必须在
改造完成前收敛到 `tests/README.md`；完成后由仓库维护者手动删除本文，最终只保留一份测试说明。

## 目标结论

采用“pytest 稳定内核 + ProxyMind 领域测试设施”的架构：

- pytest 继续负责测试发现、fixture、参数化、异步执行、marker、失败报告和工具生态；
- 测试目录表达稳定的责任所有者，目录不表达速度、测试等级或 CI 运行频率；
- marker 只表达跨目录的执行属性、风险门禁或环境要求；
- 自研代码只负责 Turn、审批、工具、重放、终端帧和 PTY 等领域场景、可控故障与不变量断言；
- 不自研测试运行器、fixture 容器、mock 框架、断言语言、调度器或独立配置格式；
- 自动化客户端测试不直接覆盖独立的 `backend/` 服务包。客户端禁止依赖 `backend` 的审计仍
  属于客户端架构边界，应继续保留；未来若恢复 backend 验证，应由其独立构建边界拥有测试集。

完成后的核心收益不是目录更整齐，而是每个失败都能回答三个问题：哪个责任所有者失败、哪条
稳定不变量失败、哪一级验证已经阻止回归。

## 当前基线

以下数据是 2026-09-07 当前工作树的盘点快照，不作为永久固定数量。迁移期间必须在每个批次
开始时重新生成基线，并比较该批次前后的测试集合。

| 信号                               |                       当前值 | 判断                                    |
|------------------------------------|-----------------------------:|-----------------------------------------|
| `tests/` 根目录测试模块            |                          198 | 全部平铺，责任边界不可见                |
| pytest 收集用例                    |                         4095 | 规模已经需要稳定的选择与分层机制        |
| 根目录测试代码                     |                  约 9.7 万行 | 目录导航和代码审查成本过高              |
| `test_tui_spacing.py`              |  超过 1 万行、295 个测试函数 | 同时承担过多 TUI 行为与测试设施职责     |
| `test_package_architecture.py`     |   约 5500 行、120 个测试函数 | 稳定入口正确，但内部需要按边界拆分      |
| `test_run_result.py`               |    约 4700 行、85 个测试函数 | Turn、工具、审批、重放等职责混合        |
| `test_tui_input_completion.py`     |    约 2500 行、74 个测试函数 | 输入来源、菜单和编辑行为混合            |
| `test_hooks.py`                    |    约 2400 行、83 个测试函数 | 与已有 hook 专题模块职责重叠            |
| 根 `conftest.py`                   | 1 个 `anyio_backend` fixture | 简洁，应保持克制                        |
| 手工 `live_*` 脚本                 |                            3 | 与自动化测试同层但不参与 pytest 发现    |
| 直接依赖 `__file__` 层级的测试模块 |                        约 13 | 移动目录会破坏仓库根和 fixture 路径解析 |

现有 marker 在当前快照中的收集规模如下；各集合允许重叠：

| marker             | 收集数 | 当前职责                     |
|--------------------|-------:|------------------------------|
| `pty_acceptance`   |     87 | 真实 PTY 或 ConPTY 验收      |
| `runtime_p0`       |    331 | Agent Runtime 关键风险门禁   |
| `runtime_fault`    |    135 | Fake Server 与传输故障注入   |
| `runtime_frame`    |      9 | TUI 逻辑帧与原子提交契约     |
| `runtime_stateful` |     10 | 固定 seed 的可重放状态长序列 |

### 已有优势

- 测试统一使用 pytest，异步测试已有 `anyio` 约定；
- Turn 场景设施已提供不可变场景值、状态枚举和统一不变量断言；
- Fake Mind Chat Server 已能表达响应丢失、超时、重连和幂等窗口；
- PTY 设施已隔离原生 PTY、终端输入、渲染场景和平台差异；
- fixture 数据使用 JSON 文件而不是把大型结构散落在测试代码中；
- 大多数测试不互相导入，需要复用的测试设施数量有限，物理迁移风险可控；
- 现有 marker 已覆盖 Runtime 最关键的故障、帧和状态序列。

### 主要问题

- 文件名隐含领域，但平铺目录不能表达顶层责任所有者和依赖方向；
- 巨型测试模块混合被测行为、场景构造、fake、断言和渲染数据，修改冲突频繁；
- “测试哪一层”和“什么时候运行”尚未分离，开发者难以稳定选择最小测试集；
- 测试通过 `Path(__file__).parents[...]` 推导仓库根，结构变化会产生隐式破坏；
- 少数命令展示断言写死旧测试路径，移动后必须区分真实路径与故意构造的示例文本；
- 手工联调脚本、自动化验收测试和可复用测试设施尚未形成清晰生命周期边界；
- 当前 CI 没有形成快速门禁、Runtime 风险门禁、平台验收和全量回归的明确矩阵；
- 超大文件内已有与其他专题文件重叠的行为，直接按行切分会固化重复职责。

## 架构原则

1. **先按责任所有者定位。** 测试放在其主要断言所约束的生产责任下，不按导入数量决定归属。
2. **目录与执行属性正交。** `agent/`、`protocol/`、`frontends/` 表示所有者；PTY、P0、故障、
   慢速和平台要求由 marker 表示。
3. **测试公开事实。** 优先通过公开契约、具名端口、稳定事件和投影断言行为；只有架构审计或
   adapter 边界测试可以有理由检查源码结构或第三方私有接口。
4. **一个测试只有一个主要失败原因。** 单个用例可以穿过多层，但断言必须围绕同一不变量。
5. **场景模型不复制生产状态机。** 自研 harness 负责生成输入、记录事实和检查不变量，不重新
   实现被测分支来计算预期结果。
6. **依赖显式、时间可控。** clock、随机 seed、transport、进程和文件根从边界注入；自动化
   测试不依赖公网、用户主目录或执行顺序。
7. **结构迁移不夹带行为修改。** 移动、路径修正、拆分和行为增强分别提交并分别验收。
8. **不以覆盖率替代风险覆盖。** 先覆盖身份、终态、幂等、审批、副作用、恢复和资源关闭等
   核心不变量，再使用覆盖率寻找盲区。

## 目标目录

测试目录镜像稳定的生产责任边界，最多按有实际规模的下一层职责展开；不要为单个文件预建
空目录。

```text
tests/
├── README.md
├── conftest.py
├── test_package_architecture.py       # 根级稳定审计入口，路径保持不变
├── architecture/                      # 从巨型审计拆出的专题边界检查
├── agent/
│   ├── adapters/
│   ├── application/
│   ├── capabilities/
│   ├── domain/
│   ├── harness/
│   ├── protocol/
│   └── stores/
├── protocol/
│   ├── schema/
│   ├── transport/
│   └── client/
├── frontends/
│   ├── cli/
│   ├── mcp/
│   ├── subscription/
│   ├── terminal/
│   └── tui/
│       ├── adapters/
│       ├── core/
│       ├── features/
│       ├── rendering/
│       ├── runtime/
│       └── acceptance/
├── infrastructure/
│   ├── config/
│   ├── hooks/
│   ├── mcp/
│   ├── persistence/
│   ├── platform/
│   ├── services/
│   ├── sidecars/
│   ├── skills/
│   ├── update/
│   └── workspace/
├── observability/                     # 运行报告、结构化观察与记录边界
├── composition/                       # 根组合入口和资源 owner
├── integration/                       # 跨顶层所有者的完整客户端用例
├── distribution/                      # build、npm 和发布布局
├── fixtures/                          # 无行为的版本化输入与期望数据
├── scenarios/                         # Turn 场景、观察事实与稳定不变量
├── fakes/                             # 按正式端口命名的可控测试实现
├── pty/                               # 跨平台 PTY 与终端验收设施
└── manual/                            # live_* 人工联调入口，不作为 pytest 用例
```

`tests/test_package_architecture.py` 是 `AGENTS.md` 和 `ARCHITECTURE.md` 指定的稳定入口，不移动、
不删除。允许把专题扫描器和专题测试拆入 `tests/architecture/`，但根文件必须继续承载跨包总边界
和稳定的定向执行入口。

测试目录默认不添加 `__init__.py`，测试模块文件名在整棵树内保持唯一。只有
`tests/scenarios/`、`tests/fakes/`、`tests/pty/` 这类需要显式导入的具名测试设施使用包初始化
文件。禁止新增 `support/`、`utils/`、`common/` 等通用收纳包；无法按能力命名的 helper 留在
使用它的测试模块内。不要在迁移阶段同时切换 pytest import mode；若未来确有模块冲突，再作为
独立变更评估 `importlib` 模式。

## 归属决策

新增或迁移测试时按以下顺序判断：

1. 如果测试审计跨包依赖、公开入口或全仓源码约束，放在根稳定审计或 `tests/architecture/`。
2. 如果主要断言属于一个生产模块，将目录镜像到对应顶层包和最近的稳定职责目录。
3. 如果测试覆盖同一顶层包内的多层协作，放在拥有该用例或生命周期的 application/harness 层。
4. 如果测试必须组合两个以上顶层所有者才能表达主要事实，放入 `tests/integration/`。
5. 如果测试启动根 `composition.py` 或 `mind.py` 来验证组装和资源所有权，放入
   `tests/composition/`；只有继续覆盖完整用户流程时才进入 `integration/`。
6. 如果代码只供人工连接真实服务、设备或账号使用，放入 `tests/manual/`，文件名不以
   `test_` 开头。

| 被验证事实                        | 应归属                           | 不应归属                      |
|-----------------------------------|----------------------------------|-------------------------------|
| 纯领域规则、状态转换              | `tests/agent/domain/`            | `integration/`                |
| Agent 用例协调与投影              | `tests/agent/application/`       | 按所用 adapter 分类           |
| Session、Run、Hook、Tool 生命周期 | `tests/agent/harness/`           | `frontends/`                  |
| 本地事实、CAS、幂等存储           | `tests/agent/stores/`            | `infrastructure/persistence/` |
| wire 字段、事件和错误             | `tests/protocol/schema/`         | Agent 本地协议目录            |
| HTTP/SSE 可靠请求                 | `tests/protocol/transport/`      | `integration/`                |
| 远程协议用例客户端                | `tests/protocol/client/`         | 按调用它的前端分类            |
| TUI 输入、布局或渲染              | `tests/frontends/tui/` 对应职责  | `agent/`                      |
| 终端能力、颜色、PTY adapter       | `tests/frontends/terminal/`      | TUI core                      |
| 配置、平台、sidecar 具体实现      | `tests/infrastructure/` 对应职责 | `agent/ports/`                |
| 完整 Turn 从 wire 到前端投影      | `tests/integration/`             | 任一单层目录                  |
| 打包布局和 npm launcher           | `tests/distribution/`            | `infrastructure/`             |

“单元、组件、集成、验收”是验证深度，不作为第一层目录。一个 `protocol/transport` 测试可以是
纯单元测试，也可以使用本地 fake transport；它的所有者不会因此变化。

## 验证深度

| 深度          | 主要对象                               | 允许的替身                 | 主要价值                 | 默认频率         |
|---------------|----------------------------------------|----------------------------|--------------------------|------------------|
| 规则/契约     | reducer、schema、policy、值对象        | 无或纯数据 builder         | 快速定位规则回归         | 每次相关修改     |
| 组件/生命周期 | application、harness、store            | 具名 fake port、注入 clock | 验证状态所有权和关闭语义 | 每次相关修改     |
| adapter 集成  | HTTP、SSE、文件、sidecar、终端 adapter | 本地 fake server/process   | 验证边界转换和故障分类   | PR 门禁          |
| 客户端验收    | 完整 Turn、TUI、PTY、恢复流程          | 只替代真实外部系统         | 验证用户可观察行为       | 合并、夜间或发布 |

同一风险可以在多个深度验证，但每一级必须有独立价值。例如终态不可离开应先由纯 reducer 测试
证明，再由 Fake Server 故障场景验证恢复，最后只用少量 PTY 用例证明用户表面没有错误释放。

## 核心风险矩阵

以下不变量是测试架构优先保护的对象。每一行至少要有快速确定性门禁；慢速验收不能成为唯一
证据。

| 风险                        | 快速门禁                  | 场景/故障门禁             | 验收门禁                  |
|-----------------------------|---------------------------|---------------------------|---------------------------|
| Session、Run、Turn 身份混用 | 类型和值约束              | 重连、重复事件、迟到事件  | 恢复后只显示目标 Turn     |
| 幂等键或请求身份漂移        | command/request builder   | 响应丢失后重试            | 不产生重复 Turn 或副作用  |
| 非权威信号提前结束 Turn     | reducer/terminal contract | EOF、204、404、超时       | 输入门只在权威终态释放    |
| 工具副作用重复执行          | tool policy/store         | replay、未知结果、对账    | PTY/完整流程只执行一次    |
| 审批裁决错配或过期          | approval domain           | 排队、过期、迟到 snapshot | 前端不裁决错误 call       |
| replay 污染活动表面         | item/surface reducer      | gap、supersede、旧 epoch  | TUI 不恢复旧画面          |
| 资源关闭遗漏                | owner 单元测试            | 单项清理失败注入          | 进程、PTY、sidecar 无泄漏 |
| TUI 非原子帧                | frame reducer             | 工具、审批、正文因果交接  | 屏幕无中间空帧和重叠      |
| 严格 wire 契约退化          | schema/client contract    | 未知字段和错误响应        | 多前端消费同一正式事件    |

## 自研测试设施边界

### 应保留并增强

- `scenarios/turns.py`：只保存不可变输入维度、稳定标识、观察记录和跨实现不变量；
- `fakes/mind_chat.py`：模拟正式端口语义、可控故障窗口、事件序号和请求记录；
- `pty/`：隔离 Windows ConPTY 与 POSIX PTY 差异，统一有界等待、输入、resize 和关闭；
- 协议 fixture：保存正式 wire 载荷、未知字段、边界值和版本化期望；
- 事实 recorder：记录端口调用、资源 lease、frame、请求身份和关闭顺序，失败时输出完整事实；
- 固定 seed 状态序列：失败报告必须包含 seed 和最小场景标识，保证可复现。

### 不应自研

- 替代 pytest 的测试发现、参数化、fixture 生命周期或插件系统；
- 包装 `unittest.mock` 的通用 mock DSL；
- 使用字符串步骤解释业务的通用 BDD 语言；
- 隐式注入所有依赖的 service locator 或全局测试容器；
- 复制生产 reducer、重试算法或状态机来生成期望结果；
- 依赖固定 `sleep`、真实公网、真实用户配置或测试执行顺序的调度器；
- 为了减少几行样板而返回宽泛字典、`Any` 或动态属性的万能 stub。

### 测试设施准入规则

- 至少有三个测试模块共享同一稳定概念，或重复实现已经造成事实不一致，才提取公共 helper；
- helper 接收显式参数并返回具名值，不读取调用测试的局部变量或隐藏的进程状态；
- fake 实现声明它模拟的端口，不扩展成生产对象的完整替代品；
- builder 只构造有效默认值和明确覆盖项，不能静默删除未知字段或兼容旧字段；
- assertion helper 必须保留 pytest 可读的差异，并在错误中包含身份、序号、阶段和观察事实；
- 支持代码本身的状态转换、故障注入和资源生命周期必须有小型契约测试；
- 平台差异只留在 PTY/进程 adapter，领域场景不分支判断操作系统。
- 不建立按“支持”“通用”或“工具”命名的收纳模块；共享代码必须直接表达场景、fake、PTY 或
  其他稳定测试能力。

## Fixture 与数据规则

- 根 `tests/conftest.py` 只保存全测试树确实共享且无业务含义的配置，例如 anyio backend 和由
  pytest `rootpath` 派生的仓库、fixture 根目录；
- 只在同一责任子树的三个以上模块共享 fixture 时增加局部 `conftest.py`；
- 优先使用显式 factory/helper 参数，不使用难以追踪的 autouse fixture；
- fixture 的创建方负责关闭进程、task group、socket、文件和 sidecar，清理失败不得跳过后续资源；
- 测试不直接修改进程环境，路径、平台和配置派生值从上层注入；
- JSON fixture 进入 schema/parser 前保持原始外部形态，验证后再转为具名类型；
- golden/snapshot 只用于稳定的用户可见输出或正式 wire 载荷，不用于隐藏业务状态；
- 更新 snapshot 必须审阅语义差异，禁止用批量接受掩盖回归；
- 临时文件统一使用 pytest `tmp_path`，仓库与 fixture 根通过具名 pytest fixture 注入；
- 不再从可迁移测试文件的目录深度推导仓库根，也不建立保存全局路径常量的公共测试模块；固定在
  根目录的架构审计入口可以从自身稳定位置定位仓库根。

## Marker 与执行策略

marker 只在存在实际选择命令和维护责任时增加。保留当前五个 marker；不要给所有测试机械添加
`unit` 或 `integration`。

候选 marker 只有在相应 CI job 同时落地时才可新增：

- `slow`：算法上必然进行长序列、重复稳定性运行或大规模生成，不按某台机器的偶然耗时判断；
- `external_process`：启动真实子进程或 sidecar，但不等同于真实网络；
- `platform_windows`、`platform_macos`、`platform_linux`：行为只能在指定平台成立；
- `serial`：经审计确认占用不可隔离的进程级资源，在并行运行前必须串行。

建议执行层级：

| 门禁       | 选择                      | 触发时机         | 失败处理                 |
|------------|---------------------------|------------------|--------------------------|
| 定向门禁   | 受影响责任目录或测试文件  | 本地每次修改     | 立即修复                 |
| 快速全量   | 排除真实 PTY 和明确 slow  | 每个 PR          | 阻止合并                 |
| Runtime P0 | `-m runtime_p0`           | 每个 PR          | 阻止合并                 |
| 平台验收   | PTY、sidecar、平台 marker | 对应平台合并门禁 | 阻止发布分支推进         |
| 完整回归   | 全测试树                  | 夜间和发布       | 建立责任人并修复         |
| 稳定性回归 | 固定 seed 矩阵和有限重复  | 夜间或发布       | 输出 seed 与首个失败场景 |

在完成端口、临时目录、进程环境和全局状态隔离审计前，不直接启用 pytest-xdist。需要并行时，
先识别并标记真实串行集合，再验证并行与串行结果一致。

## 分阶段改造

### 当前实施状态（2026-09-08）

当前接力点如下。通用 `tests/support` 已移除，阶段 1 路径治理、阶段 2 目录迁移和阶段 3 巨型
模块拆分均已完成并通过门禁，阶段 3 已推送为 `c9c9a2c8`。阶段 4 场景与测试设施已经完成实现，
并通过全部合并验证；阶段 4 推送后进入阶段 5 CI 门禁。继续改造时仍按真实层级确定主要生命周期，
不能按文件名前缀机械归类：

| 项目                 | 当前状态                                                                                                |
|----------------------|---------------------------------------------------------------------------------------------------------|
| 阶段 1 路径治理      | 已完成；无通用 support 包，可迁移测试由 pytest 根 fixture 显式接收仓库或 fixture 根                     |
| 路径与具名设施定向集 | `416 passed`；另有真实 TUI PTY `86 passed, 1 skipped`                                                   |
| macOS sidecar 定向集 | 当前 Windows 环境 `11 skipped`，仍需 macOS 门禁验证                                                     |
| 完整架构审计         | 拆分后根入口与 `tests/architecture/` 合计 `132 passed`，1 条第三方弃用 warning                          |
| 非 PTY 快速全量      | 目录迁移后为 `4064 passed, 14 skipped, 135 deselected`                                                  |
| Runtime P0           | `331 passed, 3882 deselected`                                                                           |
| PTY 已知基线         | `test_exec_command_pty.py` 为 `20 passed, 3 failed`；失败均为 sandbox sidecar 启动后未返回 session id   |
| 批次 A 迁移前收集    | 规范化 node id 共 `4211`，SHA-256 为 `CC786B4BE4A8EA6D7037CF45FCC673DD46429D9922F9A9E9B134E79E24D3EE03` |
| 阶段 2 批次 A        | 已完成 36 个纯文件移动；迁移后规范化 node id 数量和哈希与迁移前完全一致                                 |
| 批次 A 定向验证      | protocol `128 passed`；infrastructure `259 passed, 14 skipped`；distribution `11 passed`                |
| 阶段 2 批次 B        | 已完成 28 个 frontend 纯文件移动；迁移后规范化 node id 数量和哈希与迁移前完全一致                       |
| 批次 B 定向验证      | frontend 非 PTY `471 passed, 21 deselected`；terminal acceptance `19 passed, 2 skipped`                 |
| 阶段 2 批次 C1       | 已完成 34 个 Agent composition、domain、application、capabilities、adapters 纯文件移动                  |
| 批次 C1 定向验证     | `tests/agent` 为 `400 passed`；全树规范化 node id 数量和哈希与迁移前完全一致                             |
| 阶段 2 批次 C2       | 已完成 18 个 Agent stores 与 harness 纯文件移动                                                         |
| 批次 C2 定向验证     | `tests/agent` 为 `677 passed`                                                                           |
| 阶段 2 批次 C3       | 已完成 14 个 Agent、hooks、persistence、protocol schema、observability 纯文件移动                        |
| 批次 C3 定向验证     | 对应迁移目标为 `212 passed`；规范化收集数量和哈希与当前基线完全一致                                     |
| 阶段 2 批次 D1       | 已完成 38 个 TUI adapters、core、features 纯文件移动；定向验证 `1103 passed`                             |
| 阶段 2 批次 D2       | 已完成 17 个 TUI architecture、rendering、runtime、acceptance 纯文件移动；非 PTY 定向验证 `625 passed`  |
| 阶段 2 批次 E        | 已完成 10 个 integration、composition、scenario 测试和 3 个 manual 入口移动                             |
| 批次 E 定向验证      | 非 PTY 为 `458 passed, 23 deselected`；规范化收集数量和哈希与当前基线完全一致                            |
| 根目录收口           | 仅保留 `conftest.py`、`README.md`、`test_package_architecture.py`                                       |
| 当前全树收集         | `4222`；阶段 4 新增场景与 Fake 契约节点，无收集错误                                                       |
| 当前规范化收集基线   | node id 按“文件名 + 测试路径”排序、LF 连接且无尾换行，SHA-256 为 `A0F968290BCBC4FEA1E84AB2AD31F193FBAC3D3730A451CABF8160035AE81556` |
| 阶段 3 语义收集基线  | 拆分后仍为 `4213`；忽略文件路径的测试路径 SHA-256 为 `443274728CC422B18311F0F251F74686724E8DB51CA9252D419A197A7049407F` |
| 架构与平台定向验证   | 根架构、terminal acceptance、macOS 门禁共 `139 passed, 13 skipped`                                     |
| 阶段 3 TUI rendering | 原 437 个节点，拆分前后哈希 `04271FDB...EC81F5`；共享 frame 设施收敛后 `437 passed`                     |
| 阶段 3 RunResult     | 原 87 个节点，拆分前后哈希 `E5FE18E4...87285`；拆分后 `87 passed`                                      |
| 阶段 3 completion    | 原 123 个节点，拆分前后哈希 `705B89BC...53E45`；拆分后 `123 passed`                                    |
| 阶段 3 Hook          | 原 88 个节点，拆分前后哈希 `AF83170F...D0EC`；拆分后 `88 passed`                                       |
| 阶段 3 config        | 原 95 个节点，拆分前后哈希 `12F693F8...8F37`；拆分后 `95 passed`                                       |
| 阶段 3 architecture  | 原 124 个节点，拆分前后哈希 `F6D3314D...3E74`；含既有 TUI 专题共 `132 passed`                           |
| 阶段 3 合并定向集    | 受影响责任目录 `1480 passed, 2 skipped`                                                                |
| 阶段 3 风险门禁      | Runtime P0 `331 passed`；非 PTY 全量 `4064 passed, 14 skipped, 135 deselected`                          |
| 阶段 4 设施定向集    | Fake、场景、故障集成与 frame 契约 `357 passed`                                                         |
| 阶段 4 平台验收      | TUI 与 terminal PTY `105 passed, 3 skipped`                                                            |
| 阶段 4 风险门禁      | Runtime P0 `331 passed`；非 PTY 全量 `4073 passed, 14 skipped, 135 deselected`                          |
| 阶段 4 收集基线      | `4222`；语义 node id SHA-256 为 `BA8EE17DE3CD5F796C2C25CEDFCB6F8CA76FC8331013FA6591E2DCDD04793DC8`     |
| 静态收口             | 阶段 4 完整架构审计 `132 passed`，`compileall` 与 `git diff --check` 通过                              |

批次 A 已应用的目录范围：

- `tests/distribution/`：build、npm；
- `tests/protocol/client/`：chat stream、tool requests、turn control；
- `tests/protocol/transport/`：event report、reliable requests、service auth；
- `tests/infrastructure/config/`：应用路径、执行策略、配置 session、运行路径、settings session；
- `tests/infrastructure/platform/`：命令安全、git diff、PTY capability、macOS、安全沙箱、受管网络、shell；
- `tests/infrastructure/mcp/`：approval policy、group、基础设施和 tool result；
- `tests/infrastructure/services/`：配置服务、结果增强、server manager 和服务生命周期；
- `tests/infrastructure/sidecars/`：JavaScript bundle、process、protocol、provider 和 session；
- `tests/infrastructure/skills/` 与 `tests/infrastructure/update/`。

阶段 3 已按职责拆除六个巨型聚合模块。仍超过约 1500 行的模块只保留单一状态机或不变量矩阵，并在
模块 docstring 中说明维持整体的原因；后续不得仅为降低行数复制 fixture、驱动器或期望计算。

### 阶段 0：冻结基线与建立可比清单

- [x] 等待将被移动的测试文件不再承载未收口的用户行为修改；不回退或覆盖现有工作树改动。
- [x] 使用当前虚拟环境记录 `pytest --collect-only -q` 的退出码、总数和 node id 清单。
- [ ] 记录五个现有 marker 的收集清单，而不只记录数量。
- [ ] 运行一次完整测试集，记录通过、失败、跳过、耗时和最慢测试；已有失败单独列为基线。
- [ ] 分 Windows、Linux、macOS 记录可运行集合，平台缺失不能伪装成通过。
- [ ] 建立移动映射表：原路径、目标路径、主要所有者、路径依赖、稳定文档引用。
- [x] 对测试模块中的真实路径字符串与故意作为展示数据的虚构路径做人工区分。
- [x] 确认 backend 专属测试不在客户端测试清单中；客户端禁止反向依赖 backend 的审计保留。

完成门槛：基线可重复生成，每个测试模块有唯一目标目录，当前失败与结构改造引入的失败可以区分。

### 阶段 1：先消除目录深度耦合

- [x] 删除 `tests/support/paths.py` 和其专用契约测试，不建立通用路径模块。
- [x] 根 `tests/conftest.py` 通过 pytest `rootpath` 提供仓库根和 fixture 根的具名 fixture。
- [x] 修正 `durable_queue.json`、hook fixture、sidecar、npm 等路径消费者，通过参数显式接收路径。
- [x] 固定根架构审计入口保留自身定位；可迁移测试不再通过 `Path(__file__).parents[...]` 推导仓库根。
- [x] 新增 `tests/README.md`，写明本文的归属决策、定向命令和新增测试检查项。
- [x] 保持 pytest import mode 不变，重新执行路径消费者定向集和完整收集。

完成门槛：移动任一测试到多一层临时目录仍能解析仓库根和 fixture；完整收集与阶段 0 等价。

### 阶段 2：按责任所有者迁移目录

每个批次只做文件移动、必要导入修正和真实文档路径更新，不拆测试、不改变断言、不重写 fixture。
保留现有文件名，避免 pytest 默认导入模式下的同名模块冲突。

- [x] 批次 A：迁移 `protocol/`、`distribution/` 和边界清晰的 `infrastructure/` 测试。
- [x] 批次 B：迁移 CLI、MCP、Subscription 和 terminal 测试，PTY 测试进入 terminal 或 TUI
  acceptance 的真实所有者目录。
- [x] 批次 C：按 domain、application、harness、stores、capabilities、adapters 迁移 Agent 测试。
- [x] 批次 D：迁移 TUI 测试；先按 adapters、core、features、rendering、runtime、acceptance 分组。
- [x] 批次 E：迁移根组合、跨所有者 integration、专题 architecture 和 manual 脚本。
- [x] 保留 `tests/test_package_architecture.py` 原路径，并同步 `AGENTS.md`、`ARCHITECTURE.md`、
  `PTY_CHECKLIST.md` 中真正发生变化的稳定路径。
- [x] 更新测试断言中的真实 pytest 命令；保留 `tests/test_sample.py` 等明确作为示例载荷的虚构路径。
- [x] 每个批次比较规范化 node id：忽略目录前缀后，模块名、测试名和参数 id 必须等价。
- [x] 每个批次先跑目标目录，再跑收集；所有批次结束后完成非 PTY 全量，真实 PTY 按平台集合独立验证。

完成门槛：除 `test_package_architecture.py` 外，根目录不再平铺业务测试；收集集合没有静默增加、
减少或重命名；稳定文档路径有效。

### 阶段 3：拆分巨型测试模块

拆分按生产责任和不变量进行，不按固定行数机械切割。优先把已有专题模块能拥有的测试迁回专题，
避免创造名称不同但事实重复的新文件。

- [x] `test_tui_spacing.py`：按 frame/viewport、stream rendering、input layout、menu/approval
  surface、shell/process surface 和 terminal degradation 归入对应 TUI 专题。
- [x] 清理 `test_tui_spacing.py` 中重复的 Screen/Runtime 构造器，提取显式的局部 factory；渲染期望仍
  由测试声明，不在 helper 中计算。
- [x] `test_package_architecture.py`：将 agent、protocol、frontend、infrastructure、源码卫生等专题
  审计拆入 `tests/architecture/`，根文件保留跨包总边界、公共清单入口和稳定可执行路径。
- [x] 架构扫描 helper 只解析一次源码清单并返回具名违规项，不让专题测试各自遍历仓库。
- [x] `test_run_result.py`：按 Turn 建立与终态、工具与审批、replay/recovery、展示投影、资源关闭拆分。
- [x] `test_tui_input_completion.py`：按 completion source、候选菜单、编辑操作和关闭/恢复生命周期拆分。
- [x] `test_hooks.py`：与现有 catalog、protocol、trust、async、MCP 专题核对，按真实 owner 合并，删除
  重复断言而不是复制到新文件。
- [x] `test_config_session.py`：按 schema/解析、session 生命周期、持久化和服务边界拆分。
- [x] 继续处理超过约 1500 行或同时覆盖三个以上稳定职责的模块；例外必须在模块顶部说明原因。
- [x] 每拆一个文件，先比较测试名和参数 id，再运行拆出的全部测试；行为变更另起提交。

完成门槛：不存在无说明的巨型多职责测试文件；测试构造、刺激、观察和断言四部分边界清晰；
拆分没有降低失败信息质量。

### 阶段 4：收敛领域场景与测试设施

- [x] 将 `turn_scenarios.py` 按 runtime scenario、frame trace 和 invariant 分为最小稳定模块，仅在
  拆分确实降低耦合时执行。
- [x] 将 Fake Mind Chat Server 对齐正式控制端口、事件序号和错误类型，不增加服务端未声明字段。
- [x] 为响应已提交但回执丢失、重复事件、gap、迟到 epoch、未知工具结果建立可组合故障表。
- [x] 为每条核心风险建立一个快速最小场景和少量完整组合场景，删除只增加组合数量但不增加风险
  覆盖的参数化项。
- [x] 统一 recorder 输出：请求身份、Turn 身份、事件序号、owner、lease、终态和关闭顺序可诊断。
- [x] 场景与 Fake 使用 event 或零延迟让步同步；PTY/进程轮询只保留有界截止时间和完整超时事实。
  全仓普通测试的真实时间等待审计仍按阶段 6 逐项治理。
- [x] 固定所有随机 seed，并在失败 node id 或错误信息中输出 seed。
- [x] 为 harness 的隔离、资源清理、错误报告和确定性增加契约测试。
- [x] 保持 fake 与生产 port 的类型一致；第三方对象只在 adapter 测试边界出现。

完成门槛：同一场景连续运行结果一致；harness 自身失败可定位；领域支持层没有复制生产决策逻辑。

### 阶段 5：建立执行矩阵与 CI 门禁

- [ ] 为责任目录提供稳定的定向 pytest 命令，并写入 `tests/README.md`。
- [ ] 建立快速全量 job，排除真实 PTY 和经定义的 slow 集合。
- [ ] 建立独立 `runtime_p0` job，报告其与快速全量的重叠但不因此删除风险标记。
- [ ] 在 Windows、Linux、macOS 对应 runner 上运行各自平台 adapter 和 PTY 集合。
- [ ] 建立完整夜间回归，保留 junit 报告、最慢测试和失败 seed。
- [ ] 发布门禁运行完整测试、架构审计、compileall 和 `git diff --check`。
- [ ] 只有 CI 有真实选择需求时才注册 `slow`、`external_process`、平台或 `serial` marker。
- [ ] 审计固定端口、进程环境、单例缓存和共享目录后，再评估 pytest-xdist；并行结果必须与串行一致。

完成门槛：PR 能在合理时间内获得确定性反馈；平台失败不会被其他平台的 skip 掩盖；夜间失败可以
由 node id、seed 和事实 trace 重放。

### 阶段 6：风险覆盖和重复用例治理

- [ ] 为核心风险矩阵逐项标注现有快速、故障和验收证据，缺口进入功能 backlog。
- [ ] 统计按生产包和风险维度的覆盖率，只把它用于发现未执行路径，不设置脱离风险的总百分比目标。
- [ ] 对协议 parser、状态 reducer、权限 policy 和幂等 store 评估定向 mutation test，先限定小模块。
- [ ] 查找只验证 mock 调用而没有稳定结果或状态断言的用例，补充事实断言或删除低价值重复。
- [ ] 查找多个文件重复构造的大型字典，迁移到严格 builder 或版本化 fixture，未知字段仍必须失败。
- [ ] 查找依赖异常文本、日志文本或 UI 私有状态推断业务事实的测试，改测结构化事件和正式投影。
- [ ] 查找真实时间等待、未关闭 task/process/socket、执行顺序依赖和跨测试缓存污染。
- [ ] 对 flaky 用例先定位并修复所有权或同步问题，不以无界重试、放宽断言或长期 quarantine 收口。

完成门槛：关键不变量至少有一条快速测试；慢速测试提供快速层无法替代的证据；重复和 flaky 有
明确删除条件与责任所有者。

### 阶段 7：长期守护

- [x] 在架构审计中限制 `tests/` 根目录允许文件，保留 `conftest.py`、稳定审计入口和文档。
- [x] 审计测试不得直接导入 `backend`；客户端包禁止依赖 backend 的生产边界审计继续执行。
- [x] 审计不存在 `tests/support`、`tests.utils`、`tests.common` 等通用收纳包。
- [x] 审计可迁移测试的仓库路径来自 pytest 根 fixture，不再引入依赖目录深度的根路径推导。
- [ ] 新增测试评审模板：所有者、主要不变量、验证深度、关键失败路径、资源关闭、最小定向命令。
- [ ] 每季度检查最慢集合、最大模块、marker 规模、跳过原因和 flaky 记录。
- [ ] 当目录职责或 marker 语义变化时同步 `tests/README.md` 和稳定架构文档，不维护迁移流水账。

完成门槛：新测试可以按规则唯一归类；结构不会重新退化为根目录平铺或万能 harness。

## 每阶段统一验证

先激活仓库虚拟环境，再按改动范围执行：

```shell
python -m pytest <affected-targets> -q
python -m pytest --collect-only -q
python -m pytest -m runtime_p0 -q
python -m pytest tests/test_package_architecture.py tests/architecture -q
python -m compileall agent protocol frontends infrastructure observability metadata
git diff --check
```

约束如下：

- 普通目录迁移先跑目标目录和收集，完成一个阶段后才扩大到完整测试集；
- 修改根架构审计、包边界、pytest 配置或发布门禁时必须运行完整架构审计；
- 比较收集结果时使用该批次即时基线，不把本文中的 `4095` 写成永久断言；
- 收集数量相同不代表集合相同，必须同时比较规范化 node id；
- 测试路径改变导致的 node id 改变属于预期，模块名、函数名和参数 id 改变必须有明确理由；
- 若结构批次失败，回退该批次的纯移动和路径修正，不用兼容导入、转发模块或重复测试维持两套路径。

## 建议提交顺序

1. pytest 根路径 fixture、具名测试设施目录与 `tests/README.md`；
2. protocol、distribution、infrastructure 纯移动；
3. 非 TUI frontend 纯移动；
4. Agent 纯移动；
5. TUI、PTY 和 manual 纯移动；
6. 稳定文档路径与架构目录守护；
7. 巨型文件逐个拆分，每个文件独立提交；
8. 领域 harness 收敛与 harness 契约测试；
9. marker、CI 执行矩阵与平台门禁；
10. 风险覆盖、重复用例和 flaky 治理。

每个提交应能独立收集并回退。纯结构提交不包含生产代码行为变化；行为修复提交不夹带大规模
文件移动。

## 完成定义

- [ ] 所有长期有效规则和命令已收敛到 `tests/README.md`，本文仅剩可删除的完成记录；
- [ ] `tests/` 根目录只保留约定的稳定入口、配置和说明文件；
- [ ] 每个自动化测试存在唯一、可解释的责任所有者；
- [ ] backend 独立服务实现不由客户端测试树直接导入或验证；
- [ ] 当前五个风险 marker 有稳定选择命令，新增 marker 均有实际 CI 消费者；
- [ ] 不存在依赖测试文件目录深度的仓库根解析；
- [ ] 不存在无说明的超大多职责测试模块；
- [ ] Turn、审批、工具、replay、终态、资源关闭和 TUI 原子帧均有快速确定性门禁；
- [ ] Fake Server、场景模型和 PTY harness 各自拥有明确契约与关闭生命周期，不存在通用 support 包；
- [ ] 自动化测试不访问公网、不依赖真实用户配置、不依赖执行顺序；
- [ ] 定向、PR、Runtime P0、平台验收、夜间和发布门禁各有清晰职责；
- [ ] 全量测试、架构审计、compileall 和差异检查通过；
- [ ] `AGENTS.md`、架构文档、PTY 文档和测试说明中的稳定路径保持有效。
