---
name: blueprint
description: `--code` 星图协议对象，定义多任务块、循环、回放和稳定复用写法。
---

# Mind `--code` 星图写法

## What

`--code` 不是独立模式，而是把多任务块、循环和回归材料装进稳定来源。

原则：

- 星图正文只写自然语言任务。
- 不写内部工具调用脚本。
- 一个 `--code` 命令可以加载一个或多个 source。
- source 可以是本地文件、`-` 标准输入、`inline:<内容>` 或 HTTP(S) URL。

## When To Use

- 一条命令已经塞不下全部步骤。
- 任务需要循环、批量回放、多轮重复。
- 你要把回归材料版本化保存。

## Core Rules

- 每个任务块最好有 `# name`。
- 每个 `# name` 任务块都必须以单独一行的块结束线 `---` 结束，不要省略，也不要写成别的分隔符。
- 任务块之间用块结束线 `---` 分隔；如果文件里只有一个任务块，这个任务块末尾也照样保留块结束线 `---`。
- 每条任务都要包含目标、动作、通过条件、输出。
- `--code` 必须附着在对应领域入口后面：接口/媒体用 `--fast --mcp --code`，Android 顺序任务用 `--chat --mcp --code`，coding/外接 MCP 用 `--xtra --code`。
- `--code` 参数支持多个 source，按传入顺序顺序执行。
- 本地文件不存在、URL 拉取失败、标准输入为空或 inline 内容为空时，按 [错误与回退](errors-and-fallbacks.md) 回报。
- 生成星图时，先定块头和块结束线，再回填正文：先写 `# name`，再预留最后一行块结束线 `---`，中间再填任务内容。

## Good Examples

最小结构：

````md
# name: smoke
对 https://www.example.com 做 GET 请求。
校验状态码为 200。
返回响应摘要。
---
````

命令示例：

```bash
mind --fast --mcp --code smoke.md
mind --fast --mcp --code smoke.md regression.md
mind --fast --mcp --code api_regression.md
mind --chat --mcp --code android_nightly.md
mind --xtra --code investigation.md
mind --fast --mcp --code -
mind --fast --mcp --code https://example.com/packs/smoke.md
```

`inline:` 也可作为 source 使用，但内容必须包含真实换行，例如 `inline:<完整星图文本>`；如果命令行环境不便传多行文本，优先使用文件或 stdin。

任务写法模板：

```text
对 <目标> 执行 <动作>，校验 <通过条件>，提取 <结果>，返回摘要。
```

单块模板：

````md
# name: <task_name>
对 <目标> 执行 <动作>。
校验 <通过条件>。
返回 <结果>。
---
````

空壳模板：

````md
# name: <task_name>
<在这里写目标>
<在这里写动作>
<在这里写通过条件>
<在这里写输出>
---
````

双块模板：

````md
# name: <task_name_1>
<在这里写第一块的目标>
<在这里写第一块的动作>
<在这里写第一块的通过条件>
<在这里写第一块的输出>
---

# name: <task_name_2>
<在这里写第二块的目标>
<在这里写第二块的动作>
<在这里写第二块的通过条件>
<在这里写第二块的输出>
---
````

推荐生成顺序：

```text
1. 先写 `# name: ...`
2. 立刻写一行块结束线 `---`
3. 再回到中间补目标、动作、通过条件、输出
```

推荐起手式：

```text
先复制这 2 行骨架：

# name: <task_name>
---

再回到中间补正文。
```

多块起手式：

```text
如果确定要写两块或更多块，先把所有块头和块结束线一次性铺好：

# name: <task_name_1>
---

# name: <task_name_2>
---

然后再分别回填每一块的正文。
```

## Bad Examples

```text
在星图里直接写内部工具调用脚本。
```

问题：

- 破坏对外稳定契约。
- 无法在不同客户端迁移。

```md
# name: smoke
对 https://www.example.com 做 GET 请求。
校验状态码为 200。
返回响应摘要。
```

问题：

- 缺少块结束线 `---`。
- 后续继续追加任务块时，边界会变得不稳定。
- 生成器容易把下一个任务块误并到当前块里。

````md
# name: step_one
对登录接口做 POST，请求体包含 username 和 password。
校验状态码为 200。
---

# name: step_two
对 profile 接口做 GET，请求头带上一步拿到的 token。
校验状态码为 200。
返回摘要。
````

问题：

- 第一块的块结束线是对的，但最后一块仍然缺少块结束线 `---`。
- 多块文件里最容易漏的是最后一个任务块的块结束线，不是只有第一块需要补块结束线。
- 后续继续追加第三块时，第二块和第三块的边界会变得不稳定。

## Checklist

- 是否每个任务块都有稳定名字。
- 是否每个 `# name` 任务块末尾都显式写了块结束线 `---`。
- 是否任务块之间都用块结束线 `---` 分隔。
- 是否只写自然语言任务。
- 是否需要 `repeat`、`loop`、`attempts` 等全局控制。
- 是否确认 `--code` source 形态正确：文件、stdin、inline 或 URL。
- 生成时是否采用“先写 `# name` 和块结束线 `---`，再补正文”的顺序。

终检口诀：

