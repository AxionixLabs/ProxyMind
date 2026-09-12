# 外接 MCP 单服务控制：方案评估、设计稿与分阶段验收清单

状态：P0 已完成边界复核、具名验收输入和真实 MCP fixture；P1—P5 尚未实施，单服务控制和新菜单尚未接入产品。
本文件统一维护方案、菜单与交互设计、分阶段清单、验收运行说明及证据；测试目录不另设验收说明文档。
本文的阶段、自动测试和真机验收项均须凭对应证据勾选，源码阅读、测试替身或设计截图不能代替真机通过。

## 1. 目标与依据

用户要解决的是：一个外接 MCP 服务需要启动、停止、重试或排障时，其他服务保持可用；交互遵循现有 TUI 菜单。
菜单不放 `All servers`；裸 `/mcp` 选择单个服务，带动作的直接命令默认管理全部外接服务。
本方案同时评估菜单、动作语义和交互细节，不把既有实现中的问题当成必须保留的行为。

职责、依赖和生命周期以 [ARCHITECTURE.md](ARCHITECTURE.md) 为准，跨系统边界以 [ARCHITECTURE_SYSTEM.md](ARCHITECTURE_SYSTEM.md) 为准。
本功能属于客户端本地连接管理，不新增 AppServer/Fabric 端点、线上事件或远程服务器管理能力。
本文不是新的架构权威；实现完成时将稳定行为同步进 [交互模式文档](docs/interactive-mode.md)，本清单只承担实施与验收跟踪。

| 已核对的实现                                                                                           | 当前事实及设计影响                                                                    |
|--------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| [MCP 菜单](frontends/tui/features/mcp.py)                                                              | 当前菜单直接列出五个全量动作；`force` 在已有连接时调用整组重启；有工具时默认选 `stop` |
| [菜单契约](frontends/tui/contracts/menu.py)、[菜单行布局](frontends/tui/rendering/menu/rows.py)        | 已有标题、简短说明、编号、选择标记、标准页脚和窄屏说明换行；复用这些能力              |
| [Review](frontends/tui/features/review.py)、[Mailbox](frontends/tui/features/mailbox.py)               | 已有压入子菜单、返回父菜单和子项确认后关闭菜单组的模式                                |
| [MCP owner](agent/harness/mcp/owner.py)、[运行时端口](agent/ports/mcp_runtime.py)                      | Harness 拥有实例生命周期；公开操作尚无单服务目标                                      |
| [外部运行时](infrastructure/mcp/external_runtime.py)                                                   | 当前 `started` 会使整组 `start` 提前返回；整组停止会清除 group                        |
| [连接组](infrastructure/mcp/external_group.py)                                                         | 每个服务已有独立 owner task、关闭信号和资源栈；工具和路由目前整组清理                 |
| [工具组合](infrastructure/mcp/tool_runtime.py)、[组合会话](infrastructure/mcp/composite_session.py)    | 工具来源在进入使用范围时确定；旧工具快照和底层连接的生命周期必须协调                  |
| [流式命令策略](frontends/tui/prompting/commands.py)、[前台任务屏障](frontends/tui/session/barriers.py) | 当前运行中 `stop/restart` 被拒绝，`status` 是本地快照，部分启动操作经屏障执行         |
| [工作区切换](agent/harness/sessions/workspace_change.py)                                               | 切换时移交旧 MCP owner、释放旧资源并重建；旧工作区回调不能重新发布工具                |

## 2. 整体评估与推荐取舍

### 2.1 菜单入口

| 方案                                  | 优点                                                 | 问题                                                         | 结论   |
|---------------------------------------|------------------------------------------------------|--------------------------------------------------------------|--------|
| 服务列表 → 单服务操作；全量用直接命令 | 对象先于动作，最适合独立排障；菜单没有批量误操作入口 | 全量命令的作用范围需要明确提示                               | 推荐   |
| 服务列表中增加 `All servers`          | 全量操作可发现性高                                   | 把批量与单服务放在同一选择列表，容易选错；不符合本次菜单要求 | 不采用 |
| 动作 → 服务列表                       | 延续当前动作入口                                     | 看不到服务状态就先选动作；先选 `stop` 再选对象容易出错       | 不采用 |
| 每行直接展开五个动作或横向按钮        | 少一层导航                                           | 长名称、窄窗口和键盘焦点变复杂，偏离现有菜单                 | 不采用 |

裸 `/mcp` 与 `/mcp stop` 的差异本身不是漏洞，但必须在菜单说明、命令帮助和操作反馈中一致表达。
不能把“菜单关闭后没有选到服务”、未知服务或无效参数解释成“全部”。

### 2.2 需要主动修正的语义

1. **`force` 不承担重启。** 推荐将其限定为“临时越过 `enabled=false` 的启动”，已连接服务保持连接。需要重建连接时明确选择 `restart`。这会替换当前 `force` 对已有连接执行重启的行为，必须一起修改文案与测试。
2. **`start` 按服务幂等。** 全量 `start` 补启动尚未连接且配置启用的服务，不得因其他服务已连接而整组跳过。已通过 `force` 建立的连接也不会因普通 `start` 被关闭。
3. **操作菜单默认选 `status`。** 五项顺序保持不变，默认选中项与运行状态解耦，避免在一级、二级连续按 Enter 时执行 `stop` 或临时启用。复用菜单视觉和键位，不延续当前危险的默认高亮规则。
4. **配置开关不等于运行状态。** `disabled` 不能冒充 `stopped`；禁用服务通过 `force` 启动后应显示 `ready · disabled · temporary`。零工具也可能是有效连接。
5. **停止不能只看当前调用数量。** 活动 Turn 可能稍后使用冻结目录里的工具。生命周期门禁必须覆盖持有该服务工具快照的消费者，而不只是正在等待响应的请求。

`force` 保留为第五个动作，是为了继续区分“遵守配置的启动”和“本次运行临时启用”。不把 `enabled` 改名或悄悄解释为另一种配置含义，也不引入 `force stop`。

另一个容易误解的边界是临时启用后的 `restart`。本方案保留“重读配置、按 enabled 重建”的语义，不让临时覆盖在重启后隐式延续。
因此禁用服务 restart 后可能停止；菜单必须写明 `if enabled`，结果必须报告 `stopped · disabled in configuration`，不能显示 restarted。
这样全量与单服务 restart 使用同一规则，force 的生命周期也有明确结束边界。

### 2.3 范围控制

