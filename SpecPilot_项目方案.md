# SpecPilot —— 面向 Agent 应用开发实习的项目方案

> 目标岗位：Agent 应用开发、AI 应用工程、大模型应用工程、RAG/智能体工程实习  
> 项目策略：方案 A 为 7 周（W0–W6）主工程，方案 C（Dify）仅作为发布后 backlog  
> 核心定位：可部署、可观测、可评测的长文档 Agent 服务  
> 版本：v5（2026-08-06）

---

## 一、优化结论

### 1.1 项目不再追求“关键词全覆盖”

原方案同时规划了 Agent Registry、Planner、三层记忆、MCP、多 Agent、版本差异、Dify 对照、React、灰度发布和完整消融。单人 6 周很容易形成“模块很多，但每个模块的证据都不够深”的结果。

v2 改为：

- **主工程 A**：用 Python、LangGraph、MCP、FastAPI 完成长文档 Agent 的核心链路，重点证明工具调用、异常处理、引用可靠性、评测、服务化和部署能力。
- **展示入口 C**：Dify 仅作为主工程发布后的扩展，用同一工具和 fixture 语料展示低代码交付能力，不占用 W0–W6 主线。
- **不进入算法岗范围**：不做 SFT、DPO、GRPO、RLHF、模型训练或推理框架优化。

v3 在此基础上做了三类收敛：语料从三份减到两份并移出 TS 38.331；评测规模下调约三分之一以匹配单人标注工时；新增 gold 标注隔离协议、L2-adv 对抗子集、离线演示模式和 W4 中期节点。合并了语义重叠的工具，移除了评测 HTTP 端点，并把消融第一组换成长上下文基线。

v4 关闭了实施前审核发现的关键缺口：明确 top-k 最小证据片段的云端处理边界；用 corpus manifest 统一版本契约；开发集负责选择、锁定测试集只在 W6 首次运行；修正不可回答题的 Recall、pooling 偏差和 L2-adv 单类样本问题；补全缓存键、冷缓存测量、独立 MCP 容器与可验证的 fixture demo。Dify 正式移出首发验收。

v5 把上述承诺收紧为可执行契约：所有 provider 调用统一经过本地出站闸门，按运行累计限制 Evidence 与片段长度；重构 L2-adv 的正负样本与 dev/test 隔离；拆分 source、corpus 和 evaluation run 三层 manifest；只在锁定测试集保留两组核心对照（512 内的 excerpt 窗口选择、Verifier gate-only），层级检索等其他消融降为开发集诊断；前端收缩为最小 trace 页。

## Current state — 2026-08-15

> **[当前状态校正｜替代旧快照]** 下文保留旧日期的计划与过程记录；当前受限 store
> 重算结果为 **L1 40/40**、**L2 20/20**、`awaiting_adjudication=0`、**deep
> review 12/12**，pooling 已完全 sealed。当前 quick gate 为 **1537 unit, 187
> CLI**；另有 2026-08-14、commit `b89339d` 的 fresh PostgreSQL + frozen Qdrant
> full-service 证据：**1998 passed, 0 skipped**。
>
> 后一项仅是 fixture-only 的工程与服务集成证据，不代表真实 provider 验收、质量、
> 校准、延迟、L2 开发集指标、locked evaluation 或发布结果。历史上的 pooling typo
> 限制已失效：现在会重新提示，**does not end the pass**。SSE/
> reconnect、四场景 demo/profile matrix、evaluation `run_spec` 与第一次 locked
> evaluation 仍是 W5/W6 工作。

> **实施校正附注（2026-08-08）**：以下正文保留 v5 的原始计划与决策过程，不作追溯性改写。新增附注只标记当前状态：`[已变更]` 表示原假设已被后续决策替代，`[已完成]` 表示已有代码、测试或正式记录可核验，`[进行中]` 表示已启动但尚未达到原验收规模，`[延期]` 表示仍保留目标但不属于当前已完成范围。

### 1.2 一句话定义

**SpecPilot：面向通信标准长文档的可验证 Agent 服务，支持条款问答与规范一致性核查原型；每项结论必须绑定版本明确的条款证据，证据不足时拒答，并提供可追踪、可评测、可部署的完整工程链路。L2 是技术验证原型，不代表法律、认证或商用合规结论。**

> **[已变更｜定位重写]** 换语料后“长文档”这个前提已经不成立。实测（可索引单元 = 条款 + 表格，BGE-M3 含特殊 token）：RFC 9110 = 92,064 tokens，RFC 9112 = 19,717 tokens，冻结语料合计 **111,781 tokens**，一个现代模型的上下文窗口装得下数遍；最长单元 357 tokens。上方定义中的“长文档”，以及 §2.1 与 §14 的“长文档 RAG”行、§13 的长上下文风险行、§15.2 第 8 问，一律按下述重写理解：
>
> **SpecPilot：面向公开技术规范的可验证引用服务，在不可绕过的披露上限下完成条款问答与规范一致性核查原型；每项结论必须绑定版本明确的条款证据与可审计的披露记录，证据不足时拒答。L2 是技术验证原型，不代表法律、认证或商用合规结论。**
>
> 这不是降级。本项目的工程重量从来不在“文档装不下”，而在 §3.2 与 §5.2 的出站闸门、原子账本与逐次披露计量 —— 这套论证不依赖文档长度，换语料后一行未损。§15.2 第 8 问的答案相应改为：**长上下文能给出答案，给不出 `disclosure_id`、`content_hash` 和一份可审计的逐次披露账本。实测整个语料确实装得下；不发它是一个可执行、可测试、不可绕过的工程约束，而不是能力上限。** 旧答案（“数据最小化承诺与整份规范传输冲突”）在语料装得下之后是循环论证，不再使用。

### 1.3 预期招聘证据

同类 Agent 项目大多能展示“系统能跑”，能展示“数字可信”的很少。本项目的差异化集中在后者。完成后应能用可复现材料回答：

1. **这些数字是怎么来的？** 检索 gold 如何标注才不构成循环论证？答案由谁打分，那个裁判本身验证过吗？（§8.2、§8.3）
2. **有没有第三方能验证？** 招聘方 clone 仓库后，无 API key、无需下载真实 RFC 语料，能否用一条命令看到脱敏执行轨迹与 Verifier 的逐项拦截；已有镜像的暖启动能否达到经实测后才对外声明的三分钟目标？（§9.6）
3. **Agent 如何自主选择工具**，而不是走硬编码流程？工具超时、坏参数、空检索、无限循环、预算超限分别如何降级？（§5、§7）
4. **长文档如何解析、检索并定位到具体条款**，且每项确定性结论都绑定版本与条款 ID，证据不足时拒答？（§4.1、§4.3）
5. **哪些设计是被自己的数据推翻或被时间盒淘汰的？** L2 的职责分离是否值得保留？为什么不直接用长上下文？为什么不做长期记忆？（§8.5、§4.5）
6. **系统如何交付给业务使用？** API、SSE、容器、CI、运行轨迹。（§9）

第 1、2 条是与同类项目真正拉开差距的地方，简历和面试都应优先展开。第 5 条要求提前准备好“某项设计没有带来收益”的诚实回答——这类回答比一串漂亮数字更能证明工程判断力。

---

## 二、招聘信号与项目取舍

### 2.1 Agent 应用开发岗位的高权重能力

| 能力 | 本项目的证据 |
|---|---|
| Python 与应用工程 | 类型化数据契约、异步 FastAPI、测试、CI |
| Agent 编排与任务规划 | LangGraph Orchestrator，受预算约束的执行计划 |
| Function Calling | 模型自主选择 MCP 工具，记录工具名、参数和结果 |
| RAG 与知识库 | 章节树、条款切分、混合检索、交叉引用扩展 |
| 记忆与状态管理 | 会话工作状态、版本化用户偏好、明确失效规则，以及不做长期事实记忆的理由 |
| MCP | LangGraph 经 Streamable HTTP 调用独立 `mcp` 容器；工具通过协议发现，schema/timeout/health 随配置版本化 |
| 数据治理与出站控制 | 统一 `EgressPolicyEnforcer`；PostgreSQL 原子预算账本，抗并发与进程重启；unique/transmitted 双账本与 per-route 披露记录 |
| 评测与调优 | 固定测试集、消融、失败分类、成本与延迟 |
| 服务化与部署 | FastAPI、SSE、Docker Compose、健康检查 |
| 可观测性 | 每步 Agent、工具、token、耗时、成本和错误轨迹 |
| 工作流平台 | 发布后可选的 Dify 展示版复用同一工具与 fixture；不计入首发验收 |
| 业务沟通与文档 | 架构决策记录、评测报告、演示视频和使用手册 |

### 2.2 与现有简历资产的关系

已有项目已经证明：

- PrintPilot：LangGraph 工作流、Pydantic 契约、安全闸门和系统评测；
- CropSciBench：RAG、领域问答、幻觉评估和专家终审；
- 学术知识图谱：证据链接、结构化抽取和跨模型复现；
- GitHub 组织网络分析：数据管线、回归测试和决策型报告。

SpecPilot 不应重复证明“会调用 LLM”或“会做一次 RAG”。它应集中补齐：

- 模型自主工具选择与完整 tool loop；
- MCP 工具服务；
- 长文档结构化检索；
- Agent 任务成功率与工具调用评测；
- API、容器、运行轨迹和业务展示；
- （发布后扩展）Dify 工作流配置与平台取舍。

### 2.3 招聘样本核验

本方案以原三份 Agent 实习 JD 为基线，并用公开岗位补核应用工程岗位的共同要求：

