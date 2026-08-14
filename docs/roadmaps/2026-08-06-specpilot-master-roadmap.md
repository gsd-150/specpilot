# SpecPilot Master Implementation Roadmap

**Canonical product plan:** `../../../SpecPilot_项目方案.md` (v5, 2026-08-06)

**Project root:** `/Users/chunxue/Documents/resume_project/specpilot`

**Delivery rule:** Each week is executed from its own detailed, test-first plan. A later week does not begin until the preceding week’s hard gate is evidenced or the product plan’s pre-registered fallback is selected.

> **[已变更｜2026-08-11]** 上面这条 Delivery rule 已不适用，本文件其余部分按本块理解。
> 原文保留，因为它记录了当初的设计意图，而不是因为它仍然成立。
>
> **1. W3–W5 是可并行的工作包与验收清单，不再是周次硬门禁。** 依据是方案 §11
> 的 `[已失效｜排期模型重写]`：实测三个日历日内完成 W0、R0、W1 与 W2 的
> Task 1–5，共 88 次提交，而计划给这些工作的是三周；**工程侧不是瓶颈，标注是，
> 而且是唯一的**。方案由此规定按依赖排期——唯一的关键路径是
> `gold 标注 → 一次性 pooling → 冻结 corpus_manifest → 锁定评测`——并点名
> W3–W5 的工程（MCP、Orchestrator/Evidence、Compliance/Verifier、**SSE 与最小
> trace 页**、demo/real profile、CI 与离线 demo）**都不依赖 gold**，可与标注并行。
> 上面那句「A later week does not begin until…」按字面执行恰好禁止这个并行，
> 所以它作废而不是被重新解释。
>
> **2. 当前 W3 slice：** MCP、Orchestrator/Evidence、FastAPI、基础 trace 数据，
> 外加**只读 trace 页**。页面通过 `GET /runs/{run_id}` 首次加载，可选短轮询。
> 实现沿用方案 §9 的最小 React 页，不引入构建-free 的静态 HTML/JS 方案。
>
> **3. SSE、断线重连与 SSE 专属凭据传递仍留在 W5。** 闭环不需要 SSE：页面按 `run_id`
> 取一次轨迹即可闭合，短轮询已足够看到执行过程。SSE 的成本集中在
> `GET /chat/{run_id}/events` 的凭据传递（Authorization header/cookie，token 不
> 入 URL）、重连语义和异步流测试，这些都不增加闭环的可演示性。
>
> **4. 只读页面提前的理由，以及它反转了什么。** 这个项目的差异化在披露账本、
> 引用校验和失败轨迹，而**三者没有界面就是不可见的**。方案的非核心裁剪顺序把
> 「前端只保留静态 trace/SSE 页」列为第 2 顺位被砍项；把只读页提前是对该顺序的
> **有意反转**，记录在此而非静默执行。反转成立的前提是同一条 `[已失效]` 记录的
> 事实：工程近乎免费，用它换可见性不占用标注时间。
>
> **5. 最小会话与 run ownership 随 W3 一起落地，这不是提前。** 方案 §9 把
> ownership 绑在端点上而不是绑在周次上：`GET /runs/{run_id}` 一行原文即
> 「返回脱敏执行轨迹，**必须做 run ownership/会话归属校验**」。下方 W4 条目里的
> 「最小 checkpoint/run ownership」是本路线图与方案走样的地方，按方案读。
> 边界：`POST /chat` 用短期 bearer/cookie 标识会话（real profile 要求已有会话
> bearer，localhost demo 可在页面初始化时签发短期、仅限 fixture 的凭据）；trace
> 只能由创建该 run 的会话读取；不建用户系统、账号库或复杂权限；trace 不保存
> query、excerpt 或候选正文，只保存脱敏 ID、hash、计数与事件（这一条不是新增
> 约束，是 §9 既有的 committable-field 规则）；demo/测试用确定性签发器，真实
> profile 从环境或 secret provider 注入签名密钥，密钥不进入普通配置。
>
> **6. 运行生命周期：`POST /chat` 采用异步创建语义。** 校验请求、manifest、
> 预算与会话后持久化 run，返回 `202 Accepted` 与 `run_id`，后台执行 L1
> Orchestrator/Evidence/answer 链；`GET /runs/{run_id}` 返回状态与脱敏轨迹；
> React 页轮询至终态即停。进程恢复与完整 checkpoint 仍留 W4。
>
> **状态机有三条必须先定死，否则 demo 会反噬本项目的主张：**
>
> - **`failed` 由 `provider_error` 判定，绝不由 verdict 判定。**
>   `answer/run.py` 目前把 `ProviderError` 映射为
>   `REFUSED` + `EVIDENCE_INSUFFICIENT`，provider code 另存于
>   `AnswerOutcome.provider_error`。若读模型走 verdict，一次网络超时会渲染成
>   「证据不足，拒答」——正是本项目要让人信任的那句话。走 `provider_error`。
> - **闸门拒绝是独立状态，不并入 `failed` 也不并入 `evidence_insufficient`。**
>   reservation 在链路内部提交，因此 `root_unique_excerpts_exceeded`、
>   `excerpt_bytes_exceeded`、`policy_snapshot_mismatch` 都在 202 之后异步到达。
>   没有东西坏（不是 `failed`），模型也从未看到证据（不是 `evidence_insufficient`）。
>   它是全系统最值得演示的状态：披露上限真的挡住了一次发送。给它独立 reason。
> - **`interrupted` 必须有可达的产生路径。** 进程死亡时没有代码能在死亡瞬间写它，
>   所以它必须由**读时推导**（`running` 且心跳/租约过期）保证立即可见，再由启动
>   扫描持久化遗留记录，而不是依赖工作进程退出前的一次赋值。否则
>   run 永远停在 `running`、页面永远轮询。轮询同时需要客户端上限。
>   W3 的语义是：服务重启后未完成 run 明确标为 `interrupted`，**不静默重跑
>   provider**——重跑会另开一次 reservation 并再次计价，而账本的规则是预留一经
>   提交就已花费。
>
> 本块不改变任何 cap、契约或已记录的合规决定。

