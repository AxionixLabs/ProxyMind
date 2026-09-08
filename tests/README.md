# ProxyMind 测试

测试集使用 pytest 作为执行内核，目录按 `ARCHITECTURE.md` 中的稳定责任所有者组织，marker
只表达跨目录的风险门禁或环境要求。本文是测试归属、测试设施和执行方式的唯一长期说明。

## 归属规则

1. 单一生产责任的测试镜像到对应顶层包和最近的稳定职责目录。
2. 同一顶层包内的跨层用例归到拥有该生命周期的 application 或 harness。
3. 必须组合两个以上顶层所有者才能表达主要事实的测试归入 `integration/`。
4. 根组合和资源 owner 归入 `composition/`，构建与发布布局归入 `distribution/`。
5. 人工连接真实服务、设备或账号的脚本归入 `manual/`，且文件名不以 `test_` 开头。
6. `test_package_architecture.py` 是稳定的根级架构审计入口，不移动、不删除。
7. 客户端测试不直接验证独立 `backend/` 服务包，但保留客户端禁止依赖 backend 的边界审计。

目录不用于表达 unit、integration、slow 或平台属性。新增 marker 前必须先有实际的测试选择命令
或 CI 消费者。

责任目录迁移已经完成。`tests/` 根目录只保留全树配置、本文和稳定架构审计入口；测试分别归入
`agent/`、`protocol/`、`frontends/`、`infrastructure/`、`observability/`、`distribution/`、
`composition/` 与 `integration/`。测试设施归入 `scenarios/`、`fakes/`、`pty/`、`fixtures/`，
人工联调入口归入 `manual/`。不得为了清空目录把跨层测试随意塞入单一生产责任目录。

| 目录              | 主要责任                                                                        |
|-------------------|---------------------------------------------------------------------------------|
| `agent/`          | domain、application、harness、stores、capabilities 与 adapters                  |
| `protocol/`       | schema、transport 与独立 client 契约                                            |
| `frontends/`      | CLI、MCP、subscription、terminal、TUI 及输出边界                                |
| `infrastructure/` | config、hooks、MCP、persistence、platform、services、sidecars、skills 与 update |
| `observability/`  | 运行报告、结构化观察与记录边界                                                  |
| `distribution/`   | build、npm 与发布布局                                                           |
| `composition/`    | 根组合入口和进程资源所有权                                                      |
| `integration/`    | 必须组合两个以上顶层所有者才能成立的客户端用例                                  |
| `architecture/`   | 从根稳定入口拆出的专题源码与依赖审计                                            |

## 测试设施

- `scenarios/turns.py`：Turn 场景、确定性组合选择和 Runtime 稳定不变量；
- `scenarios/frames.py`：逻辑 frame 事实与跨帧原子性断言；
- `fakes/mind_chat.py`：不可变故障计划、正式命令端口和请求/事件事实记录；
- `pty/`：跨平台 PTY、终端输入和渲染验收设施；
- `fixtures/`：无行为的正式协议、配置和展示输入；
- `frontends/tui/rendering/frame_scenarios.py`：TUI frame 输入构造、同步等待与可见事实读取；
- `architecture/source_inventory.py`：架构审计专用的源码清单和进程内 AST 缓存。

公共 helper 只在至少三个测试模块共享稳定概念，或重复已经造成契约不一致时提取。fake 只实现
它声明的端口，不能复制生产状态机来计算期望结果。禁止新增 `support/`、`utils/`、`common/`
等通用收纳模块；仓库路径通过根 `conftest.py` 的 pytest fixture 显式注入。

Turn 场景使用调用方提供的固定 seed，并在失败信息中输出 seed 与完整 trace。Runtime trace 包含
Turn/epoch、事件序号、cursor、输入 owner、lease、终态和执行门状态；Fake Server trace 包含命令
请求身份、Turn、事件序号、输入 owner 和工具结果状态。Fake Server 不创建后台 task、socket 或
进程；PTY 资源由 context manager 关闭。场景与 Fake 使用 event 或零延迟让步同步，真实 PTY、
sidecar 和终端时序可以保留带明确截止时间的轮询。

## 结构守护

`test_package_architecture.py` 保留全仓入口与基础依赖方向，`architecture/` 承载 Agent、基础设施、
前端和组合根的专题审计。两者共享同一源码清单与 AST 缓存，并共同审计以下测试架构边界：