- 首期支持 `/mcp` 和 `/mcp start|force|stop|restart|status`。不扩展带服务名的直接命令；单服务目标从菜单产生。
- 多余参数、未知动作必须作为本地命令错误处理，不能退化成全量动作或普通模型输入。
- `mind mcp` 仍是配置注册 CLI；不在本次增加后台守护进程、跨进程 IPC、服务自动重连或远程 kill。
- stdio 管理本客户端创建的连接及子进程；HTTP/SSE 只连接、断开和重连，不能关闭远端进程。
- 新增能力复用现有 runtime、group、owner 和菜单栈。菜单改动小；真正的工作量集中在连接状态、工具快照、资源关闭及多消费者门禁。

## 3. 动作与状态契约

### 3.1 作用范围与五个动作

| 入口/动作 | 单服务菜单                                                                             | 全量直接命令                                                             |
|-----------|----------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| `start`   | 启动配置启用且未连接的目标；已连接返回 `already ready`；禁用且未连接时提示使用 `force` | `/mcp start` 补启动全部配置启用且未连接的服务，保持已有连接              |
| `force`   | 临时启动目标，允许 `enabled=false`；已连接返回 `already ready`                         | `/mcp force` 补启动全部未连接的配置服务，包括禁用服务，保持已有连接      |
| `stop`    | 关闭目标并撤下其工具；已停止返回 `already stopped`                                     | `/mcp stop` 关闭全部本实例持有的外接连接，包括临时启用和已移出配置的连接 |
| `restart` | 先校验目标新配置，再关闭并按 `enabled` 重建；配置禁用时收束为停止，不隐式 `force`      | `/mcp restart` 先校验有效配置，再关闭全部连接，仅重建配置启用的服务      |
| `status`  | 输出目标连接、配置和工具详情                                                           | `/mcp status` 输出所有配置服务及本实例仍持有的连接状态                   |

- 这五个动作均不写配置文件；临时启用一直有效到该连接被停止、重建、工作区切换或客户端退出。
- 配置读取/校验失败时不拆除健康连接。重启开始关闭后，新连接建立失败可以使目标处于失败状态，不自动恢复旧连接或旧配置。
- 全量 `start/force` 对已有服务和本批新增服务分别处理：新增 `required` 服务失败，回收本批新建连接，保留操作前的连接；冷启动时操作前连接为空，因此保持现有 required 启动失败收束语义。
- 全量 `restart` 是显式允许断开所有连接的操作；其重建阶段 required 失败时回收本批新建连接，不能声称其他旧连接仍被保留。
- 单服务失败（包括 `required`）只收束目标，不能升级成其他健康服务的停止。退出及工作区切换仍执行全量最终释放。

### 3.2 身份和状态

服务操作以有效配置的原始 `config_key` 定位，显示名称、规范化工具前缀和列表序号不承担身份。
全量目标使用显式具名类型，与单服务目标和取消选择分开；不能用服务名字符串 `all`、空字符串或找不到键作为全量哨兵。
运行实例/工作区身份与目标一起校验，菜单打开后切换工作区或配置删除不能让旧选择作用于新实例。

| 连接状态   | 含义                         | 展示/收束要求                                                          |
|------------|------------------------------|------------------------------------------------------------------------|
| `stopped`  | 未连接且没有待释放资源       | 工具数为 0；配置可为 enabled 或 disabled                               |
| `starting` | 正在准备、握手或发现工具     | 不能提前发布不完整目录；重复启动不新建第二条连接                       |
| `ready`    | 已建立并发布的连接           | 工具可以为 0；不能用工具数判断连接是否存在                             |
| `stopping` | 已阻止新的使用，正在释放资源 | 重启不能越过旧资源收束阶段                                             |
| `failed`   | 建连失败或已确认连接不可用   | 保留可展示的失败原因；仍在清理的资源必须继续有 owner，不能伪装成已停止 |

`restart` 是动作，其连接状态经过 `stopping → starting → ready/failed`；无需新增第二套重启状态机。
操作被门禁拒绝、配置无效等错误不代表健康连接断开，应保持实际连接状态并单独报告操作失败。
`status` 读取本地事实，不主动建连或调用健康探测；`ready` 表示已知的连接状态，不保证远端下一次调用必定成功。
已观察到的 task 退出、传输失败或服务异常必须更新状态并撤下不可用工具，不能长期保留虚假的 ready。
工具发现请求失败不能按“成功发现零工具”发布 ready。只有服务明确不支持工具、成功返回空目录或全部工具被过滤，才是有效的零工具连接。

菜单服务列表取“当前有效配置与本实例仍持有连接”的并集。配置删除后的连接显示 `removed from config`，仍可 `stop/status`；`start/force/restart` 明确报告缺少配置，不能复活旧配置。
工具前缀在连接存活期间保持稳定；单独重启 A 不得改变 B 的工具名或把新连接覆盖到 B 的路由。
不要只对选中的配置条目重新规范化名称；必须核对完整配置与当前已占用前缀。新配置与存活连接冲突时报告目标冲突，不重命名其他连接或覆盖路由。

## 4. 菜单设计稿

下列是等宽设计示意；实际颜色、缩进、行间距、选择标记、序号与页脚均由现有菜单 renderer 和 Runtime Keymap 生成。
保留英文产品文案、短标签加说明的形式，使用 `STACK_BELOW_WHEN_NARROW`，不手工拼固定列宽或新增一套面板样式。

### M1：一级，只选择单个服务

```text
External MCP
Select a service; /mcp <action> manages all services.

› 1. github        ready · stdio · 8 tools
  2. filesystem    stopped · stdio
  3. browser       failed · streamable_http
  4. database      stopped · disabled · stdio

Press enter to confirm or esc to go back
```

- 不放 `All servers`，不放五个全量动作，也不额外增加 `Back` 行。
- 服务按显示名称稳定排序，身份仍使用原始配置键；第一次打开选择第一项，返回时保留原服务和滚动位置。
- 行说明只放连接状态、传输、工具数以及必要的 disabled/temporary/removed 标记；完整错误和工具列表由 `status` 展示。
- 配置禁用不使服务行不可选，否则无法进入 `force`。
- 服务较多时复用菜单现有滚动、翻页和序号选择；首期不新增搜索输入或页签。

### M2：二级，五个单服务动作

```text
External MCP — github
ready · stdio · enabled · 8 tools

  1. start      Start this service if enabled; keep its current connection.
  2. force      Start this service even if disabled; keep its current connection.
  3. stop       Disconnect this service and close its child process.
  4. restart    Reload configuration and reconnect this service if enabled.
› 5. status     Show this service's status and tools.

Press enter to confirm or esc to go back
```

- 五项始终显示、顺序固定；第一次进入二级默认 `status`。有连接、无工具、禁用和失败都不改变默认动作。
- HTTP/SSE 的 stop 说明为 `Disconnect this service; the remote server keeps running.`；不显示关闭远端进程的承诺。
- restart 的说明保留 `if enabled`，让临时启用的禁用服务重启后停止这一结果可以预期。
- 无效配置、目标消失或 busy 在执行入口再次校验，输出准确原因；不依赖菜单项是否灰显保证安全。