## Current state — 2026-08-13

The bounded W3 MCP/API/read-only trace slice was merged into `main` by PR
[#1](https://github.com/gsd-150/specpilot/pull/1) at merge commit `96b13eb`.
Both the PR-triggered and push-triggered GitHub Actions runs passed all seven
jobs, including PostgreSQL and Qdrant integration, the local fake-provider
browser flow, packaging, and Docker image builds. A later independent local gate
on a fresh PostgreSQL database with Qdrant available recorded 1,856 passed with
zero skips; this is dated release evidence and must be rerun after later code
changes rather than treated as permanently current.

This status is deliberately narrower than declaring every later milestone
complete: L2/Compliance, SSE/reconnect, checkpoint recovery, the complete W5
demo matrix, locked evaluation, and the remaining annotation floors are still
open. Current annotation state is L1 20/40 and L2 3/20; L1's registered 20-item
pooling audit and five-item deep review are sealed, while L2 adjudication and
the remaining gold are not. A second batch of 20 L1 locked proposals has been
drafted and verified, including the three additional unanswerable candidates
needed to meet the locked floor, but the author-owned review has not happened,
so the formal store correctly remains at 20/40. The 2026-08-07 section below
remains a historical snapshot rather than the live delivery state.

## Current state — 2026-08-07

W0 and R0 are complete on `feat/w0-foundation`. All ten W0 tasks are done except
Task 8 Step 4's box, which the plan marks as the author's own and deliberately
leaves unchecked; Task 10 recorded its route decision.

**The recorded route decision is `C` — the corpus moves to IETF RFCs.** Both
chosen 3GPP sources are refused by the ingestion boundary with
`embedded_active_content`, and three further specifications measured the same
way, so the blocker is ingestion rather than compliance.

**R0 has since carried out that decision.** RFC 9110 and 9112 are frozen in
both renditions, both pass the same ingestion boundary that refused all five
DOCX distributions, and both hold default-deny `source-manifest/v2` records
alongside an author-written BCP 78 source-terms assessment. Sections and
cross-references now come out of the v3 XML as elements — 288 sections and
2,519 cross-references for RFC 9110, none dangling.

The archive and OOXML boundary is retained unchanged, limits included. It is
the evidence that produced route `C`, and deleting it would erase a
demonstrated capability. The 3GPP manifests and their source-terms assessment
stay as records of what was assessed.

W1 may now begin against the RFC corpus. No successor manifest exists, every
source manifest is default-deny, and no real provider has been called.

Verification as of this date: 376 tests pass with a local PostgreSQL DSN set
(24 of them are skipped without one), Ruff and mypy are clean, and the envelope
and both fixture route smokes pass. A fixture route smoke proves the transport,
enforcer, and ledger are wired and policy-bound; it proves nothing about any
real provider, credential, or model, and its own output says so.

## Milestone sequence

1. **W0 — Safety, manifests, and egress enforcement**
   - Safe outer-ZIP handling and isolated OOXML inspection.
   - Immutable source manifests and compliance-decision successors.
   - Atomic PostgreSQL egress ledger and the only provider transport.
   - Fixture-only provider smoke tests and Compose/CI skeleton.
   - Go/no-go evidence for route A, B, or C.
   - Detailed plan: `../superpowers/plans/2026-08-06-w0-safety-egress-foundation.md`.

2. **R0 — RFC corpus foundation** *(inserted by route `C`; complete)*
   - `source-manifest/v2` for sources with no archive and no DOCX.
   - RFC XML verification boundary, refusing DTDs, entities, and external
     references in two independent layers.
   - Sections and cross-references extracted from v3 XML as elements.
   - RFC 9110 and 9112 frozen, plus a BCP 78 source-terms assessment.
   - Detailed plan: `../superpowers/plans/2026-08-07-r0-rfc-corpus-foundation.md`.

3. **W1 — Annotation workflow and embedding throughput**
   - R0 already parses both frozen sources through the safe boundary and
     produces sections and cross-references, so W1 starts from structure rather
     than from a parsing smoke.
   - Model clauses on top of extracted sections; decide what a citable unit is
     when the source numbers sections but the outbound caps count tokens.
   - Build provenance-audited L1/L2 annotation schemas and review logs.
   - Measure local embedding throughput without committing model weights.
     Measured: the whole corpus (1907 clauses after the ABNF exclusion) encodes in 40–100 s on this
     machine. Grouping batches by token count matters; batch size does not —
     `../reports/w1-embedding-throughput.md`.
   - Detailed plan: `../superpowers/plans/2026-08-07-w1-annotation-and-embedding.md`.

4. **W2 — Frozen corpus and retrieval baseline**
   - One XML parser rather than two DOCX parsers: section/tree/table/reference
     QA and parent-child chunks over the v3 vocabulary.
   - Build versioned Qdrant dense data plus independent BM25 and RRF.
   - Run pooling-only baseline before locking the main evaluation splits.
   - Freeze a read-only `corpus_manifest` and verify its inventory root.
   - **Complete.** Manifest
     `1abafff704358c2357ead5b837d212f130cadfa330dfa30d1df0a24f76d74295`
     seals 1,922 points in `specpilot_ff4841e2d846388014efa06870fbbdb7`;
     snapshot checksum
     `a84fb3ac7352c0f73a56978cb4945ea6ec54bae5528504d6581d005cb72ea1c0`
     and inventory root
     `70bed824fc70871c49a1d350afa6d7e1fabc37c5a17f170d5db66c0b0cdfb19c`
     replay and verify without creating another snapshot.

5. **W3 — MCP, L1 agent, API, and real-ledger integration**
   - Expose the five read-only capabilities through Streamable HTTP MCP.
   - Implement the typed Orchestrator/Evidence flow, budgets, traces, and L1 API.
   - Connect every real provider call to the W0 ledger/enforcer.
   - Freeze mutually exclusive L2-adv dev/test cases before Verifier tuning.
   - **[已变更｜2026-08-11]** 本条的 "traces" 指 Orchestrator/Evidence 的轨迹
     **数据**。slice 现另含**只读 trace 页**（`GET /runs/{run_id}` 首次加载 +
     可选短轮询，最小 React，无 SSE）、最小会话与 run ownership、以及
     `POST /chat` 的 `202 Accepted` 异步创建语义。状态机的三条硬约束见顶部
     修订块——`failed` 走 `provider_error` 而非 verdict、闸门拒绝独立成状态、
     `interrupted` 读时推导。
   - **[Completed 2026-08-13.]** The bounded W3 slice above is on `main` via
     merge commit `96b13eb`. The original W3 row also asked for L1 scoring and
     frozen L2-adv cases; those evaluation-data deliverables were not smuggled
     into the bounded slice and remain open with the annotation/evaluation path.

6. **W4 — L2, Verifier, and recovery package**
   - Add Compliance Agent, deterministic citation/manifest checks, semantic gate, and one directed recovery.
   - Add run ownership and the minimum checkpoint state.
   - Exercise only development sets and publish explicitly labelled dev evidence.
   - **[已变更｜2026-08-11]** 上一条的 **run ownership 移入 W3**：方案 §9 把它
     绑在端点上而非周次上——`GET /runs/{run_id}` 原文即「必须做 run ownership/
     会话归属校验」——所以只要该端点存在，ownership 就必须同时存在，否则会先
     形成一个可枚举 `run_id` 的无鉴权接口。本周保留的是**最小 checkpoint 与
     进程恢复**；W3 遇到重启只标 `interrupted`，不恢复、不重跑。
   - **[工程包已完成并实测｜2026-08-14]** Fixture `FakeProvider`、新建
     PostgreSQL 数据库和冻结 Qdrant collection 的全套验证为 **1990 passed,
     0 skipped**。它覆盖 Compliance、确定性/语义闸门、一次定向恢复、owner
     assisted resume、generation 计费和脱敏 checkpoint/trace；精确命令与边界在
     `docs/reports/w4-compliance-verifier-recovery.md`。这不是 L2 dev 校准、真实
     provider 验收或 locked evaluation 的完成声明；这些评测交付物仍开放。

7. **W5 — Demo, trace UI, and evaluation freeze**
   - Complete four deterministic fixture scenarios, SSE, and the minimal React trace page.
   - Make fixture-init and real-init idempotent and manifest-scoped.
   - Dry-run the two core comparisons on dev, then freeze the final `run_spec`.
   - **[已变更｜2026-08-11]** 最小 React trace 页的**只读部分已移入 W3**。本周
     保留的是 SSE（`GET /chat/{run_id}/events`）、断线重连与 SSE 专属凭据传递，即在
     一个已经存在的页面上做升级，而不是从零建页面。四个 fixture 场景、
     demo/real init 与评测冻结不变。

8. **W6 — Locked evaluation and release evidence**
   - First-run the locked L1/L2 and L2-adv test sets.
   - Run the two paired core comparisons three times without treating repeats as extra independent samples.
   - Seal run/report manifests, manual audit, cold-cache cost/latency, README, report, video, and resume evidence.

## Non-negotiable global constraints

- **[新增｜2026-08-14] 同家族自生成评测偏差必须在报告正文写明，不能只靠字段披露。**
  `label_origin: mixed` 记录了这个事实，但读者不该被要求自己从字段推断结论。W6
  报告须原样表述：20 条 L2 中有 17 条的场景、Gold/标签候选由与被评测系统同模型
  家族的模型提出，之后经人工逐条对照冻结 RFC 源文评审；因此这些结果存在同家族
  自生成评测偏差，不作为无偏性能估计。L1 的 40 条同样由模型提出后经人工评审，
  同一表述按其实际条数一并适用。
- Real source text, full indexes, complete clauses, and quotations are never
  committed, whatever the corpus. The terms behind this rule changed with route
  `C` — 3GPP reserves rights by default while the IETF Trust pre-grants a public
  licence to reproduce unmodified portions with attribution — but the practice
  did not. The RFC source-terms assessment records as an open uncertainty
  whether sending an excerpt to a third-party API is one of the acts §3.c.iii
  licenses, and a rule is not relaxed on the strength of an unresolved question.
- CI/demo fixtures never emit quality metrics; all reported quality numbers come from the frozen real corpus.
- No provider route is callable outside `EgressPolicyEnforcer` plus the atomic ledger.
- Test data remains locked according to the product plan; W6 results never feed back into the frozen configuration.
- Dify and L3 remain post-release backlog.