- `tests/` 根目录只允许 `conftest.py`、`README.md` 和稳定架构审计入口；
- 不允许新增 `support/`、`utils/`、`common/` 等通用测试包；
- 客户端测试不得直接导入独立 `backend` 服务实现；
- 可迁移测试不得通过 `Path(__file__).parents[...]` 推导仓库根。

这些规则属于发布收口边界。普通业务迭代只运行受影响目录；目录、依赖方向或发布门禁变化时才
运行完整架构审计。

## 运行方式

先激活仓库虚拟环境，再运行最小受影响集合：

```shell
python -m pytest <targets> -q
python -m pytest tests/agent -q
python -m pytest tests/protocol -q
python -m pytest tests/infrastructure -q
python -m pytest tests/distribution -q
python -m pytest tests/frontends -m "not pty_acceptance" -q
python -m pytest tests/integration -m "not pty_acceptance" -q
python -m pytest --collect-only -q
python -m pytest -m runtime_p0 -q
python -m pytest tests/test_package_architecture.py tests/architecture -q
```

真实 PTY 或 ConPTY 使用 `-m pty_acceptance`，故障注入使用 `-m runtime_fault`，逻辑帧使用
`-m runtime_frame`，固定 seed 的状态长序列使用 `-m runtime_stateful`。平台测试必须在对应平台
执行，缺少平台不能视为该门禁通过。

## CI 门禁

`.github/workflows/tests.yml` 使用 Python 3.11，并把责任目录与执行属性保持正交：

| job               | 触发                 | 选择                                                   | 职责                           |
|-------------------|----------------------|--------------------------------------------------------|--------------------------------|
| `fast`            | PR、main             | `-m "not pty_acceptance"`                              | 快速全量合并门禁               |
| `runtime-p0`      | PR、main             | `-m runtime_p0`                                        | Runtime 核心风险独立门禁       |
| `platform`        | 全部                 | 三平台的 `infrastructure/platform` 与 `pty_acceptance` | 平台 adapter 和真实终端        |
| `full-regression` | 夜间、手动、版本标签 | 完整测试树                                             | 完整回归、最慢 50 项和 JUnit   |
| `release-gate`    | 版本标签             | 架构审计、compileall、差异检查                         | 汇总完整回归与三平台结果后收口 |

每个 pytest job 都上传 JUnit；PR 报告保留 14 天，完整回归保留 30 天。`runtime_p0` 与快速全量
有意重叠，因为前者是可单独要求的风险门禁。源码仓不包含 macOS sandbox 可执行产物；对应的
sidecar 集成测试只在外部产物流水线提供 `MIND_SANDBOX_SERVER` 时成立，不计入源码 CI 的 macOS
通过结论。当前不启用 pytest-xdist：进程环境、固定端口和跨进程资产尚未完成并行隔离证明。

## 核心风险证据

以下 node id 是各风险的最小稳定证据，可直接传给 `python -m pytest <node-id> -q`。扩展证据只在
跨层故障或真实终端能提供快速测试无法证明的事实时保留；没有独立价值的风险不机械增加 PTY 用例。

