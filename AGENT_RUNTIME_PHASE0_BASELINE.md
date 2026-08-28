# Agent Runtime 阶段 0 验收基线

状态：验收通过（2026-08-28）

本文把阶段 0 的导入、行为和独立打包边界变成可重复执行的验收矩阵。完整历史
测试数量只作为时间点证据；阶段退出以本矩阵在当前工作树通过为准。

## 固化产物

| 产物 | 用途 | 漂移检查 |
| --- | --- | --- |
| `AGENT_RUNTIME_ARCHITECTURE.md` | 目标 ADR | 文档检查和架构测试 |
| `AGENT_RUNTIME_MIGRATION.md` | 阶段状态唯一权威 | 人工审阅和文档检查 |
| `AGENT_RUNTIME_CONTRACT_BASELINE.md` | 外部 schema 与 Command/Event 映射 | 定向契约测试 |
| `AGENT_RUNTIME_IMPORT_GRAPH.md` | 当前第一方生产/构建依赖 | 生成器 `--check` 和 pytest |
| 本文 | 可重复测试、backend 和风险基线 | 按命令执行 |

## 行为测试矩阵

| 场景 | 必须覆盖的行为 | 定向测试 |
| --- | --- | --- |
| 主动 Turn | 无 TUI 可执行；并发上下文隔离；失败/取消均关闭 owner | `tests/test_turn_execution.py::test_execute_turn_does_not_require_root_conversation_or_frontend`；`::test_concurrent_turn_executions_keep_contexts_isolated`；`::test_execute_turn_closes_owned_report_for_failure_and_cancellation` |
| 结构化终态 | completed/failed/incomplete 结果和退出码稳定 | `tests/test_run_result.py::test_stream_returns_completed_result`；`::test_stream_returns_failed_result`；`::test_stream_without_terminal_event_is_incomplete` |
| 工具结果 | 结果携带 TurnContext；未知确认先对账；重试保留 request id | `tests/test_run_result.py::test_stream_reports_client_tool_result_from_turn_context`；`::test_stream_reconciles_uncertain_tool_result_before_failing`；`::test_stream_retries_unknown_ack_with_same_request_id` |
| 工具传输契约 | 只发送协议字段并拒绝无效 request id | `tests/test_tool_requests.py::test_tool_result_posts_only_transport_fields`；`::test_tool_result_rejects_invalid_request_id` |
| 审批 | 类型化审批、取消收束和 ack 坐标校验 | `tests/test_run_result.py::test_stream_uses_typed_approval_before_client_tool_call`；`::test_cancelled_approval_drains_interrupted_turn_settlement`；`tests/test_tool_requests.py::test_approval_ack_rejects_mismatched_coordinates` |
| 本地/远端取消 | 先通知远端；本地中断不等待网络 | `tests/test_run_result.py::test_interrupted_turn_notifies_before_stream_cleanup`；`tests/test_tui_turn_input.py::test_local_interrupt_does_not_wait_for_remote_request` |
| 断线恢复 | 从最后确认序号 attach、去重重放、补序号缺口、先恢复审批快照 | `tests/test_chat_stream.py::test_disconnect_attaches_after_last_sequence_and_deduplicates_replay`；`::test_sequence_gap_attaches_from_last_confirmed_event`；`::test_attach_restores_approval_snapshot_before_replay` |
| Subscription | 暂停后续接会话；任务取消上报 cancelled | `tests/test_agent_runtime.py::test_agent_supervisor_resumes_session_after_pause`；`tests/test_agent_forwarding.py::test_agent_executor_reports_task_cancellation_as_cancelled` |
| backend 边界 | 应用不导入 backend；backend 不导入应用包 | `tests/test_package_architecture.py::test_application_does_not_import_packaged_backend`；`::test_packaged_backend_is_self_contained` |
| backend 工具 schema | 已删除工具不注册，Nexus 只接受规范字段 | `tests/test_backend_tool_contracts.py` |
| 构建辅助 | sidecar 完整性和扩展归一化顺序稳定 | `tests/test_build_packaging.py` |

推荐的一次性阶段 0 命令：

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_agent_runtime_baseline.py tests/test_turn_execution.py tests/test_run_result.py tests/test_tool_requests.py tests/test_tui_turn_input.py tests/test_chat_stream.py tests/test_agent_runtime.py tests/test_agent_forwarding.py tests/test_package_architecture.py tests/test_backend_tool_contracts.py tests/test_build_packaging.py
```

## 结构与语法检查

```powershell
.\venv\Scripts\python.exe scripts\agent_runtime_import_graph.py --check
.\venv\Scripts\python.exe -m py_compile scripts\agent_runtime_import_graph.py tests\test_agent_runtime_baseline.py
python website\mind\scripts\check_docs.py
git diff --check
```

共享协议或入口开始迁移后，每个阶段退出前还必须运行完整 pytest 套件。

## backend 独立边界

阶段 0 冻结时，当前提交中的 `backend/` Git tree 为：

```text
cecce05e9a208dbe43dfd2ce98713de3cc4a9f3f
```

顶层目录/文件基线：

```text
__init__.py
compile.bat
compile.md
compile.sh
helix.py
mcp_core/
mcp_hub/
mcp_tools/
middlewares/
models/
register.py
requires/
routers/
utilities/
web/
```

边界规则由 `tests/test_package_architecture.py` 执行：`backend/` 只依赖自身、
标准库和第三方库，应用包不得导入 `backend`。`tests/test_backend_tool_contracts.py`
冻结对外工具 schema；`tests/test_build_packaging.py` 冻结当前构建辅助行为。

Agent Runtime 迁移不修改 `backend/`。若其他工作确实修改它，应在独立变更中更新
tree 证据；不能在 Agent Runtime 阶段验收中把 backend 漂移视为迁移成果。

## 已知风险基线

| 风险 | 当前证据 | 最晚处理阶段 | 约束 |
| --- | --- | --- | --- |
| `engine` 与 `mind_core` 跨边界循环 | `engine/enhance/handlers.py` 导入 `mind_core`；多个 `mind_core` 模块导入 `engine` | 阶段 3 | 作为 capability 迁移债务，不在阶段 0 无关重构 |
| `mind_app` 直接依赖 `engine`、`mind_core`、`mind_nova`、`server` | 见生成导入图 | 阶段 3-5 | 新 `agent.protocol/domain/runtime/application` 不复制这些边 |
| 本地与线上均出现 `run_id` / sequence 概念 | 契约基线的身份表 | 阶段 1 | adapter 保存具名映射，禁止隐式等同 |
| 工具结果存在“请求成功但确认未知”窗口 | status 查询与同 request id 重试测试 | 阶段 2 | durable effect 进入 unknown 后先对账 |
| Subscription 和主动入口目前走不同编排 | forwarding 与 root turn 入口 | 阶段 4 | 共享 Command/Run 语义，连接生命周期仍留 adapter |
| Transcript 与 SQLite history 是两类存储 | `history/transcript.py` 与 `history/store.py` | 阶段 2 | 保留旧数据读取并进行版本迁移 |

## 验收记录

执行通过后，在这里记录当前工作树结果，并同步更新迁移计划；未执行或失败时不得把
阶段 0 标记为完成。

| 日期 | 结果 | 证据 |
| --- | --- | --- |
| 2026-08-28 | 通过 | 定向矩阵 `222 passed`；导入图 `--check`、`py_compile`、文档检查和 `git diff --check` 均通过 |