### M3：禁用服务临时启动后的菜单

```text
External MCP — database
ready · stdio · disabled · temporary · 4 tools

  1. start      Start this service if enabled; keep its current connection.
  2. force      Start this service even if disabled; keep its current connection.
  3. stop       Disconnect this service and close its child process.
  4. restart    Reload configuration and reconnect this service if enabled.
› 5. status     Show this service's status and tools.

Press enter to confirm or esc to go back
```

这里的 disabled 是配置事实，ready 是连接事实；不改为只有 `disabled` 的状态行。

### M4：窄窗口与长名称

```text
External MCP — github
ready · stdio · enabled · 8 tools

  1. start
     Start this service if enabled;
     keep its current connection.
  2. force
     Start this service even if disabled;
     keep its current connection.
  ...

Press enter to confirm or esc to go back
```

省略号仅表示设计稿未画出的后续选项，产品仍由现有滚动行为展示全部五项。
说明移到下一行，标题、状态、页脚按共享布局约束裁剪或换行；不让名称覆盖序号、输入区域或活动状态。
窗口变化后保持选中服务/动作及键盘焦点；颜色不足时仍可通过文字、序号和选择标记识别状态。

### M5：空配置与配置错误

```text
External MCP
Select a service to manage.

No MCP services configured.

Press enter or esc to close
```

空配置没有可执行服务行；使用现有关闭页脚，Enter/Esc 只关闭菜单，不启动全量操作。
配置无效且无已知连接时用同一表面显示一次脱敏错误，不同时在标题和正文重复错误标记。
配置无效但有已知连接时仍列出这些连接，允许停止和查看；配置依赖的操作报告配置错误。

## 5. 交互设计稿与执行规则

### I1：导航、确认和返回

```text
输入 /mcp
    → M1 服务列表
        → Enter：M2 单服务操作（默认 status）
            → Esc：返回 M1，恢复原服务及滚动位置
            → Enter：产生具名目标与动作，关闭本组菜单
                → status：正文输出目标详情 → 回到输入框
                → 其他动作：前台活动区执行 → 提交最终结果 → 回到输入框
        → Esc：回到原输入区域

输入 /mcp stop
    → 不打开菜单，产生明确的全量 stop
    → 前台活动区执行 → 提交全量结果 → 回到输入框
```

复用现有菜单栈、导航事件与领域操作事件边界。子菜单确认时关闭整组菜单；取消只返回，不触发领域操作。
操作开始后不自动重新打开菜单；用户再次输入 `/mcp` 时读取最新事实。
不增加正常启停的二次确认。动作目标清晰、默认 status、执行门禁和结果反馈共同保证操作可预测。

### I2：进行态与最终结果

```text
单服务进行态：
• External MCP github starting

单服务最终态：
■ External MCP github ready · 8 tools

已连接时重复 start/force：
■ External MCP github already ready · 8 tools

单服务停止成功：
■ External MCP github stopped

全量停止进行态：
• External MCP stopping all services

全量最终态：
■ External MCP stopped · 3 services

单服务失败：
■ External MCP browser start failed
  └ startup timed out after 10s
```

标记、颜色、动画、树形详情和最终块使用现有 MCP/operation 活动区与终端样式；数字来自真实结果，不能固定。
单服务结果必须携带服务名；全量结果显式表达全部及各项失败。一次操作只提交一次最终结果，不残留进度动画。
动画禁用时仍输出相同的真实最终结果。异步旧结果不能覆盖新操作或新工作区的状态。

### I3：status 输出到正文

```text
External MCP — github

  • Connection: ready
  • Config: enabled
  • Transport: stdio
  • Tools: 8 available · 2 filtered
  • Last connection error: none
```

复用现有状态输出块，按目标筛选并列出工具；不增加第三层全屏状态页。
连接错误和本次管理操作被拒绝应分别表达；配置无效不能把仍存活的连接改写为 failed。
stdio 可以在脱敏诊断证据中记录 PID/启动身份；不要求 HTTP/SSE 伪造 PID，也不以 PID 存在单独证明握手成功。

### I4：活动任务与并发

- 保留当前流式命令策略：活动根 Turn 中裸 `/mcp` 及 stop/restart 不执行连接变更，status 允许读取本地快照；拒绝是本地提示，不发送给模型。
- start/force 仅添加未连接服务，可沿现有启动屏障运行；当前冻结工具目录不增加新工具，后续工具使用范围读取新目录。
- 空闲输入框不代表所有消费者空闲。子代理、Hook 和 Subscription 持有的工具使用范围也必须进入相同门禁。
- 对持有目标工具快照的活动消费者，单服务 stop/restart 返回 busy，不强行断线，不偷偷取消任务，不后台排队到未知时间再执行。
- 全量 stop/restart 在拆除任何连接前检查全部目标；有占用时整体返回 busy，避免部分停止后才发现不能继续。
- 门禁的检查与阻止新使用必须原子衔接，不能在检查通过后又允许消费者取得即将关闭的连接。
- 启动批次中的新连接只有在 required 裁决及批次收束完成后才能向后续消费者发布，防止消费者刚取得连接又被启动失败回收；status 可先展示该批次的进行态。
- 同一服务的并发 start 合并或串行复用；重复 stop 幂等；restart 等待旧连接完成清理后才建立新连接。
- 并发结果按实例与操作代次接受；生命周期清理不能依赖菜单继续打开。首期不承诺不同管理操作同时运行，工具调用隔离不等于必须并行启动管理动作。

### I5：取消、失败和配置变化

- 启动取消：回收本次未发布的连接和子进程，其他已存在连接不变，输出 interrupted。
- 停止一旦进入资源释放，用户中断也必须等待清理收束；实际已经停止时输出 stopped，不能因为晚到的取消又输出 interrupted。
- 重启取消：新连接未成功发布时保持目标停止/失败的真实结果，不复活旧连接；结果说明重启被中断。
- 连接关闭超时：沿现有 owner task 内的 SDK 清理和平台 adapter 收束；已确认释放后才能标 stopped。无法确认时保留 owner 与失败事实，禁止立即创建重叠新连接。
- 应用退出、工作区最终清理先按既有生命周期取消并收束消费者，再释放连接；最终 close 不使用交互操作的 busy 拒绝规则，不能把资源留给已退出的菜单。
- 传输失败不自动重放工具调用；结果未知的外部 Effect 沿既有对账路径处理。
- 菜单选中服务后配置被删除、重命名或工作区切换：执行时按冻结身份再次校验，给出目标失效提示；不能按旧序号选中另一个服务，不能默认全部。

