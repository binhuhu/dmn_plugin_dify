# DMN 决策表 · Dify 插件

在 Dify 工作流里直接执行标准 DMN 决策表：**全部 7 种 Hit Policy**、S-FEEL 单元格、DMN 1.1–1.5 XML 或 JSON。引擎只用 Python 标准库，在插件进程内运行，不调用外部服务，不需要凭据。

*Evaluate standard DMN decision tables inside Dify: all seven hit policies, S-FEEL cells, DMN 1.1–1.5 XML or JSON. Pure standard-library engine; no service, no credentials.*

| 工具 | 作用 | 主要输出变量 |
|---|---|---|
| **执行决策表** `evaluate_decision_table` | 用一个输入对象执行一张表 | `status`、`value`、`outputs`、`matched_rules`、`decision`（完整结果） |
| **检查决策表** `check_decision_table` | 不需要输入，找出可证明的问题，给出 sha256 固定值 | `valid`、`sha256`、`report` |

## 安装

在 Dify「插件 → 安装插件 → 本地插件」上传 `binhuhu-dmn-<版本>.difypkg`（见 Releases，或 `python scripts/package.py` 自行构建）。插件未经 Marketplace 签名，工作区需允许安装本地插件。

## Hit Policy

| 策略 | 简写 | 多条规则命中时 | 无命中时 |
|---|---|---|---|
| UNIQUE | U | 报错 `HIT_POLICY_VIOLATION` | 默认输出，否则 null |
| ANY | A | 输出全部相同才返回，否则报错 | 同上 |
| PRIORITY | P | 按输出列 `values` 的顺序取优先级最高者；多个输出列从左到右比较，再相同按规则顺序 | 同上 |
| FIRST | F | 表中最靠前的命中规则 | 同上 |
| RULE ORDER | R | 全部命中，按规则顺序 | `[]` |
| OUTPUT ORDER | O | 全部命中，按输出优先级排序 | `[]` |
| COLLECT | C | 全部命中，按规则顺序 | `[]` |
| COLLECT + SUM / MIN / MAX / COUNT | C+ C< C> C# | 聚合为一个值 | COUNT 为 0，其余为 null |

**关于 C+ 与 C#：** DMN 规范原文按“不同的输出值”求和/计数（`collect_values: "distinct"`，默认）；Camunda 7 等引擎对全部命中求和/计数，可设 `collect_values: "all"`。这一点在规范社区仍有争议（OMG issue DMN16-28），所以这里显式可选，并写进结果的规范化定义和 sha256。数字用十进制计算，`0.1 + 0.2` 就是 `0.3`。

## 决策表格式（JSON `dmn-table/v1`）

```json
{
  "format": "dmn-table/v1",
  "id": "loan-risk",
  "hit_policy": "UNIQUE",
  "inputs": [
    {"id": "age",  "label": "年龄", "expression": "applicant.age",  "type": "number"},
    {"id": "tier", "label": "等级", "expression": "applicant.tier", "type": "string", "values": "\"gold\",\"silver\",\"basic\""}
  ],
  "outputs": [
    {"name": "risk", "type": "string", "values": ["HIGH", "MEDIUM", "LOW"], "default": "LOW"},
    {"name": "limit", "type": "number"}
  ],
  "rules": [
    {"id": "young",       "when": ["< 25",     "-"],                "then": ["HIGH", 1000]},
    {"id": "mid-basic",   "when": ["[25..60]", "\"basic\""],         "then": ["MEDIUM", 5000]},
    {"id": "mid-premium", "when": ["[25..60]", "not(\"basic\")"],    "then": ["LOW", 20000]}
  ]
}
```

- `inputs[].expression`：从输入对象取值的字段路径（`a.b.c`）。不支持 FEEL 运算，需要计算的值请在上游节点算好。
- `type`：`number` `string` `boolean` `date`（`YYYY-MM-DD`）`any`。DMN 的 `integer`、`double`、`feel:number` 等都映射为 `number`。
- `inputs[].values`：可选的允许值（S-FEEL，如 `[0..100]` 或 `"A","B"`）；条件里的字面量超出允许值会被拒绝，检查工具据此判断覆盖缺口。
- `outputs[].values`：可选的允许输出值，**按优先级从高到低**；PRIORITY / OUTPUT ORDER 依此排序。`default`：单命中策略无命中时的默认输出。
- `then`：JSON 值；`any` 类型的输出可以是对象或列表。
- 也可以直接传 DMN XML（Camunda Modeler、Trisotech、Kogito 等导出）；文件含多张表时用 `decision_id` 指定。完整示例见 [examples/](examples/)。

### 单元格（S-FEEL 一元测试）