| 风险                        | 快速证据                                                                                                                          | 扩展证据                                                                                                                      |
|-----------------------------|-----------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| Session、Run、Turn 身份隔离 | `tests/agent/application/turns/test_execution_context.py::test_root_agent_and_turn_context_share_session_identity`                | `tests/integration/test_turn_fault_injection.py::test_fake_sse_duplicate_gap_and_late_event_feed_same_oracle`                 |
| 幂等键和请求身份            | `tests/agent/application/turns/test_durable_queue_application.py::test_enqueue_persists_before_network_and_retries_same_identity` | `tests/integration/test_turn_fault_injection.py::test_committed_steer_response_loss_reconciles_without_resubmit`              |
| 权威终态和输入门            | `tests/agent/application/turns/test_stream_turn_outcome.py::test_outcome_marks_stream_without_terminal_as_incomplete`             | `tests/integration/test_turn_fault_injection.py::test_production_interrupt_matrix_preserves_gate_and_input_ownership`         |
| 工具副作用恰好一次          | `tests/agent/stores/effects/test_durable_local_effects.py::test_effect_journal_never_replays_uncertain_manual_effect`             | `tests/frontends/tui/acceptance/test_pty_tui_rendering.py::test_tool_approval_effect_and_shell_states_converge_in_place`      |
| 审批裁决归属和过期          | `tests/agent/domain/approvals/test_approval_domain_contract.py::test_amendment_must_match_the_action_fingerprint`                 | `tests/frontends/tui/acceptance/test_pty_tui_interaction.py::test_approval_key_matrix_is_modal_and_exactly_once`              |
| replay、gap 和旧 epoch      | `tests/agent/application/turns/test_run_result_lifecycle.py::test_provider_retry_ignores_late_old_item_events`                    | `tests/frontends/tui/acceptance/test_pty_tui_rendering.py::test_stream_retry_keeps_attempt_order_and_exactly_once_final_text` |
| 资源关闭                    | `tests/agent/harness/execution/test_execution_resources.py::test_execution_resources_close_owned_runtime_resources`               | `tests/frontends/tui/acceptance/test_pty_tui_rendering.py::test_terminal_modes_restore_after_render_failure`                  |
| TUI frame 原子性            | `tests/frontends/tui/runtime/test_tui_frame_contract.py::test_typed_event_trace_satisfies_frame_contract`                         | `tests/frontends/tui/acceptance/test_pty_tui_rendering.py::test_dynamic_layout_regions_do_not_overlap`                        |
| 严格 wire 契约              | `tests/protocol/schema/test_stream_event_protocol.py::test_stream_event_rejects_removed_fields`                                   | `tests/protocol/client/test_tool_requests.py::test_tool_result_rejects_noncanonical_envelopes`                                |

## 覆盖率与变异评估

覆盖率用于发现未执行分支，不作为脱离风险的总百分比门槛。夜间和版本标签的完整回归使用
`pytest-cov` 对 `agent`、`protocol`、`frontends`、`infrastructure`、`observability`、`metadata`
统计 branch coverage，并上传 `coverage.xml`。本地复现命令如下：

```shell
python -m pytest -q --cov=agent --cov=protocol --cov=frontends --cov=infrastructure --cov=observability --cov=metadata --cov-branch --cov-report=term-missing
```

风险覆盖按上一节的 node id 和 `runtime_p0`、`runtime_fault`、`runtime_frame`、`runtime_stateful`
证据判断，不把 marker 数量折算为百分比。定向 mutation 只考虑
`protocol/schema/stream_events.py`、`agent/adapters/protocol/items.py`、
`agent/domain/approvals/rules.py` 和 `agent/stores/effects/journal.py`。mutation 工具暂不进入默认依赖
或 CI；先审阅 branch gap，再以单模块试点验证 survivor 是否对应真实风险缺口。

## 确定性与隔离

`tests/architecture/test_test_quality.py` 审计自动化测试不直接写 `os.environ`、不使用无 seed 的模块级
随机、不引入 retry 或 quarantine marker、不直接调用公网入口，并要求 `scenarios/`、`fakes/`
只用 event 或零延迟让步同步。需要验证环境 adapter 的用例只允许使用 pytest `monkeypatch` 做可恢复
修改；启动本地服务必须绑定动态端口并由创建方关闭。

只验证 mock 调用并非自动判定为低价值：当调用对象是正式 port、终端写入或“不得发生的副作用”时，
调用事实就是稳定结果；否则必须补充返回值、状态、结构化事件或正式投影断言。跨 schema、adapter、
fake 重复出现的 wire 字典仅在内容完全相同且共享不会掩盖未知字段时提取；不同边界的独立严格向量
继续留在各自 owner。

## 新增测试检查

- 主要断言是否属于唯一责任所有者和同一稳定不变量；
- 是否覆盖核心成功路径和关键失败路径，而不是只验证 mock 调用；
- 外部字典是否先经过正式边界校验，未知字段是否明确失败；
- clock、transport、路径和进程能力是否从边界注入；
- task、socket、进程、PTY、sidecar 和临时资源是否由创建方完整关闭；
- 失败信息是否包含必要的身份、序号、阶段、seed 和观察事实；
- 是否可以通过稳定的最小 pytest 命令独立运行。
- 超过约 1500 行时是否仍只覆盖一个状态机或不变量矩阵，并在模块顶部说明保持整体的原因。