## 6. 实现职责与删除范围

| 位置                                                                        | 改造职责                                                                   | 不应承担                                                |
|-----------------------------------------------------------------------------|----------------------------------------------------------------------------|---------------------------------------------------------|
| `agent/ports/mcp_runtime.py`                                                | 在现有生命周期端口表达明确目标、单服务状态和具名结果；精确声明可空性       | 第三方 SDK 对象泄漏、前端菜单状态、宽泛字典掩盖边界     |
| `agent/harness/mcp/owner.py` 及现有 execution/工具使用范围                  | 持有实例、协调使用门禁、管理最终释放和取消收束                             | 根据 UI 文案判断是否可停止、另建无主后台任务            |
| `infrastructure/mcp/external_runtime.py`                                    | 读取校验配置，维护唯一的逐服务运行事实和聚合投影，执行明确目标操作         | 用全局 started 跳过部分启动、同时维护互相冲突的 UI 状态 |
| `infrastructure/mcp/external_group.py`                                      | 复用每连接 owner task，按服务发布/撤下工具和路由，在进入资源栈的任务内关闭 | 为单服务 stop 设置全组 closing、清空其他服务索引        |
| `infrastructure/mcp/settings.py` 及既有 transport adapter                   | 保留配置键与连接身份、校验最新配置，隔离 SDK 和平台差异                    | 根据模糊名称猜目标、保留已删除的配置别名                |
| `frontends/tui/features/mcp.py`                                             | 构建两个菜单，产生具名选择，投影局部/全量结果                              | 持有连接、直接 kill 进程或修改配置                      |
| `frontends/tui/session/dispatch.py`、`barriers.py`、`prompting/commands.py` | 保留命令作用范围，统一导航、执行、取消及流式拒绝                           | 无效参数回退到全部或模型、绕开使用门禁                  |
| `frontends/terminal/mcp_status.py` 与现有菜单 renderer                      | 使用类型化投影渲染事实，复用布局和语义样式                                 | 重新判断连接生命周期或复制平台检测                      |

同次改造删除：菜单的全量五动作入口、force 隐式重启分支、start 的全组提前返回判断、仅用 enabled/disabled 代表连接状态的投影。
保留：直接命令的全量入口、配置字段名、工具名称契约、正式审批与 Effect 链路、应用最终 close 和工作区清理。
不用“为兼容旧 force”保留隐藏参数或第二条重启路径；文档与契约测试一并表达最终语义。

### 6.1 P0 具名验收输入

[control.schema.json](tests/fixtures/mcp/control.schema.json) 固定第 3 节的输入、状态及结果结构；
[契约测试](tests/external_mcp/test_control_contract.py) 校验以下边界。
这些定义是测试验收值，不是线上协议，也不为生产端口增加尚无消费者的测试专用类型。P1/P3 接入实际端口和菜单后，以实际输出验证相同期望。

| 具名定义 | 固定约束 |
|---|---|
| `McpAction` | `start/force/stop/restart/status` 五动作，不接受额外参数或未知动作作为已规范化请求 |
| `McpAllServices`、`McpSingleService`、`McpTarget` | `scope=all` 与 `scope=single + config_key` 判别联合；真实配置键 `all` 仍为单服务 |
| `McpControlRequest` | 必须携带运行实例、工作区、动作和明确目标；不以工具别名或显示序号寻找服务 |
| `McpMenuSelection` | 只允许 single 请求或 null 取消；null 不是管理请求，也不代表全量 |
| `McpServerMenu`、`McpServiceMenu` | 服务列表没有全量候选，二级五动作顺序固定、默认 status |
| `McpServiceSnapshot` | 配置启用、连接状态、工具目录和连接错误独立；允许 ready + 0 tools、disabled + ready、配置已删除 |
| `McpControlResult` | 每服务结果包含动作结论、快照及操作错误；busy 可以带 ready 快照，单目标只能返回一项 |

服务身份是运行实例及工作区中的**原始配置键**，工具前缀仅用于工具命名和路由，不是控制目标。
契约测试通过实际 `normalize_mcp_servers` 检查 `Docs API` / `Docs/API` 的前缀冲突和删除后的身份，禁止倒推目标。
JSON Schema 只校验结构和局部约束；实例有效性、目标存在性、结果键与目标一致、
`discovered = len(tools) + filtered` 等关系须在 P1 的实际边界验证，不能以 schema 通过代替运行事实。

[control_cases.json](tests/fixtures/mcp/control_cases.json) 保存 start 补齐、force 增量、重复 force、单停隔离、禁用目标 restart 和只读 status 的前置状态及预期连接变化。
P0 校验这些输入及目标集合；P1/P2 必须将它们接入实际 runtime，断言进程、握手及连接身份，不能仅重复验证 JSON 就勾选实现通过。

### 6.2 工具消费者与关停入口复核

| 消费者/入口 | 已核对的实际调用链 | P2 门禁与释放要求 |
|---|---|---|
| 根 Turn | [root_runner.py](agent/harness/execution/root_runner.py) → `execute_turn` → [ExecutionResources](agent/harness/execution/resources.py) 的 `with_mcp_session` → `CompositeToolRuntime.run_with_context` | 取得冻结工具目录时同时取得服务使用权，覆盖整个回调；成功、异常和取消均释放 |
| 子代理 | [subagent_runner.py](agent/harness/execution/subagent_runner.py) 使用 composition 注入的同一 execution 资源及 TurnRunner | 复用同一使用范围；父 Turn 返回后仍存活的子代理不能绕过占用检查 |
| Subscription | [forwarding.py](frontends/subscription/forwarding.py) 的 `DefaultForwardHandler` 经 `RootTurnCommandExecutor`、TurnApplication 使用注入的 TurnRunner | 纳入根 Turn 路径；前台输入空闲不代表后台使用权已释放 |
| Review 工具发现与恢复 | [reviews.py](agent/application/turns/reviews.py) 通过 `with_mcp_session` 发现工具；[observed_turn.py](agent/harness/execution/observed_turn.py) 恢复执行仍经 `execute_turn` | 短发现范围结束及时释放；实际执行重新取得使用权，不重写冻结请求中的工具身份 |
| MCP Hook | [Hook runtime](agent/harness/hooks/runtime.py) → [HookMcpRunner](infrastructure/mcp/hook_runner.py) → `external_tool_group.call_hook_tool` | 当前绕过 `with_mcp_session`；需注入同一 owner 的使用能力，解析和调用前取得，异常/超时/取消时释放 |
| 异步 Hook | [async_tasks.py](agent/harness/hooks/async_tasks.py) 持有并收束 Hook 任务 | 跟随实际 MCP Hook 使用范围；退出时等待已有清理，不仅统计前台工具调用 |
| 应用最终关闭 | [process_resources.py](agent/harness/process_resources.py) 顺序收束 Subscription、启动任务、子代理、审批、Hook 等，再关闭 execution/service | 保留现有分步关闭及失败重试语义；最终资源释放不返回交互 busy |
| 工作区切换 | [workspace_change.py](agent/harness/sessions/workspace_change.py) 移交旧 owner、释放并重建资源 | 使用权属于原运行实例；旧连接回调不能向新工作区发布状态或工具 |