| 写法 | 含义 |
|---|---|
| `-` 或空 | 任意值 |
| `5`、`"gold"`、`true`、`date("2026-01-31")` | 等于 |
| `< 5`、`<= 5`、`> 5`、`>= 5` | 比较（数字、字符串、日期） |
| `[1..10]`、`]1..10[`、`(1..10)`、`[1..10)` | 区间，`[`/`]` 闭、`(`/`)`/反向括号开 |
| `"a", "b", > 100` | 任一满足 |
| `not("a", "b")` | 都不满足 |
| `null` | 输入缺失或为 null |

不支持（会明确报 `UNSUPPORTED_EXPRESSION`，不会猜）：变量作端点、算术、`date()` 以外的函数、`?` 语法、`!=`（请写 `not(x)`）。

**缺失与类型不符：** 输入缺失按 null 处理；和 null 或不同类型的值比较，结果是“未知”，这条规则不命中。结果中的 `missing_inputs`、`type_mismatches` 和轨迹会写明原因。

## 在 Dify 工作流中使用

1. 添加「执行决策表」节点。`决策表` 直接粘贴 JSON 或 DMN XML（也可以插入保存决策表的文本变量）。`输入` 选一个对象变量（代码节点、LLM 结构化输出等），或切换为常量直接填 JSON。
2. 下游用 `status` 判断：`MATCHED`（有规则命中）、`DEFAULT`（用了默认输出）、`NO_MATCH`、`ERROR`。
3. 分支用 `value`：第一个输出列的值（文本），IF/ELSE 节点可以直接判断，比如 `value` 是 `APPROVE`。字符串原样给出，数字、列表等给紧凑 JSON，没有结果时为空。
4. 取全部输出用 `outputs`：每个输出列一个键。单命中策略是单个值；RULE ORDER / OUTPUT ORDER / COLLECT 是列表；聚合是单个值。DMN 原样的结果在 `decision.result`；`decision` 的状态、命中规则、错误码等字段在变量选择器里可以直接选。
5. 生产环境建议先用「检查决策表」拿到 `sha256`，填进执行节点的 `固定 sha256`：表一旦被改动，节点会拒绝执行（`PIN_MISMATCH`）。

## 检查工具能发现什么

每条结论都能从表本身证明（每个单元格是一组值，规则是这些集合的乘积）：

| 代码 | 级别 | 含义 |
|---|---|---|
| `OVERLAP` | 错误 | UNIQUE 表中两条规则能同时命中，并给出一个例子 |
| `CONFLICT` | 错误 | ANY 表中可同时命中的规则输出不同 |
| `SHADOWED` | 警告 | FIRST 表中某条规则被更早的规则完全覆盖，永远不会被选中 |
| `NEVER_MATCHES` | 警告 | 某个条件在该列取值范围内不可能成立 |
| `GAP` | 提示 | 单输入表中没有规则覆盖的取值区间 |
| `NO_FALLBACK` | 提示 | 单命中表既无兜底规则也无默认输出 |
| `DEFAULT_IGNORED` | 警告 | 多命中表声明了默认输出（不会生效） |

另外列出全部加载错误（类型不符、允许值之外、未知字段、重复 ID 等），每条带位置（如 `rules[3].when[1]`）。

## 结果信封（`decision` 变量）

```json
{
  "schema": "dmn-plugin/evaluation.v1",
  "status": "MATCHED",
  "decision": {"id": "loan-risk", "hit_policy": "UNIQUE", "sha256": "…", "source": "json"},
  "result": {"risk": "LOW", "limit": 20000},
  "outputs": {"risk": "LOW", "limit": 20000},
  "matched_rules": ["mid-premium"],
  "selected_rules": ["mid-premium"],
  "missing_inputs": [],
  "type_mismatches": [],
  "inputs_used": {"age": 40, "tier": "silver"},
  "trace": [{"rule": "young", "matched": false, "failed": [{"input": "age", "test": "< 25", "result": "false"}]}],
  "error": null
}
```

sha256 是规范化后的表按 RFC 8785（JCS）计算的摘要：同一张表无论 JSON 排版、hit policy 写全称还是简写、还是从 DMN XML 导入，摘要都相同。

## 限制

- 一次一张决策表；DRG 中被依赖的决策（requiredDecision）不会自动执行，其结果需作为输入传入。
- 表最大 1 MiB、1000 条规则、64 个输入列、32 个输出列；输入最大 256 KiB；单元格最长 2048 字符。超过 400 条规则时跳过两两重叠检查。
- 日期只支持 `date`，不支持带时间、时区或时长。

## 开发

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python dify-plugin==0.10.2 pytest ruff
.venv/bin/python -m pytest -q                    # 单元 + 一致性用例 + 真实 SDK 加载
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python scripts/package.py --verify     # 打包，并在全新环境里从解包后的文件运行两个工具
```

`tests/conformance/hit-policies.json` 是与语言无关的一致性用例，其他实现（例如浏览器端检查器）可以直接复用。

本插件独立维护，与 `dify_dmn_plugin`（`hu8627/dmn_json`）没有代码或依赖关系。

## 许可

Apache License 2.0，见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。打包的 `.difypkg` 也包含这两个文件。