```text
终检只看两件事：
1. 每个 `# name` 后面有没有正文
2. 每个任务块末尾有没有块结束线 `---`
```

## Failure Handling

- URL 来源失败时，回退本地文件。
- 本地文件失败时，回退 `inline`。
- 读取失败或内容为空时，参考 [错误与回退](errors-and-fallbacks.md) 的固定回报结构。

## 复杂示例（1-7）

### 示例 1：同一接口连续测 100 次（每轮不同 `session_id` 与 `query`）

```bash
mind --fast --mcp --code api_loop_100.md
```

````md
```cfg
attempts: 2
stop_on_fail: false
global_rule: |
  每轮都记录 session_id、query、状态码和摘要结果。
```

# name: chat_api_loop_100
# loop: 100
对 https://api.example.com/chat 发起 HTTP POST 请求。
请求头包含 Content-Type=application/json 和 Authorization。
每轮使用不同的 session_id 和 query。
session_id 建议包含轮次编号，例如 sess_001 到 sess_100。
query 建议从预设问题集按轮次顺序取值，不重复。
请求体至少包含 session_id、query、language、timestamp。
校验状态码为 200。
断言 response.body_json.ok 为 true。
提取 response.body_json.reply、response.body_json.intent、response.body_json.trace_id。
---
````

### 示例 2：100 组固定样本回放（推荐用于可复现回归）

```bash
mind --fast --mcp --code api_dataset_100.md
```

````md
```cfg
attempts: 2
stop_on_fail: false
global_rule: |
  每轮必须输出 case_id、session_id、status_code、ok、intent、reply 摘要。
```

# name: dataset_replay_100
# loop: 100
按轮次读取样本字段：case_id、session_id、query、language、timestamp、expected_intent。
对 https://api.example.com/chat 发起 HTTP POST。
请求头：Content-Type=application/json，Authorization=Bearer <token>。
请求体包含 session_id、query、language、timestamp。
校验状态码为 200。
断言 response.body_json.ok 为 true。
提取 response.body_json.reply、response.body_json.intent、response.body_json.trace_id、response.time_ms。
将返回 intent 与 expected_intent 比较，记录是否命中。
---
````

### 示例 3：单接口 `# loop: 30` 自动生成用例（不拆多个 `# name`）

```bash
mind --fast --mcp --code api_loop_30_contract.md
```

````md
```cfg
attempts: 2
stop_on_fail: false
global_prefix: |
  接口测试规范：
  - 目标接口：POST https://api.example.com/chat
  - 请求头：Content-Type=application/json，Authorization=Bearer <token>
  - 请求体字段：session_id、query、language、timestamp
  - 每轮声明预期结果（expected_status、expected_error_code、expected_intent）
global_rule: |
  输出总体通过率、intent 命中率、稳定失败清单。
```

# name: chat_api_loop_30
# loop: 30
按轮次自动生成正例、反例、边界输入。
每 5 轮复用同一 session_id，检查上下文延续。
对 https://api.example.com/chat 发起 HTTP POST。
校验状态码并断言 response.body_json.ok 与预期一致。
提取 reply、intent、error_code、time_ms、trace_id。
---
````

### 示例 4：策略版（热身窗口 + 计分公式）

```bash
mind --fast --mcp --code chat_eval_weighted_30.md
```

````md
```cfg
attempts: 2
stop_on_fail: false
global_prefix: |
  第 1-10 轮为热身，不计分。
  第 11-30 轮为计分窗口。
global_rule: |
  计算加权总分并给出通过结论。
```

# name: chat_eval_weighted_loop_30
# loop: 30
对 https://api.example.com/chat 发起 HTTP POST。
每轮自动生成正例、反例或边界输入。
提取 status_code、error_code、intent、reply、time_ms、trace_id。
在第 11-30 轮计算加权总分并输出最终结论。
---
````

### 示例 5：并发策略版（`concurrency` + `fail_fast`）

```bash
mind --fast --mcp --code chat_concurrency_30.md
```

````md
```cfg
attempts: 2
stop_on_fail: false
global_prefix: |
  并发策略：
  - 热身阶段（前 10 轮）：concurrency=2
  - 计分阶段（后 20 轮）：concurrency=8
global_rule: |
  输出错误率、P95 和稳定性结论。
```

# name: chat_concurrency_loop_30
# loop: 30
对 https://api.example.com/chat 发起 HTTP POST。
根据轮次切换并发级别并执行请求，保留失败样本但不中断。
提取 status_code、error_code、time_ms、trace_id。
---
````

### 示例 6：可观测性策略版（trace/request 聚类）

```bash
mind --fast --mcp --code chat_observability_30.md
```

````md
```cfg
attempts: 2
stop_on_fail: false
global_prefix: |
  每轮输出 trace_id、request_id、session_id、status_code、error_code、latency。
global_rule: |
  失败样本按 error_type 聚类，输出错误分布和 TOP3 高频错误。
```

# name: chat_observability_loop_30
# loop: 30
对 https://api.example.com/chat 发起 HTTP POST。
提取 trace_id、request_id、error_code、time_ms。
将失败样本按错误类型聚类并输出统计摘要。
---
````

### 示例 7：漂移监控策略版（与基线对比）

```bash
mind --fast --mcp --code chat_drift_guard_30.md
```

````md
```cfg
attempts: 2
stop_on_fail: false
global_prefix: |
  读取历史基线：intent_accuracy、P95、错误码分布。
global_rule: |
  输出 drift 指标与告警项。
```

# name: chat_drift_loop_30
# loop: 30
对 https://api.example.com/chat 发起 HTTP POST，覆盖正例、反例、边界输入。
提取 intent、status_code、error_code、time_ms。
与历史基线做差异比对，输出 drift 指标、告警项和建议动作。
---
````