门禁由现有 Harness owner 与 execution 使用范围协调，底层连接记录仍归 runtime/group。
“获得目录及使用权”和“检查占用并禁止新增使用”必须原子衔接；不能在 TUI 自建计数器，也不能只在 `call_tool` 期间加锁。
P0 只固定这些落点；引用记录、并发控制和实际 busy 行为在 P2 实现并验证。

## 7. 分阶段改造清单

每阶段完成实现后先跑受影响定向测试，缺少必要证据不得勾选完成。P0 的完成仅表示验收基础齐备，不代表后续产品行为已实现。

### P0：固定边界与可复现验收输入

- [x] 将第 3 节动作表映射为具名目标、状态和操作结果；取消选择与全量目标严格分离，见 6.1。
- [x] 明确运行时实例、服务配置键与稳定工具前缀之间的关系，覆盖重名规范化及配置删除。
- [x] 盘点根 Turn、子代理、Hook、Subscription 的工具使用范围和关停入口，确定门禁落点，见 6.2。
- [x] 准备第 9 节的独立验收配置及真实 MCP 测试服务；正常 fixture 不借用用户日常连接。
- [x] 为语义变更建立定向契约用例及生命周期期望输入，明确后续必须验证实际运行行为。

阶段出口：动作/目标可唯一解释；fixture 有可核验的启动身份、连接记录和工具调用结果；AC01、AC02、AC07 的用例齐备。
P0 的 schema/fixture 测试不代表 AC01、AC02、AC07 的产品链路已通过；新菜单、命令解析和逐服务状态投影仍由 P1/P3 接入后验收。

### P1：单服务连接与状态

- [ ] 扩展现有端口和 owner，将按服务操作传递到 runtime/group，保留最终全量 close。
- [ ] 按配置键管理独立连接记录，实现单服务 start/force/stop/restart、重复操作幂等和状态投影。
- [ ] 逐服务发布、撤下工具和路由；保留进入资源栈的 owner task 负责退出资源栈。
- [ ] 处理零工具、工具过滤、启动失败、连接掉线、关闭超时以及旧操作回调。
- [ ] 单服务失败不释放其他连接；停止最后一个服务后仍可重新启动。
- [ ] 定向测试通过，并用真实 stdio 子进程和 HTTP/SSE 连接验证资源行为。

阶段出口：AC03、AC04、AC07、AC08、AC10 的底层断言通过；取得 R04、R06、R07、R08 的真实传输部分证据。
此时不要求菜单已可用，也不能把这些 R 用例整体标为通过；完整菜单链路留待后续验收。

### P2：全量语义与工具生命周期门禁

- [ ] 全量 start/force 按服务补启动，保留已有连接；全量 restart 明确拆除并按 enabled 重建。
- [ ] 实现 required 失败的批次收束，区分操作前连接与本批新建连接。
- [ ] 将全部工具消费者纳入使用门禁；原子处理占用检查、新使用禁止与资源关闭。
- [ ] 活动消费者持有的工具目录不被改写，新目录只影响后续使用范围。
- [ ] 覆盖取消、客户端退出、工作区切换与迟到结果；未知工具结果不自动重试。
- [ ] 检查 Hook、子代理和审批服务身份在重启前后不串用，定向集成测试通过。

阶段出口：AC05、AC06、AC09、AC11、AC12；R05、R09、R11、R12 所需观测点齐备，不能仅检查前台 UI 的 busy 标志。

### P3：菜单、命令与反馈

- [ ] 裸 `/mcp` 只列服务，所有服务进入固定五动作菜单；没有 All servers 或全量动作行。
- [ ] 实现 M1—M5，二级默认 status、父子菜单返回保留选择、确认后关闭菜单组。
- [ ] 直接 `/mcp <action>` 只产生明确全量操作；未知动作和多余参数本地报错。
- [ ] 复用活动区、标准页脚、Runtime Keymap 和共享 renderer；范围与服务名出现在结果中。
- [ ] status 输出连接与配置的独立事实；配置错误、空列表、临时启用和配置删除均有正确显示。
- [ ] 同步命令补全说明和交互文档中受影响的稳定行为，移除旧 force 和全量菜单描述。
- [ ] 菜单、命令、活动交接及窄窗口定向测试通过。

阶段出口：AC01、AC02、AC13—AC16；M1—M5、I1—I5 均有自动验证或明确的真机用例映射。

### P4：完整调用链和原生终端自动验收

- [ ] 扩展现有 `tests/pty` 场景和 TUI acceptance 用例，在 Windows ConPTY 与 POSIX PTY 上驱动真实按键。
- [ ] 用实际 MCP SDK 和独立子进程/本机 HTTP/SSE fixture 验证从菜单选择到工具调用的完整链路。
- [ ] 覆盖连续 Enter、Esc 返回、长列表、缩放、无色、动画关闭、失败及取消的帧与事实一致性。
- [ ] 补齐全部 AC 项的定向或集成证据；运行受到此次端口/包边界变更影响的完整架构审计。

阶段出口：AC01—AC16 自动检查通过；测试报告区分“使用替身”“真实传输”和“原生终端”，不能统称真机完成。

### P5：真实客户端验收与发布收口

- [ ] 在实际终端中运行当前客户端和发布候选入口，按 R01—R14 逐项人工验收并保存证据。
- [ ] 在用户实际使用的外部 stdio 服务和远端 MCP 上完成真实连接/调用验证；fixture 通过不替代本项。
- [ ] Windows、Linux、macOS 平台验收记录完整；不可用平台写待验收，不标通过。
- [ ] 第 10 节命令、编译检查、差异检查完成；稳定文档、帮助和测试中的动作语义一致。
- [ ] 核对无残留 MCP 子进程/连接、无未完成清理任务、无旧状态回写；确认用户原有改动未被覆盖。
- [ ] 第 11 节证据表填入实际版本、平台、结果及产物位置，所有发布门槛关闭。

阶段出口：全部 AC、R 用例有证据；未执行或仅执行了模拟场景的条目不能作为发布通过。

## 8. 验收标准与追踪矩阵