| 招聘样本 | 公开信号 | 对本方案的影响 |
|---|---|---|
| [华信咨询设计研究院 2026 校招](https://power.seu.edu.cn/_t1654/2026/0325/c33447a559404/page.psp) | 数据治理、模型部署服务、AI 场景落地、咨询报告与解决方案 | 增加版本治理、部署、架构决策和评测报告 |
| [百度大模型应用开发工程师实习](https://talent.baidu.com/jobs/detail/INTERN/d1ed3134-5bd8-4743-a937-acca2773b1e7) | 业务协作、快速实现、技术选型、RAG、Agent、LangGraph | 主线聚焦应用开发与迭代，不扩到模型训练 |
| [百度 AI 数据分析实习（大模型应用方向）](https://talent.baidu.com/jobs/detail/INTERN/91f3c84c-5f91-484b-8bca-c4edec2f1932) | 输出核验、Prompt/RAG、向量检索、Docker/Kubernetes 与工程部署 | 强化 Verifier、容器、可复现测试和数据溯源 |
| [百度 Agent 策略算法实习](https://talent.baidu.com/jobs/detail/INTERN/6f85641f-2a8e-4806-bbb5-4bbbf4705741) | Long-context RAG、多 Agent，同时要求训练和后训练基础 | 吸收长文档层级检索与协同，不因关键词要求而发送整份规范或覆盖 SFT/DPO/GRPO |

公开样本用于验证岗位趋势，不替代原三份 JD。项目取舍以“Agent 应用开发/工程落地”作为最终目标。

---

## 三、项目范围

### 3.1 首发任务

| 任务 | 输入 | 输出 | 首发地位 |
|---|---|---|---|
| L1 条款问答 | 自然语言问题、规范范围和 corpus manifest | 答案、条款编号、原文证据、版本 | 核心 |
| L2 规范一致性核查原型 | 设计描述或参数配置、规范范围和冻结语料 manifest | 原子主张、一致性判断、依据、证据不足项 | 核心 |
| L3 版本差异 | 同一规范的两个版本 | 变更点和相关条款 | 发布后扩展 |

L1 和 L2 构成 W0–W6 主线。L3 不进入首发验收，也不占用主工程工期。

### 3.2 语料边界

> **[已变更｜当前生效]** 原计划的 3GPP/OOXML 主线与双轨制语料边界已被路线 C 替代：当前主语料为 IETF RFC 9110 与 RFC 9112，冻结 publication version 为 `2022-06`，输入采用官方 RFCXML v3；3GPP/OOXML 降为未来领域扩展。下方原文保留为选型历史，其中关于 ZIP/DOCX 与 3GPP 授权的要求不再约束当前 RFC 主线。**双轨制则仍然生效**：RFC 原文与全部派生索引仍只留在本机受限目录（`0700`/`0600`、git-ignored），仓库只收 fixture 语料，§8.0 的两栏边界与 §9.6 的离线演示未因换语料而简化。§3.2 原文所说“切换后双轨制可以降为单轨”是路线 C 的预期收益，**尚未兑现，也尚未决定是否兑现**：TLP §3.c.i 允许完整且未经修改地复制与分发，而 `docs/compliance/rfc-source-terms.md` 记录的三条 uncertainty 全部针对“摘录发往第三方 API”与“管线内部处理是否构成修改”，无一针对“把原件提交进仓库”。是否据此把源文档原件（不含派生物）转为单轨提交，是一项独立决定，做出前按现状执行。
>
> **[已完成｜当前安全边界]** RFC 摄取已实现单次、有界、regular-file、`O_NOFOLLOW` fail-closed 快照；manifest hash 先于 XML 解释，UTF-8/XML 安全、RFCXML v3 grammar、文档编号与唯一直接 `front/date` 身份校验均消费同一快照，下游不按路径重开。拒绝码与恶意输入回归已覆盖；真实 RFC 9110/9112 smoke 均为零 dangling cross-reference。

- MVP 语料固定为 3GPP TS 38.300 与 TS 38.321，覆盖 5G NR 总体描述与 MAC 层。
- TS 38.331 移出首发。该规范含大量 ASN.1 定义，规范性信息也分散在字段描述表中，通用的标题/条款/模态抽取管线需要更多专门适配；在首发中加入它会显著增加解析与标注风险。
- 选择这两份的理由：38.300 与 38.321 之间存在可用于验证引用扩展的跨规范引用；38.321 中的 BSR、DRX、HARQ 定时器等具体参数也适合构造 L2 规范一致性案例。具体可用案例数量以 W1–W2 人工导航结果为准。
- 两份规范使用同一 Release 线，并各自冻结一个具体版本。
- 源文件格式固定为 3GPP 官方发布的 ZIP 包内 `.docx`，不使用 PDF。DOCX 可直接暴露段落样式、表格与 OOXML 节点，减少 PDF 版面恢复这一类额外变量；这只是首发选型假设，解析隔离率、orphan clause 数和抽样准确性仍须在 W1–W2 实测后判断是否可用。
- 下载脚本在隔离临时目录中处理外层 ZIP：规范化并校验每个成员路径必须留在目标目录内，拒绝符号链接、除预期 `.docx` 外的嵌套压缩包、加密成员、非预期类型以及超过配置的文件数/单文件/总解压大小；只接收预期的 `.docx`。OOXML 解析在禁网、只读进程中执行，拒绝宏、嵌入式可执行对象和主动内容，外部关系只记录哈希、绝不解析或联网获取；按 ADR 0001，含外部关系的原件一律拒绝并进入隔离区，不做就地剥离——只有经单独评审的衍生流程才可移除白名单内的模板关系并另行产出派生文件，原件保持不变。任何违规或 hash 不匹配都进入隔离区，不写入正式语料目录。
- MVP 对每份规范只使用一个冻结版本；版本号、下载地址、文件散列和下载时间先写入 source_manifest，再由 corpus_manifest 引用。
- **双轨制语料。** 两份真实规范只在本地实验：原始文档、完整索引和全量条款一律不提交。仓库另备一份合成或许可宽松的小型规范 fixture，供 CI 与离线演示使用（§8.0、§9.6）。
- 仓库提交的是：下载脚本、非原文 source/corpus manifest、解析脚本、fixture 语料及其预计算向量、以及 §8.1 界定的可提交评测集字段。
- **数据处理边界。** 原始文档、完整索引和全量条款不出本机；embedding、本地候选检索与完整条款校验均在本地执行。在线推理可向已授权 provider 发送用户 query/L2 设计描述、派生原子主张、必要的版本元数据、按 §5.1 限额的本地预筛标题/路径节点，以及受限的 top-k Evidence excerpt；离线评测阶段调用的云端评分器只额外接收完成采分点判断所需的最小 gold evidence excerpt。发送字段、provider、端点、账户级保留/训练政策、处理区域及子处理方和逐阶段处理链路必须在配置与评测报告中披露。
- **敏感输入预检。** query/L2 设计描述出站前先在本地检查显式 secret、凭据和可配置的业务标识符；命中时要求用户确认脱敏版本或转本地 LLM。服务端硬限制 L1 query 为 1,024 model tokens、L2 设计描述为 2,048 tokens，首发 API 不接收文档附件；更长输入必须拆分或走本地路线。该规则只是额外防线，不宣称能识别所有敏感信息；真实 profile 必须在输入界面明确提示哪些字段会被发送给哪个 provider。
- **强制出站闸门。** 主链、Verifier、评分器与所有重试必须经过同一个本地 `EgressPolicyEnforcer`。唯一披露单元按 `(corpus_manifest_id, content_hash, quote_hash, normalized_excerpt_span)` 计数，不能把同一长条款切成多个 span 后仍只占一个名额。在线主链硬上限为：L1 每次运行最多 5 个唯一 excerpt、唯一 excerpt 合计最多 2,560 model tokens；L2 每个原子主张最多 4 个/2,048 tokens、每次运行最多 12 个/6,144 tokens；每个 excerpt 同时不得超过 512 tokens 和 8 KiB UTF-8。离线 judge 只能接收最终答案、采分点和最多 5 个/2,560 tokens 的唯一 gold excerpts。若做云端 judge，在线账本与 judge 子账本共同归入同一个 evaluation-case 根账本；根级唯一上限为 L1 10 个/5,120 tokens、L2 17 个/8,704 tokens。每个阶段和根级还执行 `unique_count × 8 KiB` 的 byte cap。transmitted 账本按真实阶段 fan-out 另计：在线链允许 Evidence 选择、答案/Compliance 和 Verifier 三次正常传输，再为一次重试留余量，故上限为在线唯一 token/byte cap 的 4 倍；judge 本身含一次正常调用和一次重试，上限为 judge 唯一 cap 的 2 倍。evaluation-case 根 transmitted 上限因此为 L1 15,360 tokens/240 KiB、L2 29,696 tokens/464 KiB，而不是简单乘根级唯一数。最终答案中再次出现的引文、相同 payload 重试或改发另一 provider 都累计 transmitted 用量。账本同时记录全局唯一内容与每条 provider route 的披露；重检索、交叉引用、角色切换和重试都不重置额度。任一上限超出都 fail closed，改用本地 LLM、要求用户缩小任务、转人工评分或返回证据不足，不得悄然扩大出站范围。

> **[已收紧｜待落配置]** 上方各级上限换语料后仍然适用，但实现里还有一层本节从未记载的**语料级上限** `corpus_unique`，当前值为 1,024 excerpt / 524,288 tokens / 8 MiB。那是按 3GPP 语料量级设的：对 111,781 tokens 的 RFC 语料，它允许把全文完整披露 4.7 遍，作为最外层 tripwire 完全不 bind。
>
> 改为**逐文档**计量，并设在 TLP §3.c.iii(y) 五分之一阈值之下：RFC 9110 上限 18,000 tokens（该文 90,666 的五分之一为 18,133），RFC 9112 上限 4,000 tokens（20,231 的五分之一为 4,046）。
>
> 收紧的理由不是“少发更安全”，而是它把 `docs/compliance/rfc-source-terms.md` 记录的 uncertainty #1 机械地解掉。该条原文说：五分之一以整份文档计量、本项目以 token 与字节计量，两套口径条款未规定如何换算，因此“累计摘录是否已触及该阈值”无法仅凭现有上限判定。**分母现在已经实测**，把上限设在其下即意味着 §3.c.iii(y) 的附加归属义务在任何运行下都不会产生。措辞须准确：超过五分之一触发的是“须一并包含全部 IETF 声明”的附加**义务**，不是禁止；此处收紧是让义务不产生，不是声称超过即违规。
>
> **[已落地 2026-08-08]** 四步全部完成。`default-v1.json` 新增 `corpus_document_unique`，两份 RFC 的上限取实测量的整数五分之一：9110 = 314 excerpt / 18,412 tokens / 76,113 bytes（实测 1571 单元 / 92,064 tokens / 380,569 bytes），9112 = 70 / 3,943 / 16,069（实测 351 / 19,717 / 80,345）；三维都按可索引单元（条款 + 表格）计。**bytes 是承重的那一维**——它精确、与分词器无关，且两侧同法计量（都取该字符串的 UTF-8 字节数），五分之一论证在字节上无需换算即成立；tokens 只是次级守卫，此处用 BGE-M3，而 enforcer 计量 excerpt 用的是 provider 模型的分词器，两者词表不同。合成 fixture 语料另立一档，因为 demo profile 必须能在无 key 下跑通。分母取的是**可索引条款文本**而非整份发布文件，比按分发原件计量更严。enforcer 按 `request.version.document_id` 分账，该键可信是因为 `_validate_version_binding` 已要求它等于 enforcer 从自有 store 解析出的 source manifest 的 `document_id`；账目存在同一行 corpus ledger 内，因此 ADR 0001 的单锁序列化不变。未被计价的 document 一律 fail closed（`corpus_document_cap_missing`），理由是没有实测分母就没有可辩护的上限，默认一个等于连许可论证一起默认了。测试覆盖逐文档独立累计、越限拒绝、未计价拒绝、jsonb 持久化往返（经变异验证：去掉该字段该测试即失败），以及一条**上限断言测试** —— 任何人调高上限而不重新测量，都会当场红。
- **合规评估门禁（自评，无外部审批方）。** 每个真实语料 source_manifest 默认 `cloud_egress_authorized=false`。**先说清这道门是什么：没有任何机构会为本项目签字。** 3GPP 不会答复“能否用 API 处理其规范”这类询问，项目也没有法务或 DPO 参与。因此该字段的取值是作者本人的判断——这道门禁的价值不在于取得许可，而在于让判断被记录、可回溯、推理过程可被第三方复核。把它当成等待外部回复的动作，W0 会卡在一个永远不会到来的信号上。

  W0 内必须完成并留档的四项：

  1. **读语料侧条款。** 3GPP/ETSI 的 copyright 与 terms of use 声明，存页面快照，写下解读结论**与不确定之处**。要预先接受一件事：这些条款大概率没有直接回应“把条款片段发送给第三方推理服务”——它们是为再分发和转载写的，此处只能外推，而外推的理由必须落到纸面；
  2. **读 provider 侧条款。** 主链与评分器两个 provider 各查保留期、是否用于训练、处理区域、子处理方、是否存在更严格的可选条款，各存一份 policy snapshot；
  3. **把出站上限作为判断的事实前提写入记录。** 单次 L1 运行至多 5 个 excerpt、合计 2,560 tokens，单片段不超过 512 tokens。被判断的行为是“为处理一次查询发送一份公开标准的若干条款片段”，不是“分发该规范”——这两件事的结论可以不同，而区别正来自本节的上限设计；
  4. **结论落地。** 通过则新建明确绑定 provider route/用途的 successor source_manifest，不在原对象上翻转字段；不通过则按 §4.6.1 落到路线 B 或 C。

  后续 corpus_manifest 只能引用与本次处理链相容的 source_manifest。未通过时 provider transport 必须 fail closed，路由冒烟测试只能使用 fixture。

  两条不得走的捷径：不得用“公开可下载”替代判断；也不得用“发送量很小”单独替代判断——出站上限是论证的组成部分，不是论证本身。
- **备用语料已点名：IETF RFC。** §4.6.1 的 go/no-go 若判定 no-go，切换目标不能停留在“许可兼容语料”这样一句没有主语的话——真触发时 W0 之后会变成重新做语料选型，整个排期报废。选 RFC 的理由是它对本方案几乎逐条对应：条款编号严格且层级清晰；交叉引用密集，`expand_references` 有真实用武之地；RFC 2119 的 MUST / MUST NOT / SHOULD / SHOULD NOT / MAY 与 §4.1 第 4 步基于 TR 21.801 的模态标注同构，解析器只需换一张关键词表；纯文本/XML 分发，省掉整套 OOXML 沙箱风险面；且允许自由再分发——切换后双轨制可以降为单轨，真实语料直接进仓库，§8.0 与 §9.6 的复杂度同步下降。代价是丢掉通信规范这个领域叙事、L2 案例全部重构。这是降级方案而非等价替换，但它可执行，这就是它存在的全部意义。
- 中文演示语料只有在主线完成后再增加，不作为首发依赖。

### 3.3 明确不做

- 模型训练、微调、强化学习与推理加速；
- 通用企业级 Agent 平台；
- 独立 Registry REST 服务、灰度发布和分布式 Agent 部署；
- 将历史答案直接写入事实长期记忆；
- 首发阶段的跨 Release 自动差异系统；
- 首发阶段建设 Dify 或 Coze；Dify 仅保留为发布后 backlog。

---

## 四、系统架构

### 4.1 离线文档管线

> **[已变更｜当前生效]** 下方 OOXML、TR 21.801 与 3GPP 抽样 QA 是原路线设计。当前实现已切换为 RFCXML 章节树、条款、表格、规范性关键词和交叉引用抽取；RFC 9110 smoke 得到 288 sections / 1,559 clauses / 2,519 cross-references，RFC 9112 得到 56 / 348 / 458（收集式 ABNF 附录已按 anchor 排除，对两份文档一致生效），二者 dangling cross-reference 均为 0。独立 BM25、dense route 与 RRF 已实现。**[已完成]** corpus manifest `1abafff7…` 已冻结 1,922 个 points；L1 的 20 条已完成一次性 pooling completeness audit 并封存。**[未完成]** L2 gold/pooling、主集整体锁定与完整质量评测仍在后续范围。

文档管线负责把公开规范变成可检索、可定位和可校验的数据：

1. 下载并校验文件，记录来源、版本和 SHA-256；
2. 解析标题、章节编号、正文与表格；source locator 使用段落序号、表格序号、bookmark 或 OOXML 节点位置，不承诺从 .docx 稳定恢复页码；
3. 条款作为逻辑 parent 保留完整章节路径；按 BGE-M3 tokenizer 计算长度，超过 7168 tokens 的长条款或大表格拆成带 256-token overlap 的 child chunks，每个 child 继承同一 clause ID 并记录行/段落 span，不依赖模型侧静默截断；
4. 按 3GPP TR 21.801 标注 shall、shall not、should、should not、may、need not 等显式规范性模态；is/are 陈述只标记为 declarative statement，不自动当作规范性要求，是否支持 L2 主张由后续语义判定负责；
5. 抽取术语表、缩略语和条款交叉引用；
6. 稠密向量由本地 BGE-M3 生成（1024 维），写入 Qdrant 的 dense 字段；稀疏检索走**独立实现的 BM25**，写入 sparse 字段。**不使用 BGE-M3 自带的 learned sparse**——那会让 §8.2.3 pooling 的两路候选共享同一个编码器的表示偏差，“降低对自身系统偏向”的论证随之失效。两路来源相互独立是该节成立的前提。BM25 的分词需针对本语料定制：条款编号（`5.3.1`）、缩略语（BSR、DRX、HARQ）、连字符术语必须作为完整 term 保留，默认按空白与标点切分会把 `5.3.1` 打碎成三个数字、把检索质量拖垮，而条款编号恰恰是本任务最高区分度的 term。IDF 在冻结语料上计算，随索引一并版本化；
7. 将解析失败或缺少条款编号的内容放入隔离区，不进入正式索引。

corpus_manifest 冻结前执行预注册解析 QA。每份规范逐一核对源目录中的全部顶层章节，要求零缺失；再分层抽查至少 20 个编号条款、10 个表格和 10 个交叉引用。抽样中的 clause ID/section path 必须 100% 正确，表格文本保真与交叉引用目标正确率分别至少 90%；含 shall/shall not 等模态但未归属条款的 orphan normative paragraph 不得超过候选规范段落的 1%，全部隔离内容不得超过解析 block 的 2%。这些是工程阻断线而非统计性质量证明；任一项未达标时不得冻结 corpus_manifest，必须修解析器、明确排除范围或调整首发语料。

存储建议：

- Qdrant：稠密与稀疏检索；
- PostgreSQL：文档元数据、运行轨迹、评测记录和检查点；
- 本地文件：原始下载缓存与解析中间产物。

### 4.2 在线 Agent 主链

1. FastAPI 接收问题、任务类型、规范范围、corpus_manifest_id、可选的更严格出站模式请求和预算；服务端依据已认证 profile、corpus manifest 与 provider route 解析不可变的 `resolved_egress_policy_id`，客户端不能指定更宽松 policy；
2. Orchestrator Agent 判断 L1/L2，基于固定的类型化组件配置生成不超过四步的执行计划；
3. Evidence Agent 通过 MCP 发现并调用检索工具；
4. L2 请求进入 Compliance Agent，拆分主张并形成结构化判断；
5. 每次云端调用前，`EgressPolicyEnforcer` 按字段白名单、excerpt 长度和运行级累计账本审查 payload；
6. Verifier 检查引文、manifest、适用范围和结论支持关系；
7. 校验失败时允许一次定向重检索，但不重置出站账本；仍失败则返回“证据不足”；
8. API 以 SSE 返回答案和执行状态，前端展示证据与完整轨迹。

### 4.3 Agent 职责边界

#### Orchestrator Agent

- 输入：用户请求、可用能力清单、预算；
- 输出：任务类型、步骤、依赖、负责 Agent；
- 限制：最多四个计划步骤，失败后最多重规划一次；
- 不负责：直接检索文档或自行生成一致性依据。

#### Evidence Agent

- 输入：检索目标、规范范围、corpus_manifest_id；
- 输出：结构化 Evidence 列表；
- 能力：查询改写、混合检索、章节树定位、精确取条款、交叉引用扩展、术语查询；
- 限制：L1 最多六次工具调用，L2 最多八次；不负责最终一致性判断。

#### Compliance Agent

- 仅在 L2 启动；
- 将用户描述拆成可独立判断的原子主张；
- MVP 单次请求最多接受三个原子主张；超出时要求用户拆分请求，避免固定八次工具调用预算被主张数量悄然耗尽；
- 对每个主张输出 compliant、violating 或 insufficient_evidence；
- 每个确定性判断必须引用 Evidence ID。

#### Verifier

Verifier 是独立闸门，不接受 Agent 的自我声明作为校验结果：

1. 引文经过规范化后必须存在于指定条款；
2. corpus_manifest_id、文档 ID、版本、条款编号、content hash 和适用范围必须一致；
3. 结论与证据的支持关系通过结构化判定；
4. 任何检查失败都不得输出“已确认符合/违反规范”。

其中，引文存在和版本一致可以确定性校验；“证据是否支持结论”仍可能包含模型判定，因此必须单独评测，不能把它宣传为绝对正确。

### 4.4 组件配置与工具发现

- Agent 图与职责边界使用普通类型化配置声明，MVP 不实现动态 Agent Registry，也不宣传“新增 Agent 无需修改路由”；
- MCP 工具通过协议完成发现，工具 schema、timeout、cost_tier、enabled 与 health check 随配置版本化；
- Orchestrator 只能从当前图中已启用的 Agent 与 MCP 工具生成计划，不做分布式服务发现、灰度控制或运行时插件装载。

### 4.5 状态与记忆

本项目**有意不建设长期事实记忆**，这是一个主动的设计判断，不是能力缺口。

理由：规范会随 Release 迭代，被缓存的历史结论在版本变化后会静默失效，而系统无法自动检测某条缓存的结论是否已被新版条款推翻。一个把历史答案当事实复用的记忆层，在规范一致性场景下会稳定地产出看似有据、实则过期的结论——这比没有记忆更危险。因此只保留两类状态：

- **会话工作状态**：当前计划、已找到证据、工具结果和剩余预算，由 LangGraph checkpoint 管理；
- **版本化偏好**：用户指定的规范范围、Release 或输出格式，写入前需明确确认，并带版本与更新时间。

历史答案不作为事实缓存直接复用。规范版本、索引版本或工具版本变化后，相关偏好和缓存必须失效。

面试中不应把这一节包装成“实现了记忆系统”，而应作为一个有明确理由的取舍来讲。

### 4.6 模型选型与成本控制

| 角色 | 模型 | 运行位置 |
|---|---|---|
| 主链（Orchestrator / Evidence / Compliance / Verifier 语义判定 / L1 答案生成） | `deepseek-v4-flash` | 云端 API，provider 可配置 |
| 评分器（§8.3） | `glm-5.2`（经 ChatAnywhere API 路由） | 云端 API，provider 可配置且须在 W1 实测 |
| Embedding | **BGE-M3** | **本地推理** |
| BM25 稀疏检索 | 无模型 | 本地，纯统计 |

表中是 **W0 选定的计划路线 A（云端主链）**，但它不是绕过合规评估门禁的保证——§4.6.1 的三条分支仍然成立，授权不通过时按那张表落到 B 或 C。若 W1 选择本地替代，主链通过同一 provider adapter 接入 `local_openai_compatible` route，必须记录实际权重 hash、量化、后端、上下文限制、内存占用，并在 fixture 上通过 tool calling/结构化输出 smoke test；评分可选另一款本地模型或直接走预注册人工评分。没有通过这些测试的“以后可以换本地模型”不算替代方案。

**Embedding 放在本地是由 §3.2 的数据最小化政策决定的，不只是性能选择。** 原始文档、全量条款、完整 TOC 与检索候选留在本机；在线回答和离线批量评测的云端调用都受 §3.2 出站闸门约束。发送前后记录字段类型、token 计数、Evidence hash 与 provider 路由，但普通日志不保存 query、设计描述或 excerpt 正文。

BGE-M3 的 8192 序列长度比常见的 512-token embedding 模型更适合长条款，但这是上限，不是“所有条款都不会溢出”的保证。§4.1 使用显式长度检查和 parent-child 切分；多语言能力则让后续中文 fixture 可在不更换 embedding 模型的情况下运行。

> **[已失效｜选型理由收窄]** “8192 序列长度适合长条款”在当前语料上不再起作用：最长 clause 为 250 tokens（9110）与 683 tokens（9112 附录 A，已排除出索引），全部落在常见 512-token 模型的窗口内。BGE-M3 继续保留，但成立的理由只剩多语言能力（后续中文 fixture 不必换模型）与已完成的实测吞吐。不要在报告或面试中把长序列能力列为本项目的选型依据 —— 它在这个语料上没有被用到。

评分器刻意与主链使用不同 model slug 和独立调用路径，理由见 §8.3.3——这能降低最直接的同模型自评偏差，但不能证明误差独立，仍须用锁定输出上的人工审计验证。

云端默认路线的两个 LLM 使用彼此独立的 provider 配置，不预设能共用 base_url、key、速率限制或上下文上限。`deepseek-v4-flash` 的官方模型 ID 与上下文规格已知；W1 要验证的是实际选定 provider 的可用模型清单、tool calling、结构化输出、响应元数据和输入限制。`glm-5.2` 只有在 ChatAnywhere 路由实测可用且真实语料评分路线获授权后才成为正式评分器；若不可用，改用另一款与主链不同源、且在 §8.3.2 校准达标的评分模型或预注册人工评分。评测报告记录 provider、端点标识、实际 model slug、调用日期和可获得的响应 fingerprint。

- 模型 ID、provider 与端点标识写入配置，评测报告记录主模型、评分器（若使用模型）和 embedding 的实际 ID/权重 hash、响应元数据与全部采样参数；人工评分则记录评分协议版本而不是虚构第三个模型。在接口支持时使用 temperature 0，但不把它宣传为严格确定性保证。
- 分级路由：DeepSeek V4 Flash 本身已是低成本档，先不做二级路由，跑通并实测单题成本后再决定是否把任务分类、查询改写下放。路由规则写入配置，不硬编码在代码里。
- embedding 结果按（模型权重 hash、归一化/切分版本、文本 hash）缓存。本地编码虽无 API 成本，实际 chunk 数与全量耗时在 W1 抽样测速前都不作承诺；解析或切分版本迭代、索引复建和开发诊断依赖可正确失效的缓存。
- LLM 响应缓存键至少包含 provider、实际 model slug/snapshot、完整渲染消息 hash、采样参数、tool schema 版本、tool result 内容 hash、corpus manifest hash、索引版本与 prompt hash。任一实验处理条件变化都不得命中另一条件的缓存，并提供强制失效开关。
- 正式成本与 P50/P95 延迟在冷缓存下测量，缓存命中运行只用于低成本复查，必须单独标记，不能与冷缓存结果混排。
- 记录每题成本，设置项目总预算上限，超限时先降级消融规模而不是砍评测集。

响应缓存不是可选优化。开发诊断、核心对照和日常迭代都需要复跑；无缓存时成本会高到让人不敢跑评测，而这会直接摧毁“评测驱动开发”这一整套工作方式。

#### 4.6.1 W0 必须确认的三件事

> **[已变更]** 三路线 go/no-go 已选择 **C（IETF RFC）**，因此 3GPP 云端授权与 OOXML 摄取不再是进入 W1 的前置条件。**[已完成]** `EgressPolicyEnforcer`、PostgreSQL 原子账本和 fixture transport 已通过多轮、重试、越权、并发与恢复测试。**[已实测 2026-08-09]** 两条真实 provider route 已用合成 payload 实测可用，详见下方第 1 项的附注。**[已完成 2026-08-10/11]** RFC 9110/9112 已分别写入 `cloud_egress_authorized=true` 的不可变 successor manifest，绑定 `deepseek` / `online-main`，作者授权结论于 **2026-11-08** 到期；真实 RFC excerpt 已经统一出站闸门和原子账本跑通 L1 answer path，并实测了可回答与拒答两个方向。授权仍以 provider、用途、上限与到期日为硬边界；旧的 default-deny predecessor 保留且不可用于出站。
>
> **[表述修正｜下方第 1 项按字面不可执行]** 第 1 项写“在 W0 **用 fixture** 分别验证 `deepseek-v4-flash` 与 `glm-5.2` 的**真实调用**”——这句自相矛盾：fixture route smoke 按定义不触达任何 provider，无法验证真实调用。W0 据此把该项报告为已完成，而同期的 `docs/reports/w0-foundation-report.md` 又写明该 smoke “proves nothing about any real provider”。两份记录都对，问题在要求本身。
>
> 第 1 项应读作：**用合成 payload 对真实 provider 端点发起真实调用**，验证 model slug 可达、tool calling 与结构化输出可用、响应 metadata 与账户级数据政策；**fixture adapter smoke 不满足本项**。
>
> **[已实测 2026-08-09]** 两条路由各跑了一次 `provider route-smoke --live`，合成 payload，语料未出站（manifest 绑定 `synthetic-fixture-spec`）。
>
> | | main | judge |
> |---|---|---|
> | provider / slug | `deepseek` / `deepseek-v4-flash` | `chatanywhere` / `glm-5.2` |
> | slug 被认下 | 是 | 是 |
> | usage metadata | 有 | 有 |
> | `finish_reason` | `tool_calls` | `stop` |
> | tool call | **1** | **0** |
> | 请求字节 / provider `prompt_tokens` | 495 / 414 | 353 / 228 |
>
> **退役的部分：** 两个 model slug 在各自 provider 上确实存在并应答，响应 metadata 可获得。§4.6.1 第 1 项就此部分退役，路线 A 的“provider 路由可用”前提成立。
>
> **没有退役的部分，必须分开说：**
>
> 1. **judge 路由的 tool calling 仍未证实。** `glm-5.2` 收到了同一个 tool 定义但选择不调用，返回 `stop`。这既不是“不支持”也不是“支持”——模型可以对被提供的工具选择不用。按 §8.3.1，judge 的职责是逐采分点判定，需要的是**结构化输出**而不是 tool calling，所以这不构成阻塞；但报告里不得写成“两条路由都验证了 tool calling”。
> 2. **W3 已有五个真实工具 schema 与有界 planner，但尚未实测真实 provider 上的自主工具选择质量。** W3 发布门禁使用本地 `FakeProvider` 跨过了 MCP/API/browser 的真实组件边界，且明确没有调用真实 provider。因此可以声明 schema、调度、账本和轨迹已连通，不能把它外推为真实模型的工具选择准确性。
> 3. **真实 excerpt 出站门禁已退役，但授权边界没有退役。** 两份授权 successor、provider/use 绑定、一五分之一 cap 前提和 2026-11-08 到期日必须在每次真实运行时继续 fail closed。
>
> **附带得到的一个测量：字节上界在两条真实路由上都成立**（414 ≤ 495，228 ≤ 353）。n=2、短英文、含 tool schema，证据很弱，但方向与 `ByteUpperBoundCounter` 的构造性论证一致。收紧该计数需要更多样本，尤其是中文与长条款。

三项都是方案的硬依赖，必须在 **W0** 内、任何真实条款出站前落地：

1. **实际 provider 路由是否可用。** 在 W0 用 fixture 分别验证 `deepseek-v4-flash` 与 `glm-5.2`（或候选评分模型）的真实调用、tool calling/结构化输出能力、响应 metadata 与账户级数据政策；公开模型 ID 不等于实际 provider 路由已支持。
2. **出站闸门是否在多轮下仍生效。** 用可审计的测试 transport 覆盖正常调用、查询改写、交叉引用、Verifier、评分器、重试与恶意“读取全文”请求，确认字段白名单、单片段、unique/transmitted token/byte 与原子累计上限均不可绕过；另跑“三个 L2 claim、12 个满长 Evidence、Evidence→Compliance→Verifier、一次重试、可选 judge”的最大合法包络，证明正常链不会被错误拒绝。任何测试都不出站原始文件、完整 TOC/索引、完整条款、未脱敏错误或日志。
3. **合规评估与替代路线是否形成 go/no-go。** 按 §3.2 完成四项评估（语料侧条款、provider 侧条款、出站上限作为判断前提、结论落地）；默认 false 的 source_manifest 保持不变，评估通过时新建 successor，并验证政策过期、用途/provider 不匹配或引用旧 manifest 都 fail closed。

**计划路线是 A。** W0 结束时按下表选定一条并写入配置，不允许“边做边看”：

| | 路线 | 触发条件 | 代价 |
|---|---|---|---|
| **A（计划路线）** | 3GPP + 云端主链 `deepseek-v4-flash` + 云端评分器 | §3.2 四项合规评估全部完成留档且结论为可进行，且主链/Verifier 与 judge 两条 provider 路由实测可用 | 无 |
| B | 3GPP + 本地主链 + 本地或人工评分 | 合规评估结论为不可进行，但目标硬件已完成结构化输出、时延与成本预算 smoke test | 主链能力下降；§8.5.2 的模型固定项改为该本地模型 |
| C | **切换 IETF RFC** + 云端主链 | 合规评估结论为不可进行，且本地硬件不可承载 | 丢通信规范叙事、L2 案例重构；换来双轨制降为单轨（§3.2） |

三条都不成立时不得进入 W1，把项目延长到 9–10 周重新评估。**选定 A 意味着 §3.2 的四项合规评估必须在 W0 内完成并留档**，不是可以事后补的手续。它是半天到一天的阅读与记录工作，不是等待外部回复——若 W0 内没做完，那是排期执行问题，不能当成外部阻塞。评估结论为不可进行时直接走 B 或 C，不要带着未决状态进 W1。

#### 4.6.2 本地推理的硬件要求与影响

- **一次性索引构建**：W1 先用抽样语料分别测量目标机器上的 MPS/CPU 吞吐，再据真实 chunk 数估算全量时间；未测前不写“几十分钟”之类的具体承诺。索引完成后靠内容寻址缓存减少重复编码。
- **查询侧编码进入延迟指标**：每次检索都要编码 query。§8.4 的 P50/P95 延迟**包含这一段**，报告同时注明测试机型、后端和实测 query-encoding 子耗时，否则延迟数字无法跨环境比较。
- **CI 不下载模型**：fixture 语料的向量预先算好提交进仓库（合成语料，无授权问题）。CI 只做检索与断言，不跑任何模型推理，也不拉取本地 embedding 权重（§9.4）。
- 模型权重不进仓库，由 `ingestion` 的初始化脚本按需拉取并校验散列。

---

## 五、MCP 工具层

### 5.1 首发工具

| 工具 | 作用 | 关键约束 |
|---|---|---|
| search_clauses | 在本地混合检索相关条款，可按规范性等级过滤 | 必须传入 corpus_manifest_id 与规范范围；候选池不出站，只有闸门放行的 excerpt 可进入模型 payload |
| get_clause | 按 manifest、文档和条款编号在本地取原文 | 完整条款只供本地校验；云端只能看到不超过 512 tokens 的 excerpt 与 opaque Evidence ID |
| get_toc | 在本地读取章节树，或返回指定范围内的有界标题节点 | 完整 TOC 不出站；模型单次最多获得 12 个、每运行累计最多 24 个本地预筛节点，且不含正文 |
| expand_references | 沿交叉引用在本地扩展一跳 | 单次最多新增 3 个候选，出站时仍受运行级 Evidence 总上限约束 |
| lookup_term | 查询术语和缩略语 | 返回来源条款 |

原方案的 search_requirements 已合并进 search_clauses。两者的差别只是一个规范性等级过滤条件，作为独立工具会造成语义重叠：模型选错工具时，无法区分是 Agent 能力不足还是工具边界本身有歧义，§8.4 的“必需工具召回率”和“允许工具集合 precision”会被污染。

腾出的位置给 get_toc。它覆盖一类检索工具无法替代的能力——按结构定位（例如“38.321 第 5 章下有哪些子条款”）与范围收敛，让工具选择评测真正测到能力差异，而不是测同义工具之间的抛硬币。

### 5.2 工具调用工程约束

- 所有输入输出使用 Pydantic schema；
- 每次调用记录 tool_call_id、工具名、参数、耗时、结果数量和错误；
- 超时只重试一次，并使用退避；
- 非幂等工具不自动重试；首发工具全部为只读；
- 参数校验失败时，模型只收到结构化错误码、失败字段名和修正提示；不回传原始任务全文、完整条款或底层异常堆栈；
- 工具循环达到 max_tool_calls 后强制停止，转为证据不足或降级回答。默认值按任务类型区分：L1 为 6，L2 为 8。
- 工具次数预算与出站预算彼此独立；多一次工具调用不会获得新的出站额度。

条款正文超过单片段上限时，由 `ExcerptWindowSelector` 决定送出哪 512 tokens；默认策略由 §8.5.2 的锁定对照结果确定，W5 之前使用 W-head 作为占位实现。选中窗口的 span 记入 `excerpt_span` 与 `disclosure_id`，不同窗口即不同披露单元。

> **[已收窄并已修复]** RFC 语料曾有 1 个 clause 超过 512 tokens（RFC 9112 附录 A 的收集式 ABNF，683 tokens），因此 `ExcerptWindowSelector` 不在主链关键路径上：W5 不为它安排实现工时，W-head 等价于恒等映射，`excerpt_span` 与 `disclosure_id` 的语义不变。原先按 `document_id` 硬编码的排除已改为对所有文档按 `collected.abnf` anchor 生效；`ClauseLimits.max_words` 仅作廉价预过滤，准确的 BGE-M3 token 与 byte 检查已移到冻结必经的 `corpus qa` / `excerpt_fit` 阻断线。当前可检索单元为 RFC 9110 1,571/1,571、RFC 9112 351/351 全部通过，不再存在“可检索但必然无法出站”的已知单元。

`EgressPolicyEnforcer` 是唯一允许构造 provider payload 的组件。工具层返回的本地对象默认不可序列化到 LLM 消息；只有经白名单投影后的 query/claim、必要元数据、有界 TOC nodes 和 Evidence excerpts 可放行。每次放行记录 policy ID、provider route/role、字段类型、unique/transmitted token 计数、disclosure/Evidence/content/quote hash、excerpt span 与累计使用量，不记录正文。

出站账本以 `(run_id, resolved_egress_policy_id)` 为键持久化到本地 PostgreSQL。每次网络调用前必须在事务中原子执行 `check-and-reserve`；并发 worker 共用同一行锁/CAS，重试用稳定 idempotency key 复用原 reservation，但每次传输尝试仍累计 transmitted tokens。进程恢复时从账本和 LangGraph checkpoint 继续原额度；账本不可读写、reservation 状态不明或提交失败时一律不调用 provider。provider adapter 不提供绕开该组件的公共发送入口。

L2 的上限从 6 放宽到 8，原因是工具增加到五个之后预算变紧了：一条完整的 L2 证据链可能是 get_toc → search_clauses → get_clause → expand_references → lookup_term，已经用掉五次，只剩一次留给重试。在这种预算下测出来的“错误恢复成功率”反映的是预算不够，而不是恢复能力本身，指标会失去它要测的含义。放宽后该指标才测得到真实的恢复行为。

上限调整不影响编排诊断的公平性：§8.5.4 的 L2 单决策 Agent 与职责分离共用同一个 L2 工具预算（max_tool_calls=8）与出站上限。

### 5.3 双入口复用

共享工具实现是唯一知识访问层：

- 检索、取条款、引用扩展和术语查询只有一套 Python 实现；
- LangGraph 主工程通过 MCP Streamable HTTP 调用独立 `mcp` 容器；
- 发布后的 Dify 展示版可通过 HTTP Request 节点调用带认证、同样经过出站闸门的只读 FastAPI tool gateway；不直接暴露 MCP 或无策略的 get_clause，真实语料模式仍只允许 localhost/私网访问；
- MCP server 与 HTTP gateway 复用同一实现、schema、索引和日志；
- 两个入口不得各自维护文档索引、提示词事实或复制版知识库。

---

## 六、数据契约

### 6.1 请求

请求至少包含：

- query；
- task_type：auto、qa 或 compliance；
- spec_scope；
- corpus_manifest_id：唯一绑定纳入语料的文档版本、文件 hash、解析版本与索引版本；
- requested_egress_mode（可选）：客户端只能请求 `local_only` 或比服务端默认更严格的策略；服务端根据认证 profile、corpus manifest 和 provider route 解析并在响应/轨迹中记录 `resolved_egress_policy_id`，拒绝任意 policy ID 注入；
- max_steps、max_tool_calls、max_tokens、max_cost；
- session_id。

corpus_manifest_id 缺失或 spec_scope 不属于该 manifest 时，系统必须先澄清；不得用单个全局 document_version 代替两份规范各自的版本。

### 6.2 Evidence

每条证据至少包含：

- evidence_id；
- corpus_manifest_id；
- document_id、document_version；
- clause_id、section_path；
- content_hash、quote_hash；
- disclosure_id（`corpus_manifest_id + content_hash + quote_hash + normalized_excerpt_span` 的稳定 hash）；
- quote（只是已放行 excerpt）、excerpt_span、source_locator（段落/表格序号、bookmark 或 OOXML 节点位置）；
- retrieval_method、retrieval_score。

### 6.3 答案与规范一致性判断

L1 的模型输出不是只有一段自由文本：先生成结构化 `answer_claims[]`，每个事实或规范性陈述包含 claim、evidence_ids 与 certainty，再由服务端渲染为 answer_text。纯衔接语句可不绑定证据；任何确定性事实 claim 都必须进入该数组，供 Verifier 和 §8.4 的 unsupported/contradiction 指标复核。

每个原子主张至少包含：

- claim；
- verdict；
- evidence_ids；
- rationale；
- verification_status。

前端和 API 只显示通过 Verifier 的确定性判断；未通过项统一标记为 insufficient_evidence。

### 6.4 三层 manifest

- **source_manifest（W0）**：只冻结文档 ID/版本、下载 URL、ZIP/DOCX hash、合规评估记录与下载时间。初始版本在 §3.2 合规评估**之前**冻结（评估对象必须是具体文档版本），评估通过后新建 successor；
- **corpus_manifest（W2）**：引用 source_manifest，并新增解析器/分词/切分版本、embedding 权重 hash、BM25/RRF 参数、版本化 Qdrant collection/snapshot ID、collection schema、point count、point/content-hash inventory root 与派生语料 hash。冻结后 ingestion 身份失去该 collection 的写权限，在线服务只读；启动时复核 schema、point count 和 inventory root，不符即拒绝加载。
- **evaluation 层（W5 预注册，W6 封存）**：W5 的不可变 `run_spec` 引用最终 corpus_manifest，并预注册代码/依赖/镜像、评测集与聚合脚本、prompt/config、SDK/API、provider policy snapshot、模型/权重 allowlist、seed 策略、硬件/后端、采样参数、condition matrix、case IDs 与顺序规则、cache mode、重复次数、配对/父 artifact 规则及 protocol-deviation 处理。每次执行先创建稳定 `run_id` 和仅追加的 attempt 事件；完成后才封存不可变 `evaluation_run_manifest`，记录 condition/replicate/pair ID、实际 case 顺序、parent candidate artifact hash、实际模型/fingerprint/seed/环境、开始结束时间、用量/错误/状态，以及逐案例输出与脱敏 trace 的内容 hash。所有运行结束后再封存 `evaluation_report_manifest`，引用 run manifest IDs，并绑定人工审计标签、聚合产物和报告 hash，使每个报告数字可反查到逐案例结果。

这里仍是 source、corpus、evaluation 三层；`run_spec`、attempt、逐次 run manifest 与 report manifest 是 evaluation 层的预注册、执行、封存和汇总阶段。除仅追加的 attempt 事件外，所有 manifest 都是不可变快照；任一已绑定字段变化都生成新 ID，不在原 ID 上覆盖。运行预检遇到 provider/model/policy/代码/环境偏离时 fail closed 或先创建新 `run_spec`；运行中才出现的偏离封存为 `protocol_deviation`，不得与原配对结果聚合。Evidence 与 run 必须携带同一 corpus_manifest_id，Verifier 对跨 manifest 证据 fail closed。

---

## 七、异常处理与降级

| 异常 | 处理策略 | 可评测指标 |
|---|---|---|
| 结构化输出校验失败 | 只带脱敏错误码、失败字段和已放行上下文重试一次；重试共用原出站账本 | schema failure rate |
| MCP 超时或连接失败 | 退避后重试一次，记录错误并允许部分结果 | tool success rate |
| 检索为空 | 改写查询一次，仍为空则澄清或拒答 | empty retrieval recovery |
| 文档版本不明确 | 请求用户补充版本 | version clarification rate |
| 引文不存在 | 定向重取条款一次，仍失败则移除结论 | invalid citation rate |
| 证据不支持结论 | 重检索一次，仍失败则证据不足 | unsupported claim rate |
| Agent 循环 | max_steps 与 max_tool_calls 双限制 | loop termination rate |
| token/成本超预算 | 停止扩展、缩小候选集或拒答 | budget overrun rate |
| 出站超限、字段越权或授权失效 | 本地闸门 fail closed；可用时转本地 LLM，否则拒绝本次云端处理 | egress policy violation rate |
| 文档解析失败 | 隔离失败文档，不写入正式索引 | ingestion quarantine rate |

系统必须“失败可见”：不得用空字符串、默认答案或吞掉异常来伪装成功。

---

## 八、评测设计

### 8.0 两套语料、两套评测、数字来源

本项目有两套语料，对应两套用途完全不同的评测。**先划死界限，因为混淆两者会让 §8 其余所有内容一起失效。**

| | 真实语料 | fixture 语料 |
|---|---|---|
| 内容 | 冻结版本的 IETF RFC 9110 与 RFC 9112 | 合成或许可宽松的小型规范 |
| 是否进仓库 | 否——原文、引文、索引一律不提交 | 是，含预先算好的向量 |
| 运行位置 | 原始语料、完整索引与候选检索仅本地；经授权的在线主链和离线批量评测可按 §3.2 发送受限字段 | 本地与 CI |
| 用途 | **产出报告中的全部指标** | 管线冒烟测试、离线演示 |
| 交付形态 | 版本化评测报告（数字进报告，语料不进仓库） | CI 绿灯 |

两条不可逾越的规则：

1. **报告里的每一个质量指标数字都来自真实语料。** Macro-Recall@5、L2 各类 precision/recall/F1、拒答指标、L2-adv 误确认率，以及启用自动 judge 时的人机 kappa——无一例外；纯人工路线不产生 kappa。
2. **CI 在 fixture 上跑的评测不是质量指标，而是“管线还能不能跑通”的冒烟测试。** 通过标准是没有崩溃、schema 合法、Verifier 拦截路径被触发过，**不是任何准确率阈值**。CI 输出中不得出现任何形似质量指标的数字，以免被误读。

为什么这一段必须存在：双轨制之后，若 CI 绿灯旁边挂着一个 Recall 数字，读者——包括半年后的你自己——会默认那是系统的真实检索质量，而它实际上跑在合成语料上。这层混淆一旦被发现，§8.2 的 gold 隔离协议、§8.3 的 judge 校准、§8.1.1 的对抗子集会被一起怀疑，因为尺子本身讲不清它量的是什么。整套可信度工程的前提是数字来源清晰，这比任何单个指标都重要。

### 8.1 数据集

> **[进行中｜2026-08-13 当前正式记录]** annotation store 共 23 条：L1 为 20/40（dev 15/15，locked 5/25），L2 为 3/20（dev 3/8，locked 0/12）。L1 的 20 条已完成人工 choice review、5/5 预注册 deep-review finding 和一次性 pooling audit；L1 `awaiting_adjudication=0`。剩余 20 条 L1 locked proposal 已完成第二批起草和自动校验，草案层已达到 locked 不可回答题 5 条的设计下限，但尚未经作者 Task 7 review，因此不计入正式 store 进度。L2 三条分别为 `violating`、`compliant`、`insufficient_evidence` 各 1 条，均经 model proposal 后由 human source review，retrieval-originated 为 0，仍报告 `awaiting_adjudication=3`。正式 store 仍未达的硬下限包括 L1 locked 不可回答题 2/5、L2 dev 不可回答题 1/2，以及全部 L2 locked 和 L2-adv。

首发评测集固定为：

- L1：40 道条款问答，其中 15 道开发集、25 道锁定测试集；
- L2：20 个规范一致性核查案例，其中 8 个开发集（3 compliant / 3 violating / 2 insufficient_evidence）、12 个锁定测试集（4/4/4）。**评测案例每例固定只含一个 canonical atomic claim**，以使案例数、claim 数和 verdict 分母一致；产品 API 仍可接受最多三个主张，但不把多主张聚合逻辑混入首发质量指标；
- L2-adv：16 个对抗案例组，独立于上述 20 个；6 个 `L2-adv-dev` 供 W4–W5 开发，10 个 `L2-adv-test` 在 W3 冻结并到 W6 才首次运行。这里的 16 指 16 个 negative scenario；每组另配一个同主题但最小改写后真正受证据支持的 positive claim，因此 Verifier 直喂评测共有 32 个 claim-evidence 项，见 §8.1.1；
- 从 L1/L2 中选取 16 题作为工具调用子集（8 dev / 8 locked），标注必需工具、允许工具、禁止工具和任务成功条件；
- 8 道工具开发题额外跑一遍故障注入模式（坏参数、超时、空检索、预算不足、出站越权），用于评测恢复能力。故障注入是工具层的运行时开关，不占用锁定测试集；
- L1/L2 的不可回答、范围外或证据不足比例对开发集与测试集分别成立：L1 至少为 dev 3/test 5；L2 按上面的固定分布为 dev 2/test 4，不是只满足总量；
- L2 主集的**任务级 gold**单独保存不可变 `expected_verdict ∈ {compliant, violating, insufficient_evidence}`，用于系统 verdict 混淆矩阵；Verifier 的**支持关系 gold**才使用 `(claim, proposed_verdict, evidence) -> supports_verdict`，用于判断某组证据是否支持候选 verdict。对 `violating` 项，证据支持的是“该设计违反相应要求”这一 proposed verdict，而不是字面上支持设计陈述。两套标签使用不同字段，不得互相代替；
- L1/L2 主集在拆分前按 `(canonical_clause_family, parent_clause, scenario_template/topic)` 联合分组，近重复改写与同一父条款族整体进入同一 split；冻结后输出跨 split 去重报告。任一类指标若仍出现零分母则记为 `NA`，不写 0 或跳过；
- 单人标注、无标注者间一致性，必须在报告中披露；
- 测试集锁定后，不因模型输出而修改答案；发现标注错误时记录勘误并重新发布版本。

规模相对 v2 下调约三分之一。原 60/30 规模的单人标注量与 W1–W2 的解析、检索开发无法并行完成。评测集小于预期可以在报告中披露，标注质量不足则会让全部指标失去意义——这是砍规模而不是砍单题标注深度的原因。

L1 保存问题、关键采分点、条款 ID、规范版本和预期拒答标签；L2 额外保存 canonical claim_id、expected_verdict，以及 Verifier pair 专用的 proposed_verdict/supports_verdict，不用“一个案例有几个主张”作为运行时变量。

**哪些字段可以进仓库。** 以下是项目采用的保守发布规则，不构成法律意见；正式公开前仍需按适用许可与司法辖区复核。原则上只提交自己撰写的问题、定位元数据和不复述条款表达的派生标注：

| 字段 | 可提交 | 说明 |
|---|---|---|
| 问题 | ✅ | 自己撰写 |
| 条款 ID、section_path | ✅ | 编号是定位符，不是内容 |
| 规范版本、预期拒答标签 | ✅ | 元数据与派生标注 |
| expected_verdict、supports_verdict | ✅ | 人工派生标签，不含条款表达 |
| 关键采分点 | ⚠️ **有条件** | 写成判定标准，可含必要的事实值（定时器默认值、状态名、参数范围）；**不得成句复述条款措辞** |
| 条款原文、引文 | ❌ | 属表达，一律不提交 |

采分点那条的实操标准：写“答案须指出该定时器默认值为 X 且仅在 RRC_CONNECTED 适用”可以，把条款那句话抄下来当采分点不可以。前者是事实，后者是表达。

L2-adv 的干扰条款描述同样按此办理——描述差异维度（适用状态不同、触发条件不同），不摘抄两条条款的原文。

#### 8.1.1 L2-adv 对抗子集

> **[已变更｜干扰维度换表]** 下方“差异位于适用状态、触发条件、UE 能力前提或协议层归属”是 3GPP 维度。RFC 语料的对应维度为：**请求侧 vs 响应侧**；**角色归属**（origin server / proxy / gateway / client —— 同一条要求对不同角色常有不同义务）；**文档归属**（RFC 9112 的 HTTP/1.1 具体语法 vs RFC 9110 的通用语义，同一概念在两份文档中约束不同）；**规范性强度**（`MUST` vs `SHOULD` vs `ought to`，v3 XML 的 `<bcp14>` 标记使其可机械区分）；**接收 vs 生成**（收到的字段与转发时新生成的同名字段是不同对象）。
>
> 最后一维已有现成样板：现有三条 L2-dev 正是围绕 received `Content-Length` 与 decode 后新生成的 `Content-Length` 之别构造的，可直接作为构造范例。W3 构造 16 组时按此表选维度，**每组记录所用维度**，以便报告中给出维度分布而不是只给一个总数。

该子集专门构造“引文存在、版本正确、但不支持结论”的情境，用于评测 Verifier 的语义支持判定——这是 Verifier 存在的唯一理由，也是 §4.3 中唯一无法确定性校验的环节。原有的“不可回答/范围外”案例覆盖不了它：那些是找不到证据，而这里是找到了看似高度相关的证据，但推不出结论。

构造方式：语料中存在与主张主题接近但适用条件不同的干扰条款，差异位于适用状态、触发条件、UE 能力前提或协议层归属等维度。

每个案例提供两类严格分开的材料：

- **端到端负例**：`negative_claim` 在当前 corpus view 中确无支持其 compliant/violating 结论的证据，但存在主题高度相近、适用条件不同的干扰条款。gold verdict 为 `insufficient_evidence`，观察系统是否误引干扰条款并给出确定性结论。
- **Verifier 直喂 matched pair**：负向项为 `(negative_claim, proposed_verdict, distractor_evidence, supports_verdict=false)`；正向项使用同主题但最小改写后的 `positive_claim`，配真正支持它的 evidence，即 `(positive_claim, proposed_verdict, supporting_evidence, supports_verdict=true)`。正负项是不同但匹配的 claim，不把一个无支持的 claim 同时标成可被另一证据支持。两者绕过检索直接送入 Verifier，隔离检索噪声并同时测量漏拦截与误杀。

`L2-adv-dev` 与 `L2-adv-test` 在 claim、canonical clause/scenario family、干扰条款和 positive pair 上互斥。单人构造意味着作者无法对测试内容真正盲化；“锁定”在本项目中只表示 W6 前不运行系统、不查看模型输出、不据此修改实现或 prompt，这一限制必须在报告中直说。

该子集不并入 L2 主集的 verdict macro-F1 计算，单独报告。若并入 12 例的锁定测试集，对抗案例将占比接近一半，指标不再代表正常分布下的表现。

### 8.2 gold 标注协议与循环论证隔离

> **[已变更并已实现]** 当前采用 provenance v2：模型、人工、目录导航和检索都可以提出 Gold，来源只进入有序 provenance 审计链，不再以“Gold 必须来自独立路径”作为入库限制；入库仍必须通过冻结 source 的 document/version、clause 存在性、词面重叠和 key-point 非复述校验。pooling 只能追加 successor，不覆盖历史记录。当前 `gold_clause_ids` 是 source correctness 的权威绑定；`gold_section_paths` 暂作 locator metadata，路径与 clause 的逐项 source 匹配延期实现。

gold 的发现和复核过程必须可审计披露。系统、模型或人工都可以提出候选，但每个 answerable record 必须按顺序记录 `gold_origins`（包括需要 producer 的 model/retrieval 事件）、内容与标签的 human/model/mixed 来源，并在报告中给出来源分布和完整事件链。来源只用于审计和解释诊断指标，不作为入库门禁；任何候选能否成为 gold 仍取决于下述冻结 source 的人工核验。这样既不隐藏检索或模型协助，也不把“检索器能否重新找到它自己找到过的条款”误写成独立的 Macro-Recall 证据。

#### 8.2.1 允许的来源与 source 核验

以下来源全部允许提出或协助形成 gold：规范目录与章节导航、全文字面匹配、交叉引用追踪、术语/缩略语索引、人工 source review、模型提案，以及 `search_clauses`、dense、BM25、hybrid 检索。前五种 human/source 事件不得填写 producer；model 与四种检索事件必须填写可审计 producer。`content_origin` 和 `label_origin` 分别记为 `human`、`model` 或 `mixed`。

允许来源不替代核验：每个 gold clause 仍须由标注者对照冻结原文、版本和 document identity 确认；系统输出“看起来合理”不是核验。记录不得含条款正文，answerable item 仍须有条款 ID、`question_gold_jaccard` 和非空来源事件；L1 不可回答项则没有 gold、重叠度或来源事件。不得在系统调优阶段回头修改 gold 以迁就输出。

#### 8.2.2 出题方向的混合与词面去污

本节的出题方向配比适用于 L1。L2 的输入是设计描述而非检索式提问，其 gold 条款仍按 8.2.1 的路径确定，但不套用下述比例。

纯 clause-first（先抽条款再出题）能天然保证 gold 独立于检索器，但问题会大量复用条款原词，导致靠字面匹配就能命中，指标以另一种方式虚高。因此采用混合出题：

- 约 60% clause-first：随机抽样条款后出题，且强制改写，问题中不得直接复用条款标题的完整措辞；
- 约 40% scenario-first：先写真实使用场景的问题，再用 8.2.1 的路径人工定位条款。

§8.1 要求的 20% 不可回答案例必须**刻意构造**，不能等着 scenario-first 出题失败自然产生。clause-first 的题按定义都可回答，全部不可回答案例只能来自 scenario-first 那 40%，指望其中一半恰好定位失败是不现实的。做法：先按目标数量直接设计范围外、跨规范、需要未收录版本才能回答的问题，再补足其余 scenario-first 题目。定位失败的题可以并入这一配额，但只是补充来源，不是主要来源。

每道**可回答题**记录问题与 gold 条款正文的 token 级 Jaccard 词面重叠度，评测时按重叠度分层报告 Macro-Recall@5，用以区分“语义检索确实有效”与“字面匹配侥幸命中”。不可回答题没有 gold 条款，不进入该分层。

#### 8.2.3 gold 补漏与 pooling 偏差

单标注者会漏掉同样正确的条款，导致系统检索到的正确结果被误计为错误。采用信息检索领域标准的 pooling 方法补漏：

- 取 BM25-only 与 dense-only 两路检索的 top-5 合并入池；每个候选与 producer、配置和顺序一起记录为 provenance event。这里的 BM25 是独立纯统计实现，dense 路由本地 BGE-M3 生成——两路不共享表示模型（§4.6）；
- 入池所用的 BM25 参数（k1、b、分词配置）必须冻结在基线取值并记录在 gold 元数据里。在线混合检索的稀疏路与它是同一份实现，日后调参会改变它的行为；pooling 只做一次且早于调优（§8.2.4），因此必须留下当时用的确切配置，否则事后无法说清 gold 是在什么条件下补的；
- 人工裁决池内条款；确认的候选以追加事件和 adjudication 写入 successor，既不删除既有 gold，也不删除既有来源事件；
- pooling 只在测试集锁定前执行一次，锁定后任何系统迭代都不得再补 gold；
- 报告中披露 pooled 条款占 gold 总量的比例。

pooling 无法完全消除偏差：未被两路检索召回的正确条款仍会缺失。不完整 relevance judgment 既可能因漏掉系统未召回的正确条款而高估 Recall，也可能把系统召回但未判定的正确条款计错而低估 Recall，偏差方向不固定。报告必须披露 pool depth、pooled 占比与未判定候选数量，不能宣称 gold 完备。

#### 8.2.4 时间隔离与披露

“先造尺子再造系统”这条原则按组件分别落地，不是一刀切：

- L1/L2 主集在 W2 末完成一次性 pooling 后锁定，检索器调优在此之后进行；
- `L2-adv-dev` 与 `L2-adv-test` 在 W3 一起构造；test 版本在 Verifier prompt/阈值调优前锁定，dev 版本供 W4–W5 开发与故障定位。

L2-adv 之所以放在 W3：它不参与检索指标，且必须先有可导航的条款库，才能人工找到主题接近但适用条件不同的干扰条款。时间隔离针对的是 Verifier 调优：test 子集必须在任何基于模型输出的 prompt、阈值或代码调整前冻结，而不是虚构作者对自己手工构造的案例“未知”。

pooling 补漏另有一层约束：它需要 BM25-only 与 dense-only 两路可用，因此只能在 W2 检索器可跑之后、测试集锁定之前的窗口内完成。这与上述原则不冲突——此处使用的是未调优的基线检索器，且其输出只作为候选提交人工裁决（见 §8.2.3）。这是 W2 唯一允许执行 locked query 的情形：不得计算质量指标、运行混合/端到端链路或据输出调参；pooling query 顺序、基线配置、候选 hash 和人工裁决日志必须封存。

报告中给出 `content_origin`、`label_origin`、Gold event、完整来源链和 retrieval-originated item 分布；检索来源 Recall 仅作 diagnostic，并说明单标注者、无标注者间一致性的限制。

### 8.3 答案评分器与 judge 校准

L1 关键点覆盖率这类指标无法用字符串匹配算出来，需要一个自动评分器，而这个评分器本身是 LLM 判定。如果不验证它，答案类指标就建立在一个未经检验的裁判上——这恰好是 §4.3 对 Verifier 提出的要求：模型判定必须单独评测，不能宣称为绝对正确。评分器不应享受比 Verifier 更宽松的标准。

本节描述自动评分默认路线。若 W0 go/no-go 最终选择“人工评分、不调用云端/本地 judge”，则跳过 judge prompt 校准与 kappa，不得伪造一个评分器结果；开发集和锁定默认链的答案指标直接来自预注册人工标签，核心对照的答案类结果只有在额外人工审计后才能作为 headline。报告必须明确实际采用哪条路线。

需要区分两类指标，它们的算法完全不同，不要混为一谈：

- **需要评分器的**：L1 关键点覆盖率、L2 rationale 是否成立等语义判断；
- **不需要评分器的**：claim-evidence 支持准确率。它衡量的是 Verifier 的语义支持判定对不对，算法是拿 Verifier 的输出与人工 gold 直接比对，中间不该再插一个 LLM。若用评分器去评 Verifier，就是用一个未经检验的模型判定去检验另一个模型判定，等于什么都没验。

#### 8.3.1 评分器设计

- 输入：问题、系统最终答案、gold 关键采分点列表，以及完成判定所需的最小 gold evidence excerpts；不得把整份规范、整章或无关条款发送给评分器；
- 输出：每个采分点命中与否（二元）、未命中原因，以及逐个结构化 answer claim 的 supported/contradicted/insufficient 判定与严重错误标志；
- 判定逐采分点和 answer claim 进行，不直接对整段答案给一个不可解释的整体分数；task success 由 §8.4 的确定性聚合规则计算；
- 评分器 prompt 纳入 §9.5 的版本化管理，hash 写入评测记录；
- **只喂最终答案，不喂系统的中间轨迹、检索结果或 rationale**。

最后一条容易被忽略但影响很大：如果把 Agent 的推理过程一并交给 judge，judge 会倾向于接受一条自洽但错误的推理链，评分会系统性偏高。

#### 8.3.2 开发校准与锁定审计

一致性验证分两阶段，不再把同一开发集上“调到一致”后的数字当作最终可信度证据：

1. **开发校准。** W3 使用全部 L1 dev 输出调整逐采分点 judge；W4 在最小 L2 主链能产生代表性输出后，再用全部 L2 dev 完成 L2 校准。每次修改都保留旧 prompt 与旧数值；dev kappa 只是工程准入信号，不是未见样本表现。
2. **锁定审计。** W5 冻结 judge prompt、模型、key-point schema、answer-claim 三分类/严重错误 schema 与审计总体后，W6 对默认生产链首次运行的全部 25 个 L1-test 和 12 个 L2-test 输出做人工判定；人工判定时不查看 judge 结果。审计同时覆盖两套独立标签：逐采分点 hit/miss，以及每个结构化 answer claim 的 supported/contradicted/insufficient 与严重错误标志。两套标签分别报告一致率、Cohen's kappa、混淆矩阵和标签数，不混合计算。两组核心对照的重复运行不混入这个总体；若其答案类分数被写成 headline，则须预先登记并另报该处理组的人工审计。结果只用于解释本次报告的评分可信度，不得再反向修改 judge 或主系统。

任一标签族的 dev kappa 低于 0.6 时，对应答案指标不进入 W4 简历材料；W6 中某一标签族低于 0.6 时，只将该族 judge 结果标为不可靠，并改用其人工标签与原始计数。task success 同时依赖两族标签；answer-claim 审计不达标时，不能用 key-point kappa 为 unsupported/contradiction 或 task success 放行。两阶段都披露样本/标签数和小样本局限，不宣称“评分高度可靠”。

#### 8.3.3 评分器与 Verifier 必须分离

两者都是 LLM 判定，用途却相反，不得共用实现：

- **Verifier** 在线上运行，决定一条结论是否放行，是系统的一部分；
- **评分器** 在离线运行，决定一次运行得几分，是尺子的一部分。

共用实现会让 Verifier 的错误对自己隐形——它用同一套判断给自己打分。这一点在 §8.5.3 的 Verifier gate-only 对照中尤其关键：若评分器与 Verifier 同源，开关 Verifier 时评分标准也随之改变，对照就无法解释。因此两者必须是独立的 prompt 与独立的调用路径。

**评分器换用不同模型：`glm-5.2`（经 ChatAnywhere API 路由）。** 主链跑 DeepSeek V4 Flash，评分器不复用同一个 model slug，以降低最直接的自评偏好。仅换模型不能证明误差独立，换 prompt 也不能消除相关偏差，因此 §8.3.2 的锁定人工审计仍是最终兜底。

选 `glm-5.2` 的目的是避免最直接的“同一 model slug 给自己评分”；它与 DeepSeek 出自不同的模型厂商，但这只是降低相关性，不是对训练数据或误差独立性的证明。另需记明：judge 经 ChatAnywhere 路由，而该平台的模型清单同时包含 DeepSeek 型号，两条链路因此共用同一中间方；换模型并未证明厂商层面的独立性。两条注意：

- **评分器不必比主模型更强，但不能明显更弱。** 逐采分点判命中是相对简单的判定任务，不需要顶配模型；但若评分器能力低于主链，噪声会直接进入所有答案类指标。§8.3.2 的 kappa 就是这条的兜底——kappa 达标即说明它够用。
- **评分器模型 ID 与主模型、embedding 模型一并写入评测报告**（§9.5）。换评分器等同于换尺子，必须视为新的评测版本，不与旧结果混排。

### 8.4 核心指标

#### 检索

- `Macro-Recall@5 = (1/N) Σ_i |G_i ∩ R_i@5| / |G_i|`，其中 N 只包含至少一个 gold 条款的可回答题；
- `Hit@5 = (1/N) Σ_i 1[G_i ∩ R_i@5 ≠ ∅]`，用来区分“命中任一 gold”和“覆盖多条 gold”；对多条款题额外报告 `all-required-hit@5`；
- 按问题-条款词面重叠度分层的 Macro-Recall@5 与 Hit@5（仅可回答题）；
- `MRR = (1/N) Σ_i 1/rank_i`，仅在可回答题上计算；
- 不可回答题的检索误触发率：超过冻结置信阈值并进入确定性回答链路的比例；
- 跨条款引用扩展命中率：分母为人工标注“必须沿引用才能找齐 gold”的题，分子为扩展后命中所有必需引用条款的题。

所有检索指标同时给出逐题原始值、分子/分母和题数；小样本下的百分比只是描述性结果。

nDCG@10 不纳入首发指标：单标注者的二元相关性标签支撑不了分级增益的计算，报出来也无法解释。

#### 答案与规范一致性

- L1 先按题计算 `KPRecall_i = 命中的 gold 采分点数 / gold 采分点总数`，再报告题级 `Macro-KPRecall = (1/N) Σ_i KPRecall_i`；
- L1/L2 的结构化答案另把每个事实或规范性陈述拆成 answer claim；报告 `unsupported_answer_claim_rate` 和 `gold_contradiction_rate`，并给出“至少一个严重错误”的题数。关键点命中不能抵消额外编造或与 gold 冲突的陈述，报告中的 task success 必须同时满足预注册的采分点条件且无严重错误；
- L2 在 claim 级计分；首发每个评测案例只有一个 claim，报告 verdict 混淆矩阵、各类 precision/recall/F1 与各类 claim 数；macro-F1 可一并报出，但不作为门槛（见 8.6）；
- 不可回答案例以“应拒答”为正类计算 refusal precision/recall，并同时给出 TP/FP/FN/TN；
- 有依据却拒答率；
- 无依据却给出确定结论率。

#### 引用

- 引文存在率；
- 文档版本与条款范围匹配率；
- claim-evidence 支持判定的混淆矩阵、各类 precision/recall/F1 与 accuracy（Verifier 输出与 §8.1 人工 gold 直接比对，不经评分器）；
- 每条确定性结论的证据覆盖率。

#### L2-adv 对抗子集（单独报告）

- 端到端误确认率：应判为 insufficient_evidence 却输出 compliant 或 violating 的比例；
- Verifier 直喂 matched pairs 上的混淆矩阵、对干扰证据的拦截 recall，以及对正确证据的误拒率；
- 误引干扰条款的比例。

这三个数字是“为什么引文存在不能证明结论成立”这一问题唯一能拿出实测证据的地方，不与 L2 主集指标合并计算。

#### Agent

- 必需工具召回率；
- 允许工具集合 precision；
- 参数 schema 合法率；
- 禁止或无效工具调用率；
- 计划完成率；
- 错误恢复成功率（仅在 §8.1 的故障注入模式下计算，不与正常模式合并）；
- 平均步骤数和重规划率。

工具调用锁定子集只有 8 题，上述每个比率以 12.5% 为步进。**与检索区块同样处理：只给逐题原始值、分子/分母和题数，不设阈值、不作显著性或泛化陈述。** Function Calling 是 §2.1 与 §14 的头部能力，但它在本项目的证据形态是完整可复核的调用轨迹，而不是这 8 题上的百分比。

#### 工程

- 单题成本；
- P50/P95 延迟；
- 工具超时率；
- 请求失败率；
- 预算超限率。

官方成本/延迟协议中，“冷缓存”指强制绕过本地 LLM response cache，不清空或重建已冻结的 Qdrant/BM25 索引。每类任务先做一次不计入结果的预热，测量并发度为 1，从 API 收到请求计时到最终结果/拒答完成；query embedding、工具调用、provider 网络、重试与降级均计入，离线 judge 成本/延迟单独报告。处理条件按题交错运行以降低时段偏差；L1/L2 分开给出 N、原始延迟、P50/P95 与失败数，小 N 的 P95 只作描述。

### 8.5 有限对照：两组锁定测试，最多三组开发诊断

首发不再对所有消融都作锁定测试结论。W6 只运行两组直接支撑核心叙事的锁定对照；其余各限时 2 小时，只在开发集用于工程选择，简历和报告必须标为 dev diagnostic。它们不是发布阻断项；W5 核心交付未完成时整组转 backlog，不用赶工或碰 locked 集。

#### 8.5.1 冻结的混合检索协议

所有处理条件共用同一套基础协议：本地 dense top-20 与 BM25 top-20；按 `(corpus_manifest_id, document_id, clause_id, child_span)` 去重；用 RRF（`k=60`）融合；同分时按 document_id、数值化 clause path、child start 稳定排序；最终本地返回 top-5。这些参数先记录在 W2 的不可变基线 corpus_manifest；若 W3–W5 的开发集实验改变任何索引或融合参数，必须创建 successor corpus_manifest，W5 选定的 `run_spec` 再绑定确切的最终 ID，不修改旧 manifest。§8.5.4 的三组开发诊断一律保持相同 query、chunk、filter、稳定排序、最终 top-5、生成模型和预算，只改各自要考察的那一个因素。

#### 8.5.2 核心对照 A：512-token excerpt 的窗口选择策略

> **[已失效｜已替换]** 本组对照的前提是语料中存在超过 512 tokens 的条款。RFC 语料实测不成立：唯一超过 512 tokens 的单元是两份文档的收集式 ABNF 附录，现已按 anchor 排除出索引，`corpus qa` 的 `excerpt_fit` 线在 1571/1571 与 351/351 上全数通过 —— 也就是说**没有任何可检索单元超过 512 tokens**，p50 约 50，最长单元 357。W-head 与 W-query 在 25 道 L1 锁定题上会输出逐 bit 相同的结果，下方要求的“超过 512 tokens 的条款”分层是空集。这不是“效应量小、需在报告中直说”，是本组在当前语料上没有可测量对象。
>
> **替换为「核心对照 A′：Evidence 预算分配」。** 原设计的框架不变——把“为了数据最小化付出多少代价”从一个必须突破自身约束才能测的问题，变成一个在约束内可优化的工程问题——换的只是可优化的变量。RFC clause 是段落级的，截断不再是瓶颈；瓶颈是配额闲置：L1 上限为 5 个 excerpt / 2560 tokens，而 top-5 命中按中位数只用掉约 250 tokens，九成配额没有用出去。两条臂：
>
> - **E-narrow**：命中 clause 各自单独出站（当前默认行为）；
> - **E-context**：每个命中 clause 连带其所在 section 内的相邻 clause 一并出站，按 section 顺序扩展，直到触及 5 个 excerpt 或 2560 tokens 中先到的一个。
>
> 两条臂同样严格遵守单片段 512 tokens / 8 KiB、运行级 5 个 / 2560 tokens 与全部 transmitted 上限，**闸门无需任何改动**，也不越出 §3.2 第 3 项已留档的合规包络——被判断的行为仍是“为处理一次查询发送一份公开标准的若干条款片段”，总量上限一字未改。主链模型固定为云端 `deepseek-v4-flash`；检索、排序、预算与评分规则全部冻结，唯一变量是同一批命中如何填满同一个配额。
>
> 分层要求保留，只换分层键：按“该题 gold clause 所在 section 内是否存在相邻 clause”分层，只在 E-context 实际扩展过的子集上解读差异；两条臂输出恒等的题必须计数并在报告中列出。W5 在 dev 上确认脚本与分层统计，W6 在 L1 锁定测试集上对每个条件做 3 次配对端到端运行，报告 Macro-KPRecall、unsupported/contradiction claim 原始数、成本与 P50/P95。
>
> **适用范围**：§12.3、§13 风险表、§14 覆盖矩阵与 §15.1 简历规则中所有“excerpt 窗口选择 / W-head / W-query”的表述，一律按本附注替换后的 A′ 理解。下方正文保留为设计过程记录；其中对 E-1024 的否决理由仍然成立，并同样适用于 A′。

**先纠正上一版的设计错误。** 上一版把 E-1024 列为处理条件，与本方案自身架构直接冲突：§3.2 的出站闸门把单片段硬限制在 512 tokens，§5.1 的 `get_clause` 契约同样声明 512，§8.6 把“单片段超限 fail closed”列为硬发布阻断项。E-1024 会被闸门直接拒绝，除非专门开一条提升上限的代码路径——而“闸门不可绕过”正是本项目最核心的卖点，为一组消融开这个口子得不偿失。更根本的是，§3.2 第 3 项的合规判断本身就**建立在 512 这个上限之上**并已留档；在 1024 下运行等于跑到已评估包络之外，需要重做评估。

**真正该测的问题在 512 之内。** 本方案有一处未指定的空白：当条款正文超过 512 tokens 时，**送出去的是哪 512 tokens？** §4.1 只规定了索引侧的 parent-child 切分（7168 tokens 阈值），出站侧的 excerpt 选取策略全文未定义。补上它既是设计缺口的修复，也构成这组对照：

- **W-head**：取条款开头 512 tokens（朴素基线）；
- **W-query**：在条款内按句子/段落粒度对 query 打分，选取得分最高的连续 512-token 窗口，并保留所在小节标题作为定位前缀。

两条臂都严格遵守 512 上限与全部出站契约，**闸门无需任何改动**，也不越出已评估的合规包络。主链模型固定为云端 `deepseek-v4-flash`；检索、排序、Evidence 条数、预算与评分规则全部冻结。

前置产物保留：§4.1 已用 BGE-M3 tokenizer 逐条款算过长度，报出 p50/p90/p99 与**超过 512 tokens 的条款占比**。这个比例直接决定本组的效应量上界——若绝大多数条款短于 512，两条臂在多数题上完全等价，必须在报告中直说，并按“该题 gold 条款是否超过 512 tokens”分层，只在超限子集上解读差异。

W5 在 dev 上确认脚本与分层统计；W6 在 L1 锁定测试集上对每个条件做 3 次配对端到端运行。报告 Macro-KPRecall、unsupported/contradiction claim 原始数、成本与 P50/P95，全部按上述分层给出。

这组的价值在于换了问法：把“为了数据最小化付出多少代价”从一个**必须突破自身约束才能测**的问题，变成一个**在约束内可以优化**的工程问题。两种结果都有用——W-query 明显更好，就是一个可直接上线的真实改进；两者接近，则说明 512 的截断损失本来就小，数据最小化的代价确实低。

#### 8.5.3 核心对照 B：Verifier gate-only

> **[报告方式修正]** 本组 off 臂在 `L2-adv-test` 上的结果近乎由构造决定：该子集正是按“引文存在、版本正确、语义不支持”构造的，确定性检查必然全过，off 因而必然输出确定结论。真正被测量的只有 on 臂的语义判定准确率 —— 而这个量 §8.1.1 的直喂 matched pair 已经在测，区别仅在证据来源（系统真实检索 vs 手工挑选的干扰条款）。
>
> 本组的边际信息因此小于原文读起来的分量，它是“证据来自真实检索链时的端到端误确认数”，不是一个独立的新发现。报告与 §15.1 中，本组结果必须与直喂结果**合并为一条叙事** —— 同一项语义判定在手工干扰证据下、系统检索证据下、端到端三种口径的表现 —— 不作为三个并列卖点分列。实验设计本身不变。

要隔离 Verifier 的放行价值，不能同时改变重检索。先用冻结的默认上游链路生成并保存 pre-Verifier candidate artifact（claim、proposed_verdict、Evidence IDs/hashes 与 rationale），然后对同一 artifact 运行：

- **off**：先运行与 on 完全相同的引文存在、manifest/content-hash、适用范围确定性检查；通过后不执行语义支持闸门，直接保留候选 verdict；
- **on**：运行同一组确定性检查，再执行 Verifier 语义支持判定，失败时改为 insufficient_evidence。

确定性检查失败的 artifact 不进入语义 gate-only 配对，并单独报告排除数量与原因。两组都不重检索，因而只切换最终语义放行判定。主测为 10 个 `L2-adv-test`，12 个 L2 主集 test 用来观察正常案例的误拒。W6 对每个上游候选生成做 3 次配对运行；每次生成后的 on/off 共用同一 artifact hash。报告端到端误确认数、误拒数、matched-pair 混淆矩阵与原始判定。

生产默认链路仍保留“Verifier 失败后定向重检索一次”。它的端到端结果单独命名为 **Verifier + retrieval-recovery package** 的系统效果，不归因于 Verifier 语义判定单项。

#### 8.5.4 只在开发集的诊断（最多三组，各限时 2 小时）

1. dense-only vs 冻结混合检索；
2. L2 单决策 Agent vs Evidence/Compliance 职责分离；
3. H-flat vs H-tree（`toc_router` 已实现，只是不占锁定名额——见 §8.5.2）。

职责分离诊断的两种编排必须共用相同模型、corpus/index、硬 token/成本/工具上限、外部 Verifier、出站政策和评分规则。若无法保持同一预算，只报成本-效果取舍，不将差异归因于“多 Agent”。上述三组开发诊断一律不运行锁定测试集，不产生泛化性结论；如果职责分离无开发集收益，默认使用更简单的方案。

两组核心对照即使做 3 次复跑，样本仍很小；结果只作描述性工程证据，不使用“统计显著”或强因果措辞。独立样本单位始终是 case/case group：`L2-adv-test` 仍为 `n=10 groups`，三次只是 within-case repeats，matched 正负项也不是 20 个独立样本。逐 run 报原始计数，再报每题三次一致性，绝不把重复运行扩写成更大的 N。面试中对“为什么不直接塞长上下文”的回答仍基于 §3.2 数据边界；长上下文基线只能在另行授权与独立协议下作为发布后研究。

### 8.6 硬发布阻断项与非阻断质量目标

**硬发布阻断项**：

- 100% API 响应通过 schema 校验；
- 100% 确定性结论带 corpus_manifest_id、文档版本、条款 ID 和 content hash；
- 正式测试中无“Verifier 已判失败但仍输出确定结论”的路径；
- 任何未授权 corpus/provider route、出站超限或字段越权都 fail closed，且多轮/重试闸门测试通过；
- 出站账本原子 reservation、并发/恢复测试通过；账本不可用时没有 provider 请求发出；
- 路径穿越、压缩炸弹、加密成员、宏/主动内容和外部关系 fixture 均不能进入正式语料目录；
- 两份规范均通过 §4.1 解析 QA，冻结 collection 的 schema/point count/inventory root 校验一致；
- CI、demo profile、fixture-init 与 Verifier 确定性拦截路径全部通过。

**非阻断质量目标**（未达仍可发布，但必须在报告首页披露）：

- 可回答锁定测试题的 Macro-Recall@5 目标不低于 0.85；同时报告 Hit@5、all-required-hit@5、词面重叠度分层结果、原始计数和小样本限制；
- L1 同时报 Macro-KPRecall、unsupported/contradiction answer-claim 原始计数和含严重错误题数；覆盖率不得遮蔽额外错误；
- 不可回答案例的 refusal recall **不设小数阈值**，只报 TP/FP/FN/TN 原始计数与 L1/L2 分列结果。原因与 §8.6 砍掉 L2 macro-F1 门槛的推理一致：L2 test 的 insufficient_evidence 仅 4 例，可取值只有 0 / 0.25 / 0.5 / 0.75 / 1.0；与 L1 的 5 例不可回答题合并也只有 9 例，最近的两档是 0.778 与 0.889。**旧稿写的 0.80 在两种算法下都取不到**，它实际表达的是“必须全对”，却读起来像一个统计门槛。工程期望仍是尽可能全部拒答，但那属于失败分析而不是发布阈值；
- L2 不设 macro-F1 门槛；必须给出 12 个 claim 的完整混淆矩阵、各类 precision/recall/F1、各类数量和原始判定；
- `L2-adv-test` n=10 不设阈值；完整报告端到端误确认数、matched-pair 混淆矩阵、干扰证据拦截 recall 与正确证据误拒率；
- **可复现性按双轨制分别成立**：fixture 冒烟测试任何人 clone 后可得到确定性结果；真实语料评测则公开下载脚本和 §6.4 三层 manifest 所列的非原文复现元数据、逐案例非原文结果与聚合脚本。第三方可以复跑相同协议，但云端模型别名、provider 路由与生成随机性可能导致数字波动，因此不承诺逐 bit 或逐题完全一致；报告预先给出容差、原始计数与限制；
- 所有未达目标项在报告中保留，不修改 gold 迁就输出。

---

## 九、可观测性、API 与部署

### 9.1 API

- POST /chat：创建 L1/L2 请求；real profile 要求已有会话 bearer，localhost demo 可在页面初始化时签发短期、仅限 fixture 的会话凭据；
- GET /chat/{run_id}/events：SSE 执行事件，使用 Authorization header/cookie 携带创建该 run 的会话凭据，不把 token 放入 URL；
- GET /runs/{run_id}：返回脱敏执行轨迹，必须做 run ownership/会话归属校验；
- GET /health：只返回服务和依赖状态，不回显 provider、路径、secret 或 manifest 内容。

评测**不设 HTTP 端点**，只提供 CLI。原方案的 POST /eval/runs 与 GET /eval/runs/{id} 已移除：评测是离线批处理，不存在需要远程触发的使用场景，把它做成端点只是增加攻击面和维护成本，换取一点“接口看起来更完整”的观感。CLI 同样满足“一条命令复跑评测”的门槛，且更容易接入 CI。

### 9.2 运行轨迹

每次运行记录：

- request_id、session_id、run_id、corpus_manifest_id、resolved_egress_policy_id、run_spec_id（评测）与封存后关联的 evaluation_run_manifest_id、配置版本、prompt 版本与 hash、模型 ID 与采样参数；
- 计划及每步 Agent；
- 工具名称、参数摘要、耗时和错误；
- token、成本、重试和降级；
- 检索候选的 ID/分数摘要、最终 Evidence IDs/hashes 与出站账本；
- Verifier 每项检查；
- 最终状态与拒答原因。

普通日志不保存 query/L2 设计、excerpt、完整候选或完整规范原文。LangGraph checkpoint 只持久化计划、opaque Evidence IDs/hashes、预算和 reservation IDs，禁止序列化完整条款对象；活跃 checkpoint 在最后访问 7 天后清理，完成态立即压缩为脱敏 run 元数据。LLM response cache 与可选调试轨迹只落在本地受控卷，默认 TTL 分别为 7 天与 30 天；三者都支持按 run/session 删除、最小权限和敏感场景下的本地静态加密。API key 只通过 secret 或环境注入，不进入配置文件、轨迹、错误或报告。

### 9.3 前端

React 只做一个最小 trace 页，不建设完整 SPA：

1. 对话和任务输入；
2. 执行轨迹，包括 Agent、工具、耗时、成本和错误恢复；
3. 在真实语料本地模式中展示本地条款对照；公开/demo 模式只展示 fixture 或已放行 excerpt，不向远程浏览器返回真实完整条款。

不建设复杂后台、用户系统、权限系统或可视化工作流编辑器。

### 9.4 部署与 CI

API 对外部模型和工具调用使用异步 I/O、超时与并发信号量，避免单个慢请求耗尽服务资源。客户端提交的 max_steps、max_tool_calls、max_tokens 与 max_cost 只是请求上限，服务端还必须施加不可突破的硬上限。公开环境强制 fixture/demo profile；真实语料 profile 默认只绑定 localhost/私有网络，且配置速率限制与访问凭据。

Docker Compose 包含：

- api；
- **mcp**（独立容器，使用服务间认证；只在 Compose internal network 暴露，不映射宿主机端口）；
- web；
- qdrant；
- postgres；
- `fixture-init` / `real-init`（按 profile 启动的一次性幂等服务）。

qdrant 与 postgres 同样只在 internal network 可达，不映射宿主机端口；真实语料卷以只读方式挂载给需要读取的服务。对外只暴露 web/api，调用方不能绕过 API 反复直接调用 get_clause。

Compose 明确分两个 profile：

- `demo`：fixture + fake model + `fixture-init`，无需 API key；
- `real`：本地真实语料卷 + 已授权 provider 或已实测本地模型，默认不对公网开放。

`fixture-init` 校验 fixture manifest，幂等导入预计算 dense/BM25 表示与元数据。`real-init` 由 `make ingest-real CORPUS_DIR=/absolute/path` 调用，在隔离摄取流程后构建或恢复版本化只读 collection，复核 corpus manifest、schema、point count 与 inventory root，并写入 manifest-scoped ready marker；重复执行不得修改已冻结 collection。随后才运行 `docker compose --profile real up --build`。api/mcp 必须等待对应 init 成功、ready marker 匹配及 qdrant/postgres health check，不能对空库提供“健康”服务。

CI 至少运行：

- ruff；
- mypy；
- pytest；
- fixture 语料上的端到端管线冒烟测试（检索、MCP 分发、预算、异常恢复、Verifier 确定性检查全部真跑，模型走 fake）；
- Docker 镜像构建检查。

> **[已补齐]** 上表漏了两类**服务依赖型**证据，而 §8.6 把它们列为硬发布阻断项：PostgreSQL 出站账本的原子预占/并发/恢复，以及冻结 Qdrant collection 的 schema、point count 与只读校验。前者 CI 一直在跑；后者此前**在 CI 里静默 skip** —— `ledger` job 只起了 postgres，`SPECPILOT_TEST_QDRANT_URL` 未设，8 个 collection 测试因此每次都跳过，而绿灯看起来毫无异样。现已按 compose 同一 tag（`qdrant/qdrant:v1.12.4`）加了 service，并显式跑 `make integration-qdrant` —— 该 target 在变量未设时**硬失败而不是跳过**，所以服务容器坏掉会让 job 红，而不是悄悄变绿。本地对同版本真实服务实测：设两个变量后 `tests/integration` 为 34 passed / 0 skipped（此前 26 passed / 8 skipped）。跑真实服务不违反“CI 不做模型推理、不下载权重”——Qdrant 不是模型，向量是测试内合成的确定性单位向量。

**CI 不做任何模型推理，也不下载任何模型权重。** fixture 向量已预先提交（§4.6.2），LLM 走确定性 fake（§9.6）。这既保证测试可重复，也让 CI 时长与网络状况脱钩。

**CI 的评测输出不得包含任何形似质量指标的数字**——它的通过标准是“没崩、schema 合法、拦截路径被触发过”，理由见 §8.0。

### 9.5 配置与 prompt 版本化

§8.2.4 与 §11 承诺“不用测试集调 prompt”。这个承诺要可信，就必须有机制而不只是自律：

- 所有 prompt 以独立文件形式入库，不内嵌在 Python 字符串里；
- 每次运行记录 prompt 文件的内容 hash，写入运行轨迹与评测结果；
- 评测报告标注本次使用的 prompt、配置、索引版本，以及主模型、评分器模型（若有）与 embedding 模型的实际 ID/权重 hash；人工评分则绑定人工协议与标签文件 hash。任一变化都会改变结果，漏记都会让数字失去可追溯性；
- 任一项变化都视为新的评测版本，不与旧结果混合比较。

这样“某个数字是在哪一版 prompt 下跑出来的”可以被第三方核对，而不是只能相信作者的说法。

### 9.6 离线演示模式

这是一级交付物，不是备用方案。

仓库不含真实规范（§3.2），模型调用需要 API key。默认情况下，招聘方 clone 后的真实体验是跑不起来——那么“可复现、可评测”这两条核心卖点就无法被任何人验证，整个项目的说服力只能依赖对方愿意相信 README。

`demo` 模式的做法是：**换掉语料和模型，其余一律真跑。**

- **语料换成 fixture**：仓库自带的合成小型规范，含条款编号、交叉引用、shall/should 措辞和术语表，结构上足以驱动全部五个 MCP 工具。其向量已预先算好提交，因此 demo 不需要加载 BGE-M3。
- **模型换成确定性 fake model**：它实现与真实 provider adapter 相同的接口，对四个预注册 fixture 场景返回固定 tool_use 序列与文本；未注册问题明确返回 `unsupported_demo_case`，不伪装成能泛化的 Agent。切换到真实模型通过更换 adapter/profile 完成，不假设不同 provider 共用 key。
- **其余全部真实执行**：混合检索、MCP 工具分发、预算扣减与上限、异常注入与恢复、Verifier 的确定性检查（引文存在、版本匹配）、SSE 轨迹推送。
- 四个预注册场景分别覆盖 L1 正常路径、L2 路径、拒答路径和 Verifier 拦截路径。
- `docker compose --profile demo up --build` 后由 `fixture-init` 完成幂等导入；无需下载真实 RFC 语料、无需付费模型即可运行四个场景并看到轨迹、证据高亮和 Verifier 检查结果。

**这个 demo 证明什么、不证明什么，必须讲清楚**，否则等于换一种方式过度宣称：

| 证明了 | 没有证明 |
|---|---|
| 工具分发、预算约束、异常恢复、SSE 轨迹真实工作 | Agent 选工具选得好——fake model 的调用序列是编排出来的 |
| 检索管线端到端跑通 | 检索质量——fixture 是合成语料，指标见 §8.0 |
| Verifier 的确定性检查（引文存在、版本匹配）真实拦截 | Verifier 的语义支持判定——那是模型调用，此处被桩件替代 |

相比预录整条轨迹，这里除模型之外的每一个组件都在真实执行。观察者可以修改预算上限、重跑预注册场景或手动注入故障；修改为未注册问题时会如实得到 `unsupported_demo_case`。“三分钟”是发布前的暖启目标，不是未测先写的事实；报告分别记录首次构建/冷启与已有镜像暖启时间，达到后才在简历材料中写具体数字。

---

## 十、方案 C：Dify 展示版（发布后 backlog）

### 10.1 定位

Dify 不属于 W0–W6 主工程，也不进入首发验收。主工程发布后若继续扩展，Dify 作为业务配置入口：

- 主工程证明代码、架构、可靠性、评测和部署；
- Dify 证明快速配置业务工作流和平台交付能力；
- 两者共享 MCP/HTTP 工具、模型、索引和评测题。

### 10.2 展示范围

Dify 只实现 L1 条款问答：

1. 接收问题和规范版本；
2. 判断是否需要澄清；
3. 调用检索、条款读取和引用扩展工具；
4. 生成带条款引用的答案；
5. 证据不足时拒答。

L2、多 Agent 重规划、复杂异常恢复和状态管理仍只在主工程中实现。

Dify 首个展示默认只使用 fixture 或本地 LLM。若以后接入真实语料，Dify 容器不得持有可直连外部 provider 的凭据：所有模型请求必须走本地受控 model proxy，由服务器解析 run-scoped egress policy，并与 tool gateway 共用 §3.2/§5.2 的原子累计账本；在该代理、授权和审计链全部通过前，真实语料禁止进入 Dify 工作流。

### 10.3 对照方式

在 1–2 天时间盒内，Dify 只用与 LangGraph 共享的**新建开发对照集**做探索性比较，不复用 W6 锁定测试集，不作泛化结论。若未来要做正式对比，必须在配置 Dify 前另行冻结新 holdout，并让 LangGraph 同期重跑：

| 维度 | LangGraph 主工程 | Dify 展示版 |
|---|---|---|
| 搭建与修改效率 | 编码，控制精细 | 配置快 |
| 编排可控性 | 强 | 受平台节点限制 |
| 错误恢复 | 可定制 | 依赖平台能力 |
| 调试轨迹 | 完整自定义 | 依赖平台可见信息 |
| 部署迁移 | 自主 | 存在平台依赖 |
| 准确率、成本、延迟 | 实测 | 同题实测 |

结论不预设“自研优于平台”，而是总结不同场景下的技术取舍。

### 10.4 启动门槛与时间盒

只有主工程已经发布且满足以下条件后才开始 Dify：

- L1/L2 主链跑通；
- MCP 工具可独立调用；
- Verifier 能阻止无证据确定性回答；
- 基线评测可一条命令运行。

Dify 总投入限制为 1–2 天，单独作为发布后扩展记录，不回填进首发里程碑。交付物包括工作流 DSL、配置说明、截图、短演示视频和对照表。

---

## 十一、7 周排期（W0–W6）


> **[已完成（工程包）并实测｜2026-08-14]** W4 的 L2/Compliance、确定性引文与 manifest/scope 闸门、语义闸门、一次定向 recovery、最小脱敏 checkpoint 与 owner-assisted 进程恢复已在 `FakeProvider` 上完成工程验证；使用一套新建 PostgreSQL 数据库和冻结 Qdrant collection 运行全套测试，结果为 **1978 passed, 0 skipped**。恢复保持同一 evaluation root、绑定与额度；本地可重建阶段不重发，丢失的 provider 结果用下一 reconstruction generation 重传并按既有出站账本累计。此事实只证明实现/服务集成，并不产生 L2 dev 校准数字、真实 provider 验收、SSE/reconnect、完整 demo matrix 或 locked evaluation 结论；这些仍未完成。命令与范围见 `docs/reports/w4-compliance-verifier-recovery.md`。

> **[已失效｜排期模型重写]** 本节的周次排期已与实际执行脱节，且脱节方向与它的设计假设**相反**。实测：2026-08-06 至 08-08 三个日历日内完成 W0、R0、W1 与 W2 的 Task 1–5，共 88 次提交；计划给这些工作的是三周。同期标注完成 3 条。**工程侧不是瓶颈；标注是瓶颈，而且是唯一的。**
>
> 由此本节三处必须改读：
>
> 1. **改依赖排期，不按周次。** 真实关键路径只有一条：`gold 标注 → 一次性 pooling → 冻结 corpus_manifest → 锁定评测`。下方 W3–W5 的工程（MCP、Orchestrator/Evidence、Compliance/Verifier、SSE 与最小 trace 页、demo/real profile、CI 与离线 demo）**都不依赖 gold**，只有跑评测才依赖，因此可与标注并行推进，不再串在其后。周表保留为工作项与验收清单，不再作为时间承诺。
>
> 2. **两份裁剪顺序合并，且以后者为准。** 下方主裁剪顺序（L2-adv-dev 6→3 → 工具子集 16→8 → L1 dev 15→10）砍的是**证据**，换取的是已实测为非稀缺的工程时间；本节末尾的“非核心裁剪顺序”（三组 dev diagnostics 转 backlog → 前端只保留静态 trace/SSE 页 → PostgreSQL 只保留最小 schema）砍的是**工程**。两者方向相反。执行时**先走后者并尽量走完，前者只在标注本身被证明无法完成时才触发** —— 在工程近乎免费的前提下，用证据换工期是净亏。
>
> 3. **标注吞吐已有实测，但不足以给全项目写完工日期。** L1 choice pass 的有效 19 条中位数为 22 秒/条；5 条独立 deep review 共 13m41s，中位数 91 秒、最短 34 秒。这证明当前 L1 工作流的 choice 复核不是小时级瓶颈，也证明 deep review 不能用 choice 耗时替代。L2 标注、W4–W5 工程与 W6 评测仍无完整实测吞吐，因此不把 L1 的 22 秒外推为整体完工日期。
>
> 下方原文保留为排期推理过程；其中“三件重活不能压在同一周”的判断在当时成立，并且正是它促成了 W0 的析出。

**排期为 7 周（W0–W6）。** 早期版本把三件独立的重活压在同一周：§3.2 的安全解压与 OOXML 沙箱解析（含 §12.2 要求的恶意 fixture 测试套件）、§4.6.1 的可审计闸门测试 transport、以及全部 dev 标注加一半 locked。前两项各自都是以天计的工程，第三项是全程最大的单项工作量，三者叠在一周里不成立。因此把安全与闸门整体析出为 W0，W1 专做标注。

**标注是本项目最大的单项工作量，也是唯一不能靠加班或换工具压缩的部分。** 前几版反复把它在周次之间搬移，看起来解决了，实际只是换个地方超载。这里不写逐项工时估算：开工前的单题耗时都是猜测，相乘得到的合计只会制造精度错觉，与本方案在别处（§8.6 的阈值、§9.6 的“三分钟”）坚持的标准自相矛盾。取而代之的是可验证的检查点和预先登记的裁剪顺序。

周次分工：W0 不做标注；W1 的工程量因安全与闸门整体前移而变轻，是**标注主周**；W2 工程最重（双规范解析 QA、Qdrant、dense/BM25/RRF、pooling 管线），标注让位；W3 集中构造 L2-adv。

**检查点与裁剪顺序（预先登记，不到时候临时压质量）。W2 结束时若 L1/L2 主集 gold 与 pooling 尚未锁定**，按以下顺序裁剪，每步执行后重新判断：

1. L2-adv-dev 由 6 组降为 3 组；`L2-adv-test` 的 10 组是核心对照 B 的主测集，**不可裁**；
2. 工具调用子集由 16 题降为 8 题、只保留 dev——该组指标本就只作描述性报告（§8.4）；
3. L1 dev 由 15 题降为 10 题；L1 test 的 25 题供核心对照 A 使用，**不可裁**；
4. 以上仍不够时直接延长到 8 周，把标注单独摊一周，**不压缩单题标注时间、不降低复核标准**。

L2 主集的 12 例 locked（4/4/4）是三类混淆矩阵的最小可用规模，任何情况下不裁。

| 周 | 工作重点 | 周末验收 |
|---|---|---|
| **W0** | **安全与闸门基础周，不做任何标注。** 顺序固定为：① 实现安全解压与 OOXML 沙箱解析及其恶意 fixture 测试套件；② 下载两份规范并冻结**初始 source_manifest**（文档 ID/版本、URL、ZIP/DOCX hash、下载时间）；③ 基于该 manifest 完成 §3.2 四项合规评估并落成 successor 或判定不通过；④ 实现 `EgressPolicyEnforcer`、PostgreSQL 原子账本与可审计测试 transport，并按 §4.6.1 第 2 项做多轮/重试/越权与最大合法包络测试；⑤ 用 fixture 验证两个 provider 路由的实际可用性、tool calling/结构化输出与账户级数据政策（§4.6.1 第 1 项）；⑥ 建 Compose/CI 骨架。②必须早于③——合规判断的对象是具体文档版本，没有 manifest 就无从评估 | 主链与 judge 的真实语料路线已授权，或本地/人工替代路线已实测；闸门多轮/重试/越权/并发/恢复测试通过，最大合法包络不被误拒；路径穿越、压缩炸弹、加密成员、宏与外部关系 fixture 全部只进隔离区；两份规范的初始 source_manifest 已冻结且 hash 可复核；`docker compose --profile demo` 骨架可启动 |
| W1 | **标注主周。** 对一份规范做解析 smoke；按独立路径推进 L1/L2 gold，目标为全部 dev 加约一半 locked | 一份规范可一条命令解析；标注进度可核对（完成题数、来源路径与裁决记录齐全） |
| W2 | 完成两份规范的 parent-child 切分与 §4.1 QA；建 Qdrant 和本地 dense/BM25/RRF 基线，实现 pooling 所需的本地 search/get；建立只读 corpus snapshot；完成剩余 L1/L2 gold、一次性 pooling 与分组拆分 | 两份规范全部入库且 QA 过阻断线；corpus_manifest/collection 不可变；W2 末锁定 L1/L2。locked query 只运行 pooling-only 基线，不算指标、不跑混合/端到端链路，保存候选 hash 与裁决日志 |
| W3 | 把 W0 的出站账本接入真实主链（W0 只在测试 transport 上验证过）；把五个只读能力封装为 MCP；完成 FastAPI L1、单 Agent 基线、Orchestrator、Evidence Agent、预算与基础轨迹；完成 L1 自动 judge 校准或人工评分流程演练；构造 6 个 L2-adv-dev 与 10 个 L2-adv-test | L1 API 返回带 manifest 的引用；账本在真实主链上的原子 reservation/恢复测试通过；五个 MCP 工具可复跑；L1 dev 评分记录完成；L2-adv dev/test 互斥且 test hash 在 Verifier 调优前冻结 |
| W4 | 完成 Compliance Agent、Verifier、retrieval-recovery package、最小 checkpoint/run ownership、拒答与异常恢复；用代表性 L2 dev 输出校准自动 judge 或演练人工协议；只在各 dev 集运行完整评测 | L2 跑通；坏参数、空检索、超时、循环、预算、出站越权和 gate 失败测试通过；L2 dev 评分记录完成；**产出明确标为开发集的首批真实数字** |
| W5 | 完成 SSE 与最小 trace 页、demo/real profiles、fixture-init/real-init、CI 与离线 demo；时间允许时完成 dev diagnostics（dense-only、H-flat/H-tree、职责分离）；在 dev 对两组核心对照 dry run；冻结默认链路、评分路线/prompt、阈值与 evaluation `run_spec`。**[工程状态｜2026-08-15]** SSE、四场景、初始化、profile 暴露边界、wheel/五镜像与零跳过硬 gate 已完成；作者冻结与 dev diagnostics 仍开放。 | demo 已幂等运行四个预注册场景，fresh 服务树 2199 passed/0 skipped，浏览器 5/5；real-init 已构建并由集成测试覆盖。核心对照 dev dry run、作者选择的 `run_spec`/评分路线冻结仍须单独完成；locked 输出保持 W6 首次运行边界。 |
| W6 | 首次运行 L1/L2 主锁定测试与 L2-adv-test；两组核心对照各做 3 次配对复跑；封存逐次 evaluation_run_manifest 与最终 report manifest；完成自动 judge 的人工审计或纯人工主评分、冷缓存成本/延迟、README、报告、视频和简历材料 | 测试结果未反向调参；主指标与核心对照给出原始计数；每个数字可追溯至不可变 report/run manifest；所选评分路线完成；公开仓库、版本化报告、视频和发布说明齐全 |

Dify 展示不占用 W0–W6 任何周次，统一放在首发发布后的 backlog。周期内提前完成某项工作形成的机动时间，只用于补标注、失败分析、自动化测试或报告，不得临时扩张范围。

**W5 与 W6 的分工**：W5 只看开发集，用于修实现、比较方案并冻结最终配置；W6 才首次读取锁定测试集。W6 的测试结果无论支持还是反对开发集选择，都只进入报告，不再修改 prompt、阈值、路由、工具或 gold。若测试集与开发集结论相反，如实报告分歧，并把任何后续改动放入新的评测版本与新测试集。

该 7 周方案假设单人持续投入且 W0 go/no-go 通过。**是否延期不看工时统计，只看两个可验证的事实**：W0 结束时 §4.6.1 三项硬依赖是否落地，W2 结束时语料 QA 与主集锁定是否完成。任一未达成即延长到 9–10 周，不用压缩标注与测试伪装按期完成。非核心裁剪顺序为：三组 dev diagnostics 整体转 backlog → 前端只保留静态 trace/SSE 页 → PostgreSQL 只保留出站账本、run ownership 与 checkpoint 的最小 schema。不裁掉出站审计、gold 隔离、离线 demo、Verifier 失败闸门、锁定主测试和两组核心对照。

### 11.1 W4 中期节点

> **[触发条件改写]** 本节把中期节点锚在“W4 末约在 9 月上旬，正好卡在正式批开闸前后”。按实测工程速度，W4 的工程内容是数日内的事，这个时间锚已经断了。节点要解决的问题不变（正式批投递期内简历上要有这个项目），但触发条件改为**依赖达成**而不是周次：L1 端到端通过 API 且带版本化条款引用、MCP 工具自主选择的完整调用轨迹、Verifier 能阻止无证据的确定性输出、开发集的检索与拒答指标已出数 —— 四项齐备即可产出中期材料，不必等到任何“第几周”。下方“可写入简历的最小集合”与“必须逐项标注为开发集结果并写明样本量”的要求全部不变；答案类指标仍须等 judge 的 dev 一致性达标或人工评分完成。

W4 末必须产出一份可用于简历的真实评测结果，这是排期中的硬性节点，不是顺带产物。

原因要说准确：本方案启动于 8 月初，加上 W0 后 7 周完成落在 9 月下旬。**提前批（7–8 月）无论如何都赶不上，中期节点救不了它**——旧稿把理由挂在提前批上是错的。它真正服务的是**正式批（9–10 月）**：W4 末约在 9 月上旬，正好卡在正式批开闸前后。而 §15.1 规定项目完成前不写假设数字，没有中期节点的话，简历在整个正式批投递期都不会出现这个项目。

W4 末可写入简历的最小集合：

- L1 端到端通过 API 完成，带版本化条款引用；
- MCP 工具自主选择与完整调用轨迹；
- Verifier 能阻止无证据的确定性输出；
- 开发集上的检索指标与拒答指标，必须明确写“开发集 n=...，项目进行中，W6 更新”，不得写成测试集或泛化结果；
- 答案类指标只有在自动 judge 的 dev 一致性达标，或预注册人工评分与复核完成后才可写入；否则本轮只写检索与拒答指标。

W5–W6 的成果作为增量更新，而不是简历上这个项目的首次出现。

排期原则：

- W2 末、W3 开发前完成并锁定 L1/L2 评测尺子；初始 gold 发现不得使用系统检索，唯一例外是按 §8.2.3 将冻结基线结果作为一次性补漏候选，最终裁决仍由人工对照原文完成；
- 测试集锁定与 pooling 补漏必须在检索器调优之前完成；
- 每周必须有可运行交付物；
- Dify 已固定在发布后 backlog；主线未过门槛时继续砍中文语料和 L3，不砍评测、异常处理与离线演示模式；
- 不为追求漂亮数字修改 gold 或隐藏失败案例。

---

## 十二、验收清单

### 12.1 功能

- 可摄取冻结版本的两份规范；
- L1 和 L2 均可通过 API 完成；
- Agent 通过 MCP 自主选择只读工具；
- 每项确定性结论带 corpus manifest、版本、条款、content hash 和证据；
- 证据不足时能够拒答；
- 离线演示模式可在无凭据条件下跑通四个预注册工程路径；不把它等同于真实模型能力或真实语料质量验证；

### 12.2 工程

> **[已变更｜当前验收状态]** RFC 路线的安全验收取代下方 ZIP/OOXML 恶意 fixture 条目：已覆盖非 regular file、symlink/缺失 `O_NOFOLLOW` 能力、文件过大或读中增长、hash 不匹配、非法 UTF-8、DTD/entity/external reference、非 XML processing instruction、错误 root/grammar、缺失/非法/重复 publication identity 及 manifest identity mismatch；同一 verified snapshot 贯穿 corpus、structure、retrieval 与 annotation source validation。W0–W3 已完成统一 provider 出站闸门、冻结索引与启动校验、MCP/API/Agent schema、owner-bound 脱敏轨迹、本地 fixture browser 闭环和不依赖付费模型的 CI。未完成的主要条目是 L2/Compliance、checkpoint 恢复、SSE/reconnect、完整四场景 demo/real init、LLM 响应缓存与冷缓存测量；下方清单仍是最终验收标准，不因部分完成而删除。

- `make ingest-real CORPUS_DIR=/absolute/path` 可幂等构建/恢复并校验真实只读索引，随后 `docker compose --profile real up --build` 在本地/私网启动；`docker compose --profile demo up --build` 可单命令启动无凭据演示；
- **无任何 API key、无需下载真实 RFC 语料即可运行四个预注册离线演示场景**，且除模型外的组件全部真实执行（§9.6）；
- fixture-init/real-init 幂等，demo/real 两个 profile 的 manifest-scoped ready/health check 可复跑；MCP、Qdrant 与 PostgreSQL 不暴露宿主机端口；
- provider 调用全经统一出站闸门；未授权、字段越权、单片段/累计超限和重试绕过测试全部 fail closed；
- 出站账本在 PostgreSQL 中原子预占并可随 checkpoint 恢复；并发、进程重启、同条款多 span、跨 provider 重发均不能绕过 unique/transmitted 上限；逐文档 corpus 账本随同一把 corpus 锁持久化，未被计价的 document 一律 fail closed（`corpus_document_cap_missing`）；
- 安全摄取测试覆盖路径穿越、符号链接、解压大小/数量上限、加密成员、宏/主动内容与外部关系，违规输入只进入隔离区；
- 两份规范均通过 §4.1 的解析 QA 阻断线；冻结 Qdrant collection 只读，schema/point count/inventory root 启动校验通过；
- API、工具和 Agent 边界均有 schema；
- 有健康检查、超时、重试、预算和循环上限；
- prompt 以文件入库，运行轨迹记录其 hash、模型 ID 与配置版本；
- embedding 与 LLM 响应缓存键满足 §4.6 的版本隔离要求；正式成本与延迟使用冷缓存测量；
- cache、checkpoint 与调试轨迹只落本地受控卷，TTL、按 session 删除、最小权限与可选静态加密经过测试；checkpoint 不含完整条款，secret 不进入日志与报告；
- 关键路径有单元测试、集成测试和固定 fixture；
- CI 不依赖实时付费模型即可运行；
- 运行轨迹可查询，错误不会被吞掉。

### 12.3 评测与材料

- 40 道 L1、20 个单原子 claim 的 L2 案例、16 个 L2-adv 案例组（6 dev/10 locked；直喂评测共 32 个正负 claim-evidence 项）完成版本化；
- L2 的 expected_verdict 与 Verifier supports_verdict 分离；主集/对抗集的 clause/scenario family 跨 split 去重报告通过；
- gold 来源分布、pooling-only run hash、人工裁决日志与 pooled 占比在报告中披露；
- 若启用自动评分器，dev 校准历史与 W6 锁定人工审计的一致率、Cohen's kappa、混淆矩阵和标签数均披露；若采用纯人工路线，则披露人工协议、标签文件 hash 与复核范围，并明确不存在 judge/kappa；
- excerpt 窗口选择与 Verifier gate-only 两组核心对照在锁定集各做 3 次配对复跑；dense/hybrid、H-flat/H-tree 与单 Agent/职责分离若在时间盒内完成，只作 dev diagnostics；
- **报告中的全部指标数字来自真实语料；CI 的 fixture 评测不产出任何质量指标**（§8.0）；
- 报告明确区分锁定测试结果与已完成的 dev diagnostics；未完成项列入 backlog，不把职责分离的开发集差异写成泛化或因果结论；
- evaluation `run_spec`、逐次 evaluation_run_manifest 与最终 evaluation_report_manifest 共同包含 §6.4 要求的处理条件、配对/artifact、代码、依赖、评测集、脚本、provider 政策、逐案例结果和实际运行环境元数据；
- 报告有数据处理附录，列明 query/设计描述、派生 claim、版本元数据、有界 TOC 标题/路径、Evidence/gold excerpt 在各阶段的 provider、保留/训练政策、区域/子处理方、出站上限与处理链；出站上限须**逐层列全**：单片段、stage、run、evaluation-case 根、corpus，以及 **corpus 的逐文档层**（每份文档的 excerpt/token/byte 上限、其实测分母，以及“上限设在五分之一以下”这一推理与它对应解掉的 source-terms uncertainty）；
- 报告同时包含成功、失败、成本和限制；
- README 提供架构、快速开始、评测方法和数据许可说明；
- 有架构决策记录、评测报告和 2–3 分钟演示视频；Dify DSL 与平台对照表仅在发布后扩展实际完成时追加；
- 简历只写真实跑出的数字。

---

## 十三、风险与对策

| 风险 | 对策 |
|---|---|
| 范围再次膨胀 | L1/L2、MCP、Verifier、评测优先；Dify 之后的扩展全部进入 backlog |
| 通信知识不足导致标注错误 | 每题绑定原条款；对高风险案例做二次复核；披露单标注者限制 |
| 3GPP 内容处理与再分发问题 | 仓库不放真实原文、引文或完整索引；source_manifest 授权默认 false，通过时新建 successor 而非改旧对象；评估不通过则 fail closed，并在 W0 按 §4.6.1 的 A/B/C 表选定路线或延期 |
| top-k 承诺被多轮工具或重试绕过 | 按 content+quote+span 生成 disclosure_id；PostgreSQL 原子账本同时限制 unique/transmitted tokens/bytes 和 provider route；get_clause 全文、完整 TOC、错误堆栈不出站；并发/恢复/恶意请求均测试 |
| 用户 query/L2 设计描述本身敏感 | 在出站字段和报告中显式列出；支持脱敏与本地 LLM 回退；未授权 route fail closed |
| 规范版本或索引混用 | 三层 manifest 不可变；Qdrant 使用只读版本化 snapshot 并校验 inventory root；Evidence 带 corpus_manifest_id/content hash，Verifier 对跨 manifest 证据 fail closed |
| “多 Agent”只是角色改名 | 固定预算做 dev diagnostic；不用锁定测试包装这个次要叙事，无开发集增益则默认使用简单方案 |
| 引文存在但不支持结论 | 将引文存在、版本匹配、语义支持拆成三个独立指标；L2-adv 对抗子集单独评测 Verifier |
| gold 标注被自身检索器污染 | 初始 gold 只走独立路径；pooling 只将冻结基线输出作为一次性候选，人工裁决并披露偏差 |
| CI 数字被误读为质量指标 | §8.0 划死两套语料的界限；CI 输出禁止出现形似准确率的数字 |
| Dify 占用主线时间或绕开出站闸门 | Dify 是发布后 backlog；默认 fixture/local LLM，真实语料只经本地 model proxy 与 run-scoped 原子账本 |
| 锁定测试集被用于选择配置 | W1–W5 只看开发集；W6 首次运行冻结测试协议，结果不得反向调参 |
| API 成本失控 | 固定预算、小模型路由、embedding 与 LLM 响应双层缓存、记录单题成本（§4.6） |
| 标注量压垮 W1–W3 | W1 为标注主周、W2 让位于工程、W3 专做 L2-adv；W2 末主集未锁定即触发 §11 预登记裁剪顺序，仍不够则延长到 8 周单独摊一周标注 |
| real profile 启动空索引 | 先由 real-init 构建/恢复并校验 manifest-scoped ready marker，API/MCP 不对空库报健康 |
| 招聘方 clone 后跑不起来 | fixture 语料 + fake model 的离线模式为一级交付物，除模型外全部真跑（§9.6） |
| 秋招投递期内简历上没有该项目 | W4 设中期节点，产出首批可写入的真实数字（§11.1） |
| 评测数字无法追溯 | run_spec 预注册条件/配对；run manifest 绑定逐案例输出与 artifact；report manifest 绑定人工标签、聚合结果和报告（§6.4、§9.5） |
| 被质疑“为何不直接用长上下文” | 明确说明 top-k 数据最小化承诺与整份规范传输冲突；首发在 512 上限内以 W-head/W-query 对照实测截断损失的可优化空间，不为消融突破自身出站契约；长上下文仅在另行授权后研究（§8.5） |
| 答案类指标建在调优后的 judge 拟合值上 | 自动路线在 dev 校准后冻结，W6 对锁定输出做人盲审；低于 0.6 时以人工结果为主。纯人工路线不报告虚构的 judge/kappa（§8.3） |
| 本地编码拖慢迭代 | embedding 按文本 hash 缓存；fixture 向量预先提交，CI 与 demo 不跑推理（§4.6.2） |
| 消融为了做对比而突破出站契约 | 核心对照 A 全部在 512 上限内进行；任何需要提升单片段上限的实验都要求重做 §3.2 合规评估，首发不做 |
| Verifier on/off 被重检索混杂 | gate-only 对照冻结同一 pre-Verifier artifact，两组都不重检索；完整 Verifier+重检索只作系统 package 结果 |
| 第三方 LLM 服务中断或限流 | LLM 响应缓存；embedding 本地不受影响；离线演示与 CI 完全不依赖在线服务 |

---

## 十四、需求覆盖矩阵

| 招聘需求 | 实现证据 | 取舍 |
|---|---|---|
| 多 Agent 协作 | Orchestrator、Evidence、Compliance；时间允许时补 L2 开发集诊断 | 只在 L2 做职责分离，不用锁定测试凑次要叙事 |
| Function Calling | MCP 工具发现与完整调用轨迹 | 首发工具全部只读，无语义重叠 |
| 记忆管理 | 会话 checkpoint 与版本化偏好；有意不做长期事实记忆并给出理由 | 版本迭代会让缓存结论静默失效，宁可不做 |
| 任务规划 | 四步上限、预算、失败重规划一次 | 不做开放式无限规划 |
| Agent 注册/发现 | 类型化静态组件配置；MCP 负责工具发现 | 不做动态 Agent Registry、独立注册中心与灰度系统 |
| 长文档 RAG | parent-child 条款切分、冻结 dense+BM25+RRF、本地 TOC router、引用扩展 | 完整 TOC 不出站；锁定测试比较 512 内的 excerpt 窗口选择策略 |
| 知识库与结构化 | 术语、规范性等级、交叉引用、版本 manifest | 解析失败内容隔离 |
| 工作流平台 | 发布后可选的 Dify L1 展示 | 不进入首发验收；1–2 天探索只用新建 dev 集，正式比较须预先冻结新 holdout |
| API、数据库、容器 | FastAPI、PostgreSQL、Qdrant、Docker Compose | 不上 Kubernetes |
| 前后端交付 | 最小 React trace/SSE 页 | 不做完整 SPA、复杂后台或工作流编辑器 |
| 效果测试与提示词优化 | 锁定主测试、gold 隔离、judge 锁定审计、两组核心对照与最多三组限时 dev diagnostics | 不用测试集调 prompt；次要消融不占用锁定测试且可按时间盒延期 |
| 稳定性与成本 | 超时、重试、循环/预算上限、分级模型路由、双层缓存、成本和 P95 | 所有失败可见 |
| 交付与可复现 | 离线演示模式，无 key 可完整验证预注册工程链路 | 不验证真实模型能力或真实语料质量；语料不入库 |
| MCP 项目经验 | 主工程经 Streamable HTTP 调用独立 `mcp` 容器；同一工具实现另可经只读 HTTP gateway 复用 | 工具实现、传输协议与编排解耦 |
| 数据治理与合规工程 | 统一出站闸门、原子预算账本、留档式合规自评与逐阶段处理链披露（§3.2、§5.2） | 以可验证的架构约束和可复核的判断记录，替代“我们会注意”的承诺 |

---

## 十五、简历与面试产出

### 15.1 简历写作规则

项目完成前不写假设数字。完成后只从版本化评测报告中提取：

- 语料规模和规范版本；
- Macro-Recall@5、Hit@5、all-required-hit@5 与拒答指标，均需注明公式、测试集规模和原始计数；
- L1 Macro-KPRecall、unsupported/contradiction claim 原始数与含严重错误题数；不以高覆盖率掩盖额外错误；
- L2 结果按 12 个单原子 claim 写各类 precision/recall/F1 与样本数，不单独把 macro-F1 摘出来当卖点；
- Verifier 前后的无依据确定性输出变化；
- 10 个锁定 L2-adv case groups 上的误确认原始数，matched positive claims 上的误拒原始数与 Verifier 混淆矩阵；三次复跑仍写 n=10，不写成 n=30；
- Verifier gate-only 在冻结候选上的 on/off 结果；若提及重检索，明确写成 Verifier+retrieval-recovery package；
- excerpt 窗口选择对照（W-head vs W-query，均在 512 上限内、主链模型固定）在 3 次配对复跑上的 Macro-KPRecall、unsupported claim 原始数、成本与延迟；附条款长度分布与超过 512 tokens 的条款占比，并按该分层解读；
- L2 单 Agent/职责分离若写入材料，必须标注为 dev diagnostic，不写成锁定测试或因果结论；
- （仅在发布后扩展完成时）Dify 与 LangGraph 在版本化对照集上的工程取舍；
- 出站闸门的多轮/重试/并发/恢复测试结果与最大合法包络测试；单个 excerpt、运行级 unique 与 transmitted 三层上限的实际执行情况；
- 测试数量、CI 状态和部署方式。

每个数字必须先由 evaluation_report_manifest 映射到逐次 evaluation_run_manifest，再追溯到 run_spec、代码/依赖、corpus/index、评测集、聚合脚本、prompt/config、模型/provider、artifact 与逐案例记录。

W4 末的中期节点（§11.1）可以先写入一版，但必须逐项标注为**开发集结果**与样本量，不得使用或暗示锁定测试集表现；W6 完成后替换为最终测试数字。写的仍是真实跑出的结果，只是评测地位不同。

### 15.2 面试重点问题

1. 为什么选择两类任务，而不是做通用聊天机器人？
2. 职责分离诊断是否在两小时 timebox 内完成？若完成，开发集取舍是什么；若延期，为什么不牺牲核心验证来赶它？
3. MCP 工具参数错误、超时和循环如何处理？
4. 为什么“引文存在”不能证明“结论成立”？对抗子集上的实测数字是多少？
5. 检索指标的 gold 是怎么标的，如何保证没有用检索器自己的结果当答案？
6. 答案是谁打的分？这个裁判本身验证过吗，为什么评分器不能复用 Verifier？
7. 出站闸门为什么要区分 unique 与 transmitted 两个账本？重试、改发另一个 provider、同一条款切成多个 span 分别如何计数？账本写失败时系统怎么办？
8. 这些文档不算特别长，为什么不直接交给长上下文模型？
9. 为什么不做长期记忆？这是没做还是不做？
10. 规范版本如何隔离，旧偏好如何失效？
11. 为什么把 Dify 放在发布后而不是塞进主线？（这道题考的是范围判断，你有完整答案；平台取舍的实测对比要等发布后扩展完成才有资格讲）
12. 哪个优化降低了成本，准确率是否同时下降？
13. 哪些发布门槛没有达到，为什么仍然保留失败结果？

---

## 十六、下一步

> **[状态校正｜2026-08-15]** W4 的 L2/Compliance、Verifier、checkpoint recovery 与 W5 的 SSE/reconnect、完整 fixture demo matrix、packaged init 和硬 CI gate 均已完成工程验证。当前自动化证据为 fresh PostgreSQL + Qdrant 2199 passed/0 skipped、浏览器 5/5；它只证明工程链路，不证明回答质量。真实 provider 验收、作者负责的评分路线/prompt/threshold/`run_spec` 冻结和未完成 dev diagnostics 仍开放。W6 locked evaluation 保持首次运行边界，未读取输出，也不得用于反向调参。

1. 用户复核本方案的任务边界、评测规模和 7 周排期；
2. 复核通过后，编写逐文件、逐测试的实施计划；
3. 实施计划确认后再开始 W1，不提前建设非主线模块。
