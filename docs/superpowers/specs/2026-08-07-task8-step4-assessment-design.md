# Task 8 Step 4：官方证据与自评草案设计

## 目标

为 SpecPilot 的真实语料出站门禁准备可审计证据和评估草案，覆盖：

- 语料侧：3GPP TS 38.300 与 TS 38.321 的适用条款；
- 主链：DeepSeek 官方 API，普通个人账户；
- 评分器：ChatAnywhere API 提供的 `glm-5.2`；
- 当前 `default-v1` 出站上限；
- 作者本人最终填写的 `author_conclusion`。

本工作只准备证据和前三部分评估内容。工具和助手不得填写、推断或升级
`author_conclusion`，也不得以“审批”“许可”或“法务结论”描述这份记录。

## 已确定的主链路由

主链沿用项目方案中的模型选择，并使用 DeepSeek 官方 API：

| 字段 | 值 |
| --- | --- |
| `provider_id` | `deepseek` |
| `endpoint_purpose` | `online-main-deepseek-v4-flash-api` |
| `use` | `online_main` |
| API 模型 slug | `deepseek-v4-flash` |

DeepSeek 官方 API 文档说明 `deepseek-v4-flash` 是会指向最新版本的别名。因此
route smoke 和后续运行记录除保存请求 slug 外，还必须保存调用日期以及 API 可得的
模型版本或响应 fingerprint；别名底层版本变化时不得把新结果与旧评估静默混用。

## 已确定的评分器路由

评分器使用以下稳定标识：

| 字段 | 值 |
| --- | --- |
| `provider_id` | `chatanywhere` |
| `endpoint_purpose` | `offline-judge-glm-5-2-api` |
| `use` | `offline_judge` |
| API 模型 slug | `glm-5.2` |

`provider_id` 标识直接接收请求并管理账户、计费和路由的 ChatAnywhere，而不是
GLM 或智谱。当前 source manifest 不直接绑定 `model_id`，因此模型名进入
`endpoint_purpose` 只能收窄记录的人工语义，不能替代技术门禁。实际授权前必须
另行实现并测试不可绕过的 route-to-model allowlist，固定
`model_id=glm-5.2`；否则不得创建 successor。

## 当前技术边界

本设计的本轮交付终点是“证据索引 + 只含前三部分的未签署草案 + default-deny”，
不是路线 A：

- 当前 CLI 的 provider smoke 强制使用 `--fixture-only`，不能证明 DeepSeek 或
  ChatAnywhere 的真实 API 路由可用；
- 当前 manifest 没有 `model_id` 字段，把模型写入 `endpoint_purpose` 仍只是命名
  约定；
- 当前授权逻辑不会主动访问政策页面检测变化，也不会自动撤销尚未过期的 successor。

因此本轮不调用 `source-manifest authorize-successor`。真实 adapter、合成载荷的
provider smoke、route-to-model 强绑定和相应测试需要单独设计与实施；在这些条件、
作者结论和精确 source versions 全部就绪前，W0 状态保持 `extend`。

本次选择的 `glm-5.2` 取代项目方案中原先的 judge 模型名。后续实施需同步更新
项目方案和报告约束中的旧模型引用，同时保留“主链与评分器使用不同模型，并由
人工盲审校准评分器”的要求。

## 证据来源与含义

### 语料侧

读取并记录以下官方页面：