| ID   | 必须满足的标准                 | 自动证据/重点断言                                                                                             | 真机映射      |
|------|--------------------------------|---------------------------------------------------------------------------------------------------------------|---------------|
| AC01 | 菜单只选单服务，直接命令为全量 | 菜单候选无全量值；取消、空配置不触发任何操作；全量命令不读取上次菜单目标                                      | R01、R02      |
| AC02 | 五动作固定且无静默扩大范围     | 顺序固定、默认 status；未知动作/额外参数/失效键不执行、不发送模型请求                                         | R01、R03、R10 |
| AC03 | 单服务真正隔离                 | 停止/重启 A 后，B 的连接身份、工具名、路由和正常调用保持；不只断言 close 调用次数                             | R04           |
| AC04 | start/force 按服务幂等         | 重复动作无新进程/握手；force 不重启 ready 服务；禁用服务可临时启动，配置字节不变                              | R05           |
| AC05 | 全量启动补齐缺失服务           | 菜单 stop A 后全量 start 恢复 A，B 身份不变；全量 force 再补禁用服务                                          | R02、R05      |
| AC06 | required 失败只收束规定批次    | 增量失败保留操作前连接；冷启动/全量重建失败清理本批连接；单目标失败不影响其他目标                             | R09           |
| AC07 | 工具数与配置不冒充连接状态     | ready+0 tools、disabled+ready、failed、removed 状态可区分；工具发现失败不是有效零工具；状态读取不触发网络请求 | R06、R08、R10 |
| AC08 | 关闭、失败和取消真实收束       | 同任务退出资源栈；完成清理后无残留进程、任务或路由；重启不产生重叠旧连接                                      | R07、R09、R14 |
| AC09 | 冻结工具使用范围不被破坏       | 有引用时 stop/restart 被门禁拒绝；检查与禁止新引用无竞争；新工具只进入后续快照                                | R11           |
| AC10 | HTTP/SSE 只释放连接            | 客户端关闭后远端实例仍能接受独立连接；不声称远端进程已停止                                                    | R07           |
| AC11 | 调用不被自动重放               | 断线、重启或未知结果时服务端调用计数不重复；Effect 身份及审批隔离沿既有契约                                   | R11           |
| AC12 | 工作区及配置身份不串用         | 菜单陈旧选择、规范化名称冲突、切换工作区和迟到回调均不能作用于错误服务                                        | R10、R12      |
| AC13 | 菜单符合统一设计               | 标题/说明/编号/标准页脚/窄屏布局来自共享组件；返回恢复选择，确认退出菜单组                                    | R01、R13      |
| AC14 | 活动状态与结果正确交接         | 单服务结果有服务名、全量结果有范围；一次操作只有一次最终块，失败和取消无残留动画                              | R03、R09、R13 |
| AC15 | 展示不泄露外部凭据             | 认证头、URL 凭据、环境秘密不进入菜单、错误正文和共享验收产物                                                  | R09、R10      |
| AC16 | 生命周期与跨平台约束不退化     | 定向测试、原生终端验收、架构审计通过；不引入前端持有连接或平台命令穿透                                        | R07、R12—R14  |

## 9. 真机验收方案

### 9.1 环境与测试服务

在独立测试工作区和专用配置中验收，不修改日常 MCP 服务配置或复用生产写操作。
配置通过项目既有配置/注册入口准备，记录实际生效路径与脱敏配置摘要；不额外发明配置字段。
实际入口以当前仓库 `python mind.py --help` 和发布候选的 `mind --help` 为准，不假设尚未实现的运行参数。

| 代号 | 验收服务                                                   | 可核验事实                                                                      |
|------|------------------------------------------------------------|---------------------------------------------------------------------------------|
| A    | enabled 的真实 stdio fixture，提供只读 ping 和可控阻塞调用 | 启动实例标识、PID/创建时间、握手数、调用数                                      |
| B    | 第二个 enabled stdio fixture，与 A 使用相同的原始工具名    | 验证服务前缀和路由隔离，A 重启时 B 实例标识不变                                 |
| D    | disabled 的 stdio fixture                                  | 普通 start 不启动；force 临时启动；配置保持不变                                 |
| H    | 独立运行的 Streamable HTTP MCP fixture                     | 服务实例标识、MCP 初始化/会话记录、调用记录；客户端关闭后独立客户仍可连接       |
| S    | 独立运行的 SSE MCP fixture                                 | 连接建立/关闭记录与远端进程持续存活；不将 HTTP keep-alive 数量当作 MCP 会话数量 |
| E    | 合法零工具服务及工具全被过滤的配置                         | 握手成功与 ready、工具 0/filtered 统计分别核对                                  |
| F    | 可控制启动失败、握手超时、进程退出和响应断线的 fixture     | 每种失败的注入点、调用计数及关闭结果                                            |

另准备长服务名、超过一屏的服务列表、规范化后名称冲突、配置删除及第二工作区。
fixture 是通过正式 MCP SDK 与真实进程/套接字运行的受控服务；它可以用于确定性失败注入，但不能替代 P5 对实际外部服务的验收。
真实外部服务只选择无副作用工具；不可把自动或真实写操作用作连通性检查。

最低平台覆盖：Windows 实际终端及 ConPTY、Linux 实际终端及 PTY、macOS 实际终端及 PTY。
记录终端产品与版本；宽度覆盖 120、80、40 列，高度覆盖正常高度与低高度，至少一次连续缩放。
默认主题、无色及关闭动画分别检查；平台缺失就是待验收，不得用另一个平台推断通过。

### 9.2 P0 fixture 运行与证据

实现位于 [fixture_server.py](tests/external_mcp/fixture_server.py)、[fixtures.py](tests/external_mcp/fixtures.py) 和
[fixture_suite.py](tests/external_mcp/fixture_suite.py)，通过正式 MCP SDK 提供 stdio、Streamable HTTP、SSE 服务。
先激活仓库虚拟环境，从仓库根目录执行；每次选择**尚不存在**的产物目录以保留旧证据：

```shell
python -m tests.external_mcp.fixture_suite --directory .cache/acceptance/external-mcp/manual-01
```

该入口启动 H/S 两个独立进程，由操作系统分配本机端口，生成 `mcp-fixture.toml` 并等待 Ctrl+C 回收。
stdio fixture 由被验收的 MCP 客户端按配置启动，不由 suite 提前启动。

要在同一实际终端启动客户端，并临时替换整张 MCP 配置表：

```shell
python -m tests.external_mcp.fixture_suite --directory .cache/acceptance/external-mcp/manual-02 --client
```

`--client` 通过既有 `-c mcp_servers=<TOML inline table>` 参数启动 `mind.py`，不经过 Shell 拼接、不修改进程环境或用户配置。
整张 MCP 表被替换，日常服务不会混入；用户模型等其他配置保持，客户端工作区为本次独立产物目录。
使用 `/quit` 正常退出客户端，suite 随后回收自身创建的 H/S 进程。该入口不保证尚未实施的新菜单已可用。

| 配置键 | 模式 | 默认启用 | 用途 |
|---|---|---|---|
| A、B | ready | 是 | 同名原始工具 `ping/block`，验证前缀、进程及连接隔离 |
| D | ready | 否 | enabled/force 语义输入 |
| E | empty | 是 | 握手成功且工具列表为空 |
| Filtered | ready，`allow=[]` | 是 | 发现两工具但全部过滤 |
| NoTools | no-tools | 是 | 服务明确不声明 tools 能力 |
| F | startup-failure | 否 | 进程以 7 退出，不握手 |
| DiscoveryFailure | discovery-failure | 否 | 已握手但 tools/list 返回 MCP 错误 |
| Slow | handshake-timeout | 否 | 不回答握手，由客户端超时清理 |
| Disconnect | disconnect | 否 | 记录一次实际工具请求后以 23 退出，用于检测重放 |
| CloseStall | close-stall | 否 | 会话退出清理阻塞，由实际 stdio transport 回收进程 |
| Docs API、Docs/API | ready | 否 | 规范化名称冲突，服务身份仍为原始配置键 |
| all | ready | 否 | 真实配置键 `all` 不能成为全量哨兵 |
| H、S | ready | 是 | HTTP/SSE 服务；客户端断开后远端进程继续接受连接 |

全量 `force` 会包含故障服务；批量正常路径验收先在**专用配置副本**中去掉故障条目，required 场景在副本中明确设置 `required=true`。
普通生成配置不会启用故障条目，不将其合入日常配置。

只读 `ping` 返回 `instance_id/pid/session_id/call_count/value`；`block` 使用相同返回值，但等待对应 `<fixture-name>.release` 文件出现。
可以用 `Path.touch()` 释放 block，重复阻塞场景使用新的产物目录。
每个服务有独立 JSONL 事实文件；远端 fixture 另有 stderr 文件。JSONL 包含进程启动 UUID、PID、创建时间、递增序号及事件；
每次 MCP 会话另有 session_id，只有收到实际 initialized 通知才记录 `initialized`。
工具参数及传输凭据不写入 JSONL；工具返回值只包含测试字符串及验收身份。
强制终止的故障进程可能没有 `session.closed/process.closed`，须结合客户端 transport 回收和进程证据判断，不补写虚假的关闭事件。

[真实传输测试](tests/external_mcp/test_fixture_services.py) 验证握手、实际调用、实例重建、会话隔离、可控阻塞、
零工具/无 tools 能力/发现失败的区别、启动失败/超时/断线/关闭卡住，以及 HTTP/SSE 客户端断开后远端仍可再次连接。
配置还经生产配置解析器和现有 group 启动验证。这些测试尚不构成新菜单人工验收、原生终端按键验收或实际外部服务兼容性验收。

### 9.3 操作步骤、预期及证据

| ID  | 操作步骤                                                                                            | 必须观察到的结果                                                                                           | 必须保存的证据                                        |
|-----|-----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|-------------------------------------------------------|
| R01 | 输入 `/mcp`，逐个打开服务；二级 Esc 返回；重新进入后连续 Enter                                      | 一级只有服务；二级完整五项且默认 status；连续确认最多输出状态；返回恢复服务及滚动位置                      | 菜单/返回录像或连续帧、按键记录、管理操作计数         |
| R02 | A/B 运行，菜单 stop A；再输入 `/mcp start`；最后 `/mcp stop`                                        | 单停 A 后 B 保持；全量 start 恢复 A 且 B 不重启；全量 stop 关闭所有持有连接                                | 前后状态、A/B 实例身份、握手/关闭记录、工具调用结果   |
| R03 | 菜单分别执行 start/stop/restart/status；输入未知动作和带额外参数的 `/mcp stop extra`                | 菜单确认后回到输入框；结果有目标；无效输入本地报错且没有执行或模型请求                                     | 终端录制、操作事实、请求计数                          |
| R04 | A/B 都提供 ping，交替单独 stop/restart A，并调用 B；反向重复                                        | 未操作服务的实例身份和工具前缀不变，调用持续正确；重启的工具结果来自新实例                                 | 实例/创建时间、握手数、路由身份和实际返回值           |
| R05 | D 禁用时普通 start；force D；重复 force；全量 force；全量 restart                                   | 普通 start 明确未启动 D；force 不改配置、不重启已连接服务；restart 按 enabled 收束 D                       | 配置前后哈希、实例标识、启动次数、界面 temporary 标记 |
| R06 | 启动 E，分别观察零工具和全过滤配置；另注入工具发现失败；执行 status                                 | 有效空目录可显示 ready，发现失败显示 failed；0 tools 与 filtered 数正确；status 本身不产生初始化或探测请求 | 初始化/发现结果、状态截图、请求数前后对比             |
| R07 | 分别停止 stdio A、HTTP H、SSE S；从独立客户重新连接 H/S                                             | A 所有本次创建的进程资源收束；H/S 远端仍运行且可连接，本客户端连接关闭                                     | 进程/创建身份、服务端连接记录、独立客户端返回值       |
| R08 | F 成功连接后使进程退出或传输失败；读取状态和工具列表                                                | 已观察到失败后撤下失效工具、状态为 failed；其他服务可调用；不自动重连                                      | 失败注入时间、任务/连接事实、目录和调用记录           |
| R09 | 注入启动失败、握手超时、关闭卡住；分别取消启动、停止、重启；验证 required 冷启动/增量/重建失败      | 单服务失败隔离；批次回收符合第 3 节；取消不漏进程、不重放调用、不出现假 stopped/ready                      | 逐步录屏、脱敏错误、进程/连接记录、调用数及回收结果   |
| R10 | 菜单打开后删除/重命名目标或使配置无效；使用长名称/冲突名称；错误中放入测试用凭据标记                | 原目标失效时不操作其他服务/全部；已知连接仍可停止；凭据标记不出现在展示和共享证据                          | 配置变更摘要、目标身份、错误输出、脱敏检查            |
| R11 | 真实根 Turn 和后台消费者持有 A 的工具快照；尝试 stop/restart；同时 start 新服务 D；解除占用后再操作 | 被占用时明确 busy、连接不变；D 不进入当前冻结快照；后续使用范围可见 D；断线工具调用不重放                  | 实际服务调用/阻塞记录、使用范围与操作事实、终端反馈   |
| R12 | 两工作区配置含同名不同服务；菜单未确认或连接启动中切换/恢复工作区；退出客户端                       | 旧目标/迟到回调不发布到新工作区；旧资源被清理；新目录和调用来自正确服务                                    | 工作区身份、连接实例、工具返回值、退出资源检查        |
| R13 | 多服务列表在 120→80→40 列及低高度下缩放、翻页；改键位；无色和关闭动画重复主要流程                   | 说明换行正确，无覆盖/闪回/焦点丢失；页脚随键位变化；执行结果只留一份                                       | 终端版本、尺寸与主题、关键帧或录屏、按键配置摘要      |
| R14 | 连续做至少 10 次 A 的 stop/start/restart，并在客户端退出和重复 close 后检查资源                     | 无增长的遗留子进程、连接、工具路由或 owner task；其他服务不被误清理                                        | 每轮实例/资源统计、退出记录、最终清理证据             |