- [3GPP Specifications by Series](https://www.3gpp.org/specifications-technologies/specifications-by-series)
- [3GPP Terms of Use](https://www.3gpp.org/terms-of-use)
- [ETSI Intellectual Property Rights](https://www.etsi.org/resources/intellectual-property-rights/)
- [ETSI Terms of Use](https://www.etsi.org/terms/)

每份规范必须先确定同一 Release 线上的精确版本，安全处理官方 ZIP/DOCX，取得
归档与 DOCX 哈希，并创建保持 `cloud_egress_authorized=false` 的初始
source manifest。版本未冻结或初始 manifest 不存在时，不生成 successor。

### DeepSeek 主链路由侧

读取并记录：

- [DeepSeek API 快速开始](https://api-docs.deepseek.com/)
- [DeepSeek 隐私政策](https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html)
- [DeepSeek 用户协议](https://cdn.deepseek.com/policies/zh-CN/deepseek-terms-of-use.html)

公开隐私政策覆盖 API，并说明服务收集输入和输出，可能在安全处理和去标识化后用于
模型训练与服务优化，同时提供“数据用于优化体验”的退出设置。证据必须同时记录
用户普通个人账户在抓取时该设置的实际状态；账户截图和标识只保存在受限目录，
不得提交 Git。还需从官方政策记录存储、处理区域和合作方信息，未被页面明确说明
的部分只作证据范围说明，不自行补全。

### ChatAnywhere 路由侧

读取并记录：

- [模型与费用列表](https://docs.chatanywhere.tech/doc-2694962)
- [用户协议](https://docs.chatanywhere.tech/doc-8793258)
- [隐私政策](https://docs.chatanywhere.tech/doc-8793261)
- [支持的国家和地区](https://docs.chatanywhere.tech/doc-9081297)

这些页面共同证明 ChatAnywhere 提供 API 路由并列出 `glm-5.2`。其中模型列表把
该模型描述为第三方供应商提供；隐私政策说明平台在必要范围内处理调用记录，且
输入和返回内容可能由第三方处理。公开页面没有给出固定的内容保留期限、处理
区域或具名的完整第三方清单。

### 模型身份侧

[智谱官方 GLM-5.2 文档](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)
用于确认 `glm-5.2` 是存在的模型 slug 及其公开能力说明。该页面不证明
ChatAnywhere 的实际请求一定直接进入智谱官方 API，也不替代 ChatAnywhere
账户适用的数据政策。

## 快照和留存规则

每个网页响应记录：

- 实际访问的 HTTPS URL；
- UTC RFC3339 抓取时间；
- 所取响应字节的 SHA-256；
- 作者可复核的短篇原创摘要；
- 证据适用范围说明。

网页响应、真实 ZIP/DOCX、账户截图和 API 元数据只进入已被 Git 忽略的
`artifacts/restricted/` 或 `manifests/local/`。仓库只提交不含原文、凭据、
账户标识和长篇条款摘录的结构化说明。

原始响应仅在核对哈希所需的最短时间内保留；如果页面条款不支持继续留存，完成
哈希和人工阅读后删除响应字节，只保留 URL、时间、哈希和原创摘要。

### 单快照 schema 与补充证据索引

现有 assessment schema 只允许一个 `terms_snapshot` 和一个 `policy_snapshot`，
不能把多个网页作为额外字段塞入 JSON：

- `terms_snapshot` 使用 3GPP Terms of Use；3GPP 规格入口及 ETSI IPR/Terms
  进入受限的 hash-indexed 研究记录；
- DeepSeek route 的 `policy_snapshot` 使用 DeepSeek 隐私政策；API 文档、用户协议
  和账户设置证据进入同一研究记录；
- ChatAnywhere route 的 `policy_snapshot` 使用 ChatAnywhere 隐私政策；模型列表、
  用户协议、地区页面和 GLM 官方模型页面进入同一研究记录。

补充证据索引使用 canonical JSON：

```text
schema_version: compliance-evidence-index/v1
route: provider_id + endpoint_purpose + use
entries[]: kind + url + captured_at + sha256 + summary + scope
```

索引按其 canonical SHA-256 内容寻址，保存在受限目录。对应的
`source_terms.summary` 或 `provider_policy` 摘要必须包含字面量
`evidence_index_sha256=<64 lowercase hex>`。由于这些摘要进入 assessment 和
manifest 的 canonical hash，该引用会随 successor 一起固化；现有代码尚不会读取
并验证索引，因此它是可复核的证据绑定约定，不是已实现的运行时门禁。

## 评估文件结构

每个“文档 × provider route/use”生成一份独立评估草案。对于
TS 38.300、TS 38.321 和 ChatAnywhere judge，需要两份 judge 草案；如果完整
路线 A 同时使用 DeepSeek 主链，则总计四个独立 route-bound successor 候选。

评估草案包含：

1. `source_terms`：适用条款快照、原创摘要和范围说明；
2. `provider_policy`：对应 route 的保留、训练、区域、第三方处理摘要；
3. `outbound_limit`：对 `default-v1.json` 当前允许字段及 unique/transmitted/
   corpus 上限的精确事实陈述和该陈述的 SHA-256；
4. 不由工具填写的 `author_conclusion`。

现有 schema 要求 `uncertainty` 至少包含一条。ChatAnywhere route 采用用户确认的
最小范围说明：

> 本评估仅依据 ChatAnywhere API 公布文档，不对文档未披露的上游处理链作额外推断。

这句话不改变模型选择，也不猜测上游主体；它只限定证据边界。移除该字段会使
CLI 返回 `invalid_authorization_evidence`，因此不修改 schema 来绕过它。

DeepSeek route 使用同一原则，只描述评估依据的官方公开政策、抓取时的账户设置和
适用范围，不由工具补写政策未明确披露的事实。

## 数据流和门禁

1. 安全处理两份官方规范并创建初始 default-deny manifests。
2. 抓取官方页面，记录 URL、时间和哈希。
3. 生成只包含前三部分的评估草案；分别按三个子模型的字段约束核对，但不声称
   整个 `ComplianceAssessment` 已通过校验，因为 `author_conclusion` 是必填字段。
4. 作者本人复核证据并填写完整 `author_conclusion`。
5. `authorized=false` 时不创建 successor。`authorized=true` 也只表示作者完成了
   自评；必须等真实 provider smoke 和 route-to-model 强绑定另行实现并验证后，
   才可调用 CLI 创建 successor。
6. 未填写、过期、route 不匹配、真实 provider smoke 未通过或缺少模型强绑定时
   保持 default deny，W0 状态为 `extend`。

任何页面抓取和评估草案生成都不得调用模型 API。真实 provider smoke 只能使用
合成 fixture，不发送 3GPP 正文或摘录。

## 失败处理

- 页面不可达、响应不完整或哈希无法复核：记录 blocked，不生成授权候选。
- route smoke 发现 `glm-5.2` 被重定向、替换或返回不同实际 slug：停止该路由并
  重新评估；当前系统不会自动发现该变化。
- 人工复核发现政策页面或 `default-v1` 出站上限变化：生成新快照和新评估。
  当前系统不会自动使未到期 successor 失效，因此结论使用短有效期，并在创建
  successor 前重新抓取并比对哈希。
- 评估未包含作者结论：保持 default deny；不得由工具补全。
- `author_conclusion=false`：留存完成的自评记录，但不调用 successor 命令。

## 验收标准

- 两份规范都有安全处理结果和初始 default-deny manifest；
- 每个证据页面都有可复核的 URL、时间和 SHA-256；
- 提交内容中不存在网页全文、规范正文、API key 或账户级敏感元数据；
- ChatAnywhere judge 草案记录 `offline-judge-glm-5-2-api` 与
  `offline_judge`，但不虚构当前代码已经强绑定 `model_id`；
- 草案前三部分分别符合现有子模型字段约束，完整 assessment 刻意保持未签署和
  不可授权；
- 本轮不创建 successor，任何 3GPP 内容都无法到达 provider；
- 验收结果明确记录为 `extend`，并列出真实路由 smoke 与模型强绑定两个后续阻断项。