R11 中根 Turn 使用真实应用入口与实际模型服务完成至少一次调用；子代理、Hook、Subscription 按产品当前可用入口分别记录。
仅通过 mock 构造 busy 或直接调用 Python 方法，不能勾选对应的端到端真机项。
R12 的工作区切换以产品实际允许的时机执行；不支持并行切换的入口应验证明确拒绝和无副作用，不人为绕过门禁制造“成功”。

### 9.4 证据分层

| 层次                                 | 能证明什么                                      | 不能代替什么                           |
|--------------------------------------|-------------------------------------------------|----------------------------------------|
| 定向单元/契约测试                    | 目标范围、状态规则、菜单请求、错误和并发分支    | 实际子进程退出、真实网络和终端表现     |
| 真实 MCP fixture + 原生 PTY/ConPTY   | 进程、套接字、SDK、菜单按键、帧及工具结果的集成 | 用户实际终端环境、真实外部服务兼容性   |
| 实际客户端 + 实际终端 + 实际外部服务 | 可见交互、部署环境及真实连接/工具生命周期       | 确定性覆盖全部故障分支，因此仍需前两层 |

每条通过至少关联运行版本、环境、步骤、结果及证据路径。截图只能证明画面；连接隔离还必须有服务实例、握手或调用结果证明。
不得仅以服务名、PID 相同或菜单显示 ready 证明未重启；应结合实例标识/创建时间和握手记录，避免 PID 复用及显示缓存误判。
证据保存在任务专用产物目录，例如 `.cache/acceptance/external-mcp/<run-id>/`；共享前脱敏，不将访问令牌或完整私密工具参数写入长期文档。

## 10. 自动验证执行顺序

按阶段运行对应命令；已执行范围以第 11 节记录为准，不将后续命令列出视为已通过。先激活仓库虚拟环境：

```powershell
. .\venv\Scripts\Activate.ps1
```

Linux/macOS 使用对应仓库虚拟环境的激活脚本，随后使用相同的 `python -m ...` 命令。

P0 契约输入及真实 fixture 定向测试：

```shell
python -m pytest tests/external_mcp -q
python -m compileall tests/external_mcp
```

优先运行受影响的既有模块，并将新增用例加入相应测试文件；新建测试文件时同步这里的实际路径：

```shell
python -m pytest tests/infrastructure/mcp/test_mcp_group.py tests/infrastructure/mcp/test_mcp_infrastructure.py tests/frontends/tui/features/test_tui_mcp.py tests/frontends/tui/runtime/test_tui_startup.py -q
python -m pytest tests/frontends/tui/runtime/test_tui_stream_commands.py tests/agent/harness/test_workspace_coding_lifecycle.py tests/infrastructure/mcp/test_hook_mcp.py tests/integration/test_mcp_approval_gate.py tests/integration/test_mcp_approval_semantics_matrix.py -q
```

菜单与实际终端链路形成后运行对应原生终端场景；已有场景通过不等于新增 MCP 场景已覆盖：

```shell
python -m pytest tests/frontends/tui/acceptance/test_pty_tui_interaction.py tests/frontends/tui/acceptance/test_pty_tui_rendering.py tests/frontends/tui/acceptance/test_pty_tui_colors.py -q
```

本次涉及公开端口和生命周期边界，P4/P5 运行架构审计；普通局部迭代不机械重复全套：

```shell
python -m pytest tests/test_package_architecture.py tests/architecture -q
python -m compileall agent protocol frontends infrastructure observability metadata
git diff --check
```

测试需要真实子进程或网络但当前环境不允许时记录限制并等待可用验收环境；不把跳过、权限错误或测试替身通过记录成真机通过。

## 11. 验收记录与完成条件

按实际测试层次记录结果。P0 完成仅覆盖边界复核、验收输入与真实 fixture，所有 R 项仍待执行。

| 批次/阶段 | 代码版本 | 操作系统/终端/尺寸 | 用例 ID | 测试层次 | 结果   | 证据路径与缺陷 |
|-----------|----------|--------------------|---------|----------|--------|----------------|
| P0 | 本条引入的 P0 提交；基线 `02ae0564` | Windows build 26100 / PowerShell；非 TUI | AC01、AC02、AC07 的验收输入 | 具名契约 + 真实 MCP 子进程/网络 | 52 passed | `tests/external_mcp`；本机报告 `.cache/acceptance/external-mcp/p0-20260912/fixture-tests.xml` |
| P0 回归 | 同上 | 同上 | 既有 MCP、菜单及启动流程 | 既有定向测试 | 54 passed | 第 10 节第一组既有模块命令；不表示新动作语义已接入 |
| P1—P5 / R01—R14 | — | Windows/Linux/macOS 实际终端待验收 | 全部产品及真机项 | 待执行 | 未执行 | 不以 P0 fixture 结果替代 |

P0 验证环境：Python 3.11.8、MCP SDK 1.24.0、pytest 9.1.1、jsonschema 4.26.0、uvicorn 0.38.0、Starlette 0.50.0。
新增测试最后一次运行 52 项通过，0 失败/错误/跳过；既有四个模块 54 项通过。
`python -m compileall tests/external_mcp`、fixture suite 的 `--help`、本清单本地链接检查及 `git diff --check` 通过；检查未发现遗留 fixture Python 进程。
P0 未改生产包边界，因此未运行 P4/P5 的完整架构审计、PTY/ConPTY 或人工真机验收；启动动画继续沿用第 5 节既有方案。

- [ ] P0—P5 的阶段出口均满足，AC01—AC16 都有可复查证据。
- [ ] R01—R14 完成，平台和服务覆盖符合第 9 节；未执行项保持未完成。
- [ ] 操作范围在菜单、补全、直接命令、活动反馈和文档中一致。
- [ ] force 隐式重启、start 全局跳过、旧全量菜单和旧配置状态投影均已删除。
- [ ] 资源泄漏、错误作用范围、冻结工具快照被破坏、跨工作区串用、凭据泄漏均无未关闭缺陷。
- [ ] 稳定文档已描述最终行为，未将本文设计稿或示意数字当成运行事实。
