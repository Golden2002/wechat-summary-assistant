# OpenAI-compatible API research — 9 Chinese providers
**Research date: 2026-09-17.** Method: `web_search` + `web_fetch` only, preferring official docs/model pages.
**Rule applied:** if a model ID / URL / claim could not be read on an official page, it is marked `verified=false` or "could not verify". No IDs were invented.

Goal context: a desktop tool calls `POST {base_url}/chat/completions` with a `model` string, discovers models via `GET {base_url}/models`, and summarizes **long Chinese chat logs** — so it wants **>200K context, low cost, fast, good Chinese**.

## Summary matrix

| # | Provider | base_url (verified?) | `GET /models`? | Endpoint/deployment id? | >200K option? |
|---|----------|----------------------|----------------|-------------------------|---------------|
| 1 | ByteDance Volcengine Ark | `https://ark.cn-beijing.volces.com/api/v3` ✅ (first-party SDK) | ❌ No — list op is AK/SK-signed POST OpenAPI | ❌ **No `ep-` needed** — but MUST use full versioned id `<name>-<date>` | ✅ `doubao-seed-2-1-pro-260915` 1024K, `doubao-seed-2-0-lite-260428` 1024K |
| 2 | SiliconFlow 硅基流动 | `https://api.siliconflow.cn/v1` ✅ | ✅ Yes (Bearer required) | ❌ No | ✅ 1M (`DeepSeek-V4-Flash`), 256K Qwen3.5 |
| 3 | Tencent 混元 → **TokenHub** | `https://tokenhub.tencentcloudmaas.com` (+intl/us) ✅ | ✅ Yes (Bearer) | ❌ No | ✅ `hy3` 256K, `hy4-preview` 1M |
| 4 | iFlytek Spark 讯飞星火 | `https://spark-api-open.xf-yun.com/v1/` ✅ (+ `/v2/`, `/x2/`, `/agent/v1/`) | ❌ No list API found | ❌ No | ⚠️ `spark-x` X2-Flash 256K |
| 5 | StepFun 阶跃星辰 | `https://api.stepfun.com/v1` ✅ | ✅ Yes (documented) | ❌ No | ✅ 256K |
| 6 | 01.AI Yi 零一万物 | `https://api.lingyiwanwu.com/v1` ⚠️ secondary source | ❓ could not verify | ❌ No | ❌ likely none |
| 7 | Baichuan 百川智能 | ⚠️ could not verify | ❓ could not verify | ❓ | ❌ max 128K, newest are 32K |
| 8 | Baidu Qianfan v2 百度千帆 | `https://qianfan.baidubce.com/v2` ⚠️ secondary; official docs JS-rendered | ❓ could not verify | ❌ No | ✅ `deepseek-v4-pro` 1M (on-platform) |
| 9 | SenseNova 商汤日日新 | `https://api.sensenova.cn/compatible-mode/v2` ✅ | ⚠️ non-standard `GET /v1/llm/models` | ❌ No | ❌ 128K max |

**Best fits for the stated job (long Chinese, cheap, fast): `hy3` (Tencent TokenHub), `deepseek-ai/DeepSeek-V4-Flash` (SiliconFlow), `step-3.5-flash` (StepFun).** Avoid Baichuan, SenseNova and 01.AI for this use case.

---

## 1. ByteDance Volcengine Ark / 豆包 (Doubao)

**Status: ANSWERED — but via first-party ByteDance code artifacts, not the docs site.** Every `volcengine.com` / `docs.volcengine.com` / `ark.volcengine.com` page is JavaScript-rendered and returns an empty body to `web_fetch` (HTTP 200, no text), and the doc API `docs-api.cn-beijing.volces.com/api/v1/doc/{search,fetch}` is POST-only. **No official docs-page quote was obtainable.** The findings below are instead verified against first-party Volcengine/ByteDance artifacts: `github.com/volcengine/ark-runtime-python`, PyPI `arkruntime` 0.6.0 (2026-09-09), npm `@volcengine/ark-runtime` 1.0.11, `@volcengine/ark` 1.0.4, `@volcengine/ark-plan-api` 0.1.0, and `github.com/volcengine/ark-cli`. That is a stronger class of evidence than a third-party blog, but it is **not a docs page** — read the two caveats in items 3 and 4 carefully.

1. **base_url** — **`https://ark.cn-beijing.volces.com/api/v3` is CURRENT** and is the SDK default (official npm source `src/config.ts`: `defaultBaseURL`). Additional official endpoints:
   - `https://ark.cn-beijing.volces.com/api/coding` (Coding Plan), `/api/plan`, `/api/v3/compatible`
   - BytePlus (international): `https://ark.ap-southeast.bytepluses.com/api/coding`, `https://ark.ap-southeast.bytepluses.com/api/v3/compatible`
2. **Endpoint/deployment ID (`ep-xxxxxxxx`) — ANSWERED: NOT required.** Official `@volcengine/ark-runtime` README shows `client.createChatCompletion({ model: "doubao-seed-2-0-pro-260215" })`. The official `ark-cli` skill states: **`--model` 必须是 `<name>-<primary_version>` 完整形式（或 Endpoint ID `ep-xxx`）** — you may pass either a **full versioned Model ID** *or* an `ep-xxx`.
   - ⚠️ **Critical gotcha:** a *bare family name* without the version suffix (e.g. `doubao-seed-2-0-pro`) triggers `InvalidEndpointOrModel.NotFound` **404**. Always pass the fully versioned ID with its date suffix.
   - "API Key 绑定模型" mode: **could not verify** on any official artifact.
3. **`GET /models` — likely NOT usable; the documented list operation is a different thing.** The official `@volcengine/ark@1.0.4` swagger client defines `ListFoundationModelsCommand` with `metaPath = "/ListFoundationModels/2024-01-01/ark/post/application_json/"` — i.e. an **action-style POST management OpenAPI signed with AK/SK**, *not* an OpenAI `GET /models`. The runtime SDK has **no** `listModels` method. A `GET https://ark.cn-beijing.volces.com/api/v3/models` with a Bearer key is claimed in a RAGFlow issue (#14701) but **could not be verified officially — low confidence. Treat discovery as unsupported and hardcode the versioned IDs.**
4. **Model IDs + context windows** (official artifacts; **no official yuan prices found** — Ark pricing is dynamic, obtainable via `arkcli pricing models` or the console):
   | Model ID | Context | Notes |
   |---|---|---|
   | `doubao-seed-evolving` | 1,024,000 | Best for >200K |
   | `deepseek-v4-pro` / `deepseek-v4-flash` | 1,024,000 | Third-party, hosted on Ark |
   | `glm-5.3` | 1,024,000 | Third-party on Ark |
   | `kimi-k3` | 1,024,000 | Third-party on Ark |
   | `minimax-m3` | 1,024,000 | Third-party on Ark |
   | `doubao-seed-2-1-pro-260628` | 256K in / 256K out | Flagship |
   | `doubao-seed-2-1-turbo-260628` | 256K in / 256K out | Faster sibling |
   | `doubao-seed-2-0-lite-260428` | 256K | **Ark's own curated pick for 长文本理解/文本生成 (long-text understanding / generation)** |
   | `doubao-seed-2-0-pro-260215` | 256K | Ark's curated pick for 复杂推理 (complex reasoning) |
   | `doubao-seed-2-0-mini-260428` | 256K | |
   | `doubao-seed-2-0-code-preview-260215` | — | Code-tuned |
   | `doubao-seed-1-6-251015`, `doubao-seed-1-8-251228` | — | |
   | `doubao-seed-character-251128` | — | Roleplay/character |
   **Recommended default for this tool: `doubao-seed-2-0-lite-260428`** (Ark's own long-text recommendation, 256K) or `doubao-seed-2-1-pro-260628` for quality.
5. **Official URLs** — none readable as pages. Artifact sources: `github.com/volcengine/ark-runtime-python`, PyPI `arkruntime` 0.6.0, npm `@volcengine/ark-runtime` 1.0.11, `@volcengine/ark` 1.0.4, `@volcengine/ark-plan-api` 0.1.0, `github.com/volcengine/ark-cli`. Docs pages attempted and empty: `docs.volcengine.com/docs/82379/{1330310,1544106,2121998}?lang=zh`, `ark.volcengine.com/region:cn-beijing/docs/82379/{1262849,1350667}?lang=zh`.
6. **Deprecations** — the official 模型下线公告 exists at `https://ark.volcengine.com/region:cn-beijing/docs/82379/1350667` but its content is unreadable to a plain fetch. **The parent agent resolved this independently** via Ark's doc JSON API (`https://www.volcengine.com/api/doc/getDocDetail?DocumentID=1330310`), which carries 即将下线 markers in the same official 模型列表 table: `doubao-1-5-lite-32k-250115`, `doubao-1-5-pro-32k-250115`, `doubao-1-5-pro-32k-character-250715`, `doubao-1-5-vision-pro-32k-250115`, `doubao-seed-1-6-250615`, `doubao-seed-1-6-251015`, `doubao-seed-1-6-flash-250615`, `doubao-seed-1-6-flash-250828`, `doubao-seed-1-6-vision-250815`, `doubao-seed-1-8-251228`, `doubao-seed-code-preview-251028`, `glm-4-7-251222`. Additional dated batches appear in a first-party Coze mirror of the same notice (第二批 **2025-05-28 18:00**, 第三批 **8月28日**) with replacements such as `doubao-pro-32k/240828` → `doubao-1-5-pro-32k/250115` and `deepseek-r1-distill-qwen-32b/250120` → `doubao-seed-1-6/250615`. **No explicit retirement dates for the 即将下线 list above were read.**
   The same official doc-JSON source also adds these current text models the artifact search missed: `doubao-seed-2-1-pro-260915` (**1024k ctx / 1024k input / 256k max output** — the strongest Ark option for this tool), `doubao-seed-2-0-lite-260428` and `doubao-seed-2-0-mini-260428` at **1024k** (artifact evidence had suggested 256K — **the official doc wins**), `deepseek-v4-pro-ga-260813`, `glm-5-2-260617`, `glm-5-3-flash-260828`.

---

## 2. SiliconFlow 硅基流动

Researched and cross-checked by a dedicated subagent. **High confidence.**

1. **base_url** — `https://api.siliconflow.cn/v1` (official OpenAI-SDK snippet on <https://docs.siliconflow.cn/docs/userguide/quickstart>). International alternate `https://api.siliconflow.com/v1` still published in the EN docs (<https://docs.siliconflow.com/en/api-reference/chat-completions/chat-completions>). `.cn` is the safe default.
2. **`GET /v1/models` works** — documented at <https://docs.siliconflow.cn/docs/api/models-get> (note: the `/docs/api-reference/...` path 404s). `Authorization: Bearer {API KEY}` is **required**; optional `type` and `sub_type=chat`. Returns standard `{"object":"list","data":[{"id",...}]}`.
3. **Ordered model IDs for long Chinese summarization** (prices ¥ per M tokens; contexts from official pages):
   1. `deepseek-ai/DeepSeek-V4-Flash` — **1M ctx**, ¥3/¥9 (¥1.5/¥4.5 off-peak 02:00–08:00), cache ¥0.3/¥0.15. **Best default.**
   2. `Qwen/Qwen3.5-35B-A3B` — **256K**, ¥0.4/¥3.2 (<128K), ¥1.6/¥12.8 (≥128K). Cheapest >200K option.
   3. `Qwen/Qwen3.5-122B-A10B` — 256K, ¥0.8/¥6.4 / ¥2/¥16. Best quality-per-yuan.
   4. `meituan-longcat/LongCat-2.0` — 1M native, ¥5/¥20, cache ¥0.1.
   5. `zai-org/GLM-5.2` — 1M, ¥8/¥28.
   6. `deepseek-ai/DeepSeek-V4-Pro` — 1M, ¥12/¥24.
   7. `moonshotai/Kimi-K2.7-Code` — 256K, ¥6.5/¥27 (code-tuned, weaker fit).
   Unverified context (IDs/prices verified): `zai-org/GLM-5.3`, `Qwen/Qwen3.8-27B`.
4. **No deployment/endpoint ID** — plain model-name string confirmed at <https://docs.siliconflow.cn/docs/api/chat-completions-post>.
5. **Official URLs** — see the doc links above plus <https://www.siliconflow.cn/pricing> and <https://docs.siliconflow.cn/docs/release-notes/overview>. `cloud.siliconflow.cn/models` is JS-rendered and not scrapeable.
6. **Deprecations with dates** (from the 更新公告): **2026-09-11** removed `nex-agi/Nex-N2-Pro`, `Qwen/Qwen3.5-397B-A17B`, `MiniMaxAI/MiniMax-M2.5`; **2026-06-11** `Pro/moonshotai/Kimi-K2.5`, `Pro/zai-org/GLM-5`, `Pro/zai-org/GLM-4.7`; **2026-05-15** `moonshotai/Kimi-K2-Thinking`, `zai-org/GLM-4.6`, `Qwen/Qwen3-235B-A22B-Instruct-2507`; **2026-04-29** `QwQ-32B`, `DeepSeek-R1-Distill-*`; **2026-04-22** `Qwen/Qwen3-Coder-480B`, `deepseek-ai/DeepSeek-V2.5`; **2026-03-17** `MiniMax-M2.1`, `deepseek-ai/deepseek-vl2`; **2025-12-31** `zai-org/GLM-4.5`, `Qwen/Qwen3-235B-A22B`; **2025-10-09** `deepseek-ai/DeepSeek-V3.1`. API-level: `repetition_penalty` ignored from **2026-09-15**; `/user/info` retired **2026-08-14**. **Do not hardcode any of these.**

---

## 3. Tencent Hunyuan 腾讯混元 → **TokenHub** (major finding)

**The legacy Hunyuan OpenAI endpoint is superseded.** Both <https://cloud.tencent.com/document/product/1729/111007> and <https://cloud.tencent.com/document/product/1729/104753> carry an official notice: Hunyuan features are **migrating to TokenHub**; the old platform "不再新增模型能力，并停止支持新购模型服务" (no new models, no new purchases). Migrate to TokenHub.

1. **base_url** — TokenHub, region-dependent (official table): `https://tokenhub.tencentcloudmaas.com` (广州 / China mainland), `https://tokenhub-intl.tencentcloudmaas.com` (Singapore/global), `https://tokenhub-us.tencentcloudmaas.com` (Silicon Valley); fallback `.tech` TLDs. Usage `{domain}/v1/chat/completions`. Source: <https://www.tencentcloud.com/zh/document/product/1300/78941>. The legacy `https://api.hunyuan.cloud.tencent.com/v1` still exists (verified on <https://cloud.tencent.com/document/product/1729/111007>) but is not a sensible new-integration target.
2. **`GET /v1/models` works** — officially documented, OpenAI-compatible `GET /v1/models`, Bearer key required, 401 without it. Returns `{id, object, name, created, status}` where `status` may be `online` or `pre-offline`.
3. **Ordered model IDs with official context windows** (<https://www.tencentcloud.com/zh/document/product/1300/78934>; prices per M tokens from <https://www.tencentcloud.com/zh/document/product/1300/78937>):
   | model | ctx | max in / max out | price (in/out) |
   |---|---|---|---|
   | `hy3` | 256k | 192k / 128k | $0.132 / $0.528 — **best fit** |
   | `hy4-preview` | 1M | 960k / 64k | $0.834 / $2.501 |
   | `deepseek/deepseek-flash` | 1M | 1M / 384k | $0.15/$0.60 off-peak, $0.30/$1.20 peak |
   | `deepseek-v4-flash` | 1M | 1M / 384k | $0.14 / $0.28 |
   | `deepseek-v4-flash-0731` | 1M | 1M / 384k | $0.22/$0.66 off-peak |
   | `glm-5.3-flash` | 1M | 1M / 128k | $0.15 / $0.50 (image+video in) |
   | `glm-5.3` | 1M | 1M / 128k | $1.4 / $4.4 |
   | `kimi-k2.6` | 256k | 256k / 256k | $0.858 / $3.566 |
   | `minimax-m2.7` | 256k | — | $0.3 / $1.2 |
   | `deepseek-v3.2` | 128k | 96k / 32k | $0.57 / $1.71 |
   Others present: `kimi-k3` (1m), `kimi-k2.8-preview` (1m), `kimi-k2.7-code`, `minimax-m3`, `MiMo-V2.5-Pro`, `Hy-MT2-*` translation.
4. **No deployment id needed** — raw model id in `model`. (TokenHub also offers 在线推理 service instances with "专属 API Endpoint", but the standard endpoint works with a plain model name.)
5. **Official URLs** — <https://www.tencentcloud.com/zh/document/product/1300/78941> (API usage), <https://www.tencentcloud.com/zh/document/product/1300/78934> (model list + context), <https://www.tencentcloud.com/zh/document/product/1300/78937> (pricing), <https://cloud.tencent.com/document/product/1729/111007> and `/104753` (legacy platform + migration notice).
6. **Deprecations with dates** — official 模型列表 page: `glm-5`, `glm-5-turbo`, `glm-5.1` retire **2026-10-08**; `glm-5v-turbo` retires **2026-10-30**. Legacy Hunyuan: `Max` tier retired **2026-03-10**; HY 2.0 Instruct/Think, Hunyuan-T1, Hunyuan-TurboS offline **2026-06-10**. `hunyuan-t1-latest`, `hunyuan-a13b`, `hunyuan-turbos-latest`, `hunyuan-lite`, `hunyuan-translation*`, `hunyuan-large-role-latest` are **not available on TokenHub**. (I earlier found `hunyuan-a13b` = 224K input / 32K output, ¥0.5/¥2 per M — but it is legacy-only and must not be a new integration target.)

---

## 4. iFlytek Spark 讯飞星火

1. **base_url** — **not one URL.** Chat models: `https://spark-api-open.xf-yun.com/v1/` (verified, <https://www.xfyun.cn/doc/spark/HTTP调用文档.html>). Reasoning families live on **different paths** (verified, <https://www.xfyun.cn/doc/spark/X1http.html> and <https://www.xfyun.cn/doc/spark/X2-Flash.html>):
   - X2 → `https://spark-api-open.xf-yun.com/x2/`
   - X1.5 → `https://spark-api-open.xf-yun.com/v2/`
   - X2-Flash → `https://spark-api-open.xf-yun.com/agent/v1/` (also an Anthropic-protocol path `/anthropic/agent/v1/messages`)
   Auth: APIPassword as `Authorization: Bearer <APIPassword>`.
2. **Model IDs** (official):
   - Reasoning: `spark-x` — used on **all three** reasoning paths. Note the doc's own aside: 历史"x1"传参指向X1.5模型 (the legacy `x1` value now maps to X1.5).
   - Chat (`/v1/`): `4.0Ultra` (32K in / 32K out), `generalv3.5` = Max (8K/32K), `max-32k`, `generalv3` = Pro (8K/128K), `pro-128k`, `lite` (8K, free).
   - Context: X2 = 64K input / 128K output; X1.5 = 64K output; **X2-Flash `max_tokens` range [1, 262144] — the only >200K option here** (256K budget).
3. **`GET /models`** — **no model-list API found** in the official HTTP/reasoning docs. Discovery is **not supported**; model names must be hardcoded. (Verified by absence across the three doc pages above.)
4. **No deployment id** — plain model string.
5. **Official URLs** — <https://www.xfyun.cn/doc/spark/HTTP调用文档.html>, <https://www.xfyun.cn/doc/spark/X1http.html>, <https://www.xfyun.cn/doc/spark/X2-Flash.html>, <https://www.xfyun.cn/doc/spark/接口说明.html>.
6. **Deprecation** — verified on the official HTTP doc: **Max 版本套餐将于 26 年 3 月 10 日下线，后端服务将升级为 Ultra 版本** (Max package retires 2026-03-10, backend upgrades to Ultra; quota merges into Ultra). **Practical warning: a tool that assumes one base_url per provider will break on Spark** — the reasoning models are not on `/v1/`.

---

## 5. StepFun 阶跃星辰

1. **base_url** — `https://api.stepfun.com/v1`, verified with Python/TypeScript/LangChain migration snippets at <https://platform.stepfun.com/docs/zh/guides/developer/openai>. (Tip: appending `.md` to StepFun doc URLs returns full raw content — the normal HTML pages truncate.)
2. **`GET /models` works** — the OpenAI-compatible list is explicitly enumerated on the same page: 获取模型列表 (`/docs/zh/api-reference/models/list`) and 查询单个模型信息 (`/retrieve`).
3. **Ordered model IDs** (verified at <https://platform.stepfun.com/docs/zh/guides/models/overview>):
   1. `step-3.7-flash` — **256K**, flagship multimodal reasoning (image+video in, three reasoning levels).
   2. `step-3.5-flash` — **256K**, flagship language reasoning.
   3. `step-3.5-flash-2603` — 256K, Agent-optimized, more token-efficient (priced identically to 3.5-flash on SiliconFlow at ¥0.7/¥2.1).
   4. `step-1o-turbo-vision` — 32K (vision).
   Avoid: all older `step-1*`, `step-2*`.
4. **No deployment id** — plain model string.
5. **Official URLs** — <https://platform.stepfun.com/docs/zh/guides/developer/openai>, `.../guides/models/overview`, `.../guides/models/overview.md`, `.../guides/model-migration.md`, `.../guides/image-offline-notice`.
6. **Deprecations with dates** (verified at <https://platform.stepfun.com/docs/zh/guides/model-migration>): **2026-07-08** — `step-1-8k`, `step-1-32k`, `step-1v-8k`, `step-1v-32k`, `step-2-mini`, `step-1o-vision-32k`, `step-2-16k`, `step-3`, `step-1x-medium` all retire; `step-3` → `step-3.7-flash`. **2026-10-10** — `step-2x-large` and the text-to-image / image-edit interfaces retire.

---

## 6. 01.AI Yi 零一万物

**Low confidence — official docs are a client-rendered SPA.** `https://platform.lingyiwanwu.com/docs` and `/docs/api-reference` both returned only the nav shell and a footer reading "01.AI © Copyright 2024" (suggesting the platform page has not been refreshed in a long time).

1. **base_url** — `https://api.lingyiwanwu.com/v1`, `verified=false` (secondary source only: an API-catalog dataset at <https://raw.githubusercontent.com/api-evangelist/01-ai/main/apis.yml>, generated 2026-08-28).
2. **Model IDs** — `Yi-Lightning`, `Yi-Lightning-Lite`, `Yi-Large`, `Yi-Large-Turbo`, `Yi-Vision` — **all `verified=false`**, same secondary source. **No context windows could be verified.** I found no official page confirming any of these.
3. **`GET /models`** — **could not verify.**
4. **Deployment/endpoint id** — no evidence of one; `verified=false`.
5. **Official URL** — none readable. Secondary only: <https://raw.githubusercontent.com/api-evangelist/01-ai/main/apis.yml>.
6. **Deprecations** — **could not verify.**

**Verdict for the tool: not recommended.** No verifiable >200K model and no readable current official documentation.

---

## 7. Baichuan 百川智能

**Partially verified.** The docs portal is a Next.js SPA and returned no body text (`platform.baichuan-ai.com/docs`, `/docs/api`, `commercial-platform.baichuan-ai.com/docs` → fetch error). **The official price page does render fully.**

1. **base_url** — **could not verify.** No official page states it in readable text. Do not assume.
2. **Current model IDs** — verified at <https://platform.baichuan-ai.com/prices>:
   `Baichuan-M3-Plus` (32k, ¥0.005/¥0.009 per 1K tok), `Baichuan-M3` (32k), `Baichuan-M2-Plus` (32k), `Baichuan-M2` (32k), `Baichuan4-Turbo` (32k), `Baichuan4-Air` (32k), `Baichuan4` (32k), `Baichuan3-Turbo` (32k), **`Baichuan3-Turbo-128k` (128k — the only >32k option)**, `Baichuan2-Turbo` (32k), `Baichuan2-53B` (32k). Embedding: `Baichuan-Text-Embedding`.
3. **`GET /models`** — **could not verify.**
4. **Deployment/endpoint id** — no evidence; **could not verify.**
5. **Official URL** — <https://platform.baichuan-ai.com/prices> (readable); docs portal unreadable.
6. **Deprecation with date** — verified on the price page: **`Baichuan2-Turbo-192k` was removed 2024-08-16**, and calls to it are routed to `Baichuan3-Turbo-128k`.

**Verdict for the tool: not recommended.** Maximum context is 128K and the newest models (M-series) are medical-domain and 32K.

---

## 8. Baidu Qianfan v2 百度千帆

**Low confidence on the specific details — every `cloud.baidu.com/qianfan*/doc` page I fetched returned only site chrome (JS-rendered body).** I attempted many URLs (`/doc/qianfan-docs/s/...`, `/doc/qianfan-api/s/3m7of64lb`, `/doc/qianfan/s/rmh4stp0j`, `/doc/WENXINWORKSHOP/s/em4tsqo3v`, `intl.cloud.baidu.com/zh/doc/qianfan/s/7m95lyy43-intl`) and all failed to render body content. Pages like `/doc/qianfan-docs/s/Fm9l6ocai` (Python SDK) render partially but truncate before the useful part.

1. **base_url** — `https://qianfan.baidubce.com/v2`, **`verified=false` on an official page.** It is confirmed on a maintained third-party integration doc (<https://docs.openclaw.ai/providers/qianfan>) and matches the v2 OpenAI-compatible endpoint. Treat as high-likelihood but not officially verified by me.
2. **Model IDs** — from the same secondary source, **`verified=false`**:
   | model | ctx | max output |
   |---|---|---|
   | `deepseek-v4-pro` | 1,000,000 | 393,216 |
   | `ernie-5.1` | 128,000 | 65,536 |
   | `ernie-5.0` | 128,000 | 65,536 (text+image, thinking) |
   | `deepseek-v3.2` | 128,000 | 32,768 (noted deprecated onboarding default) |
   | `ernie-5.0-thinking-preview` | 128,000 | 65,536 (noted deprecated alias → `ernie-5.0`) |
   `ernie-5.0` is corroborated by an independent third-party API page (`docs.aimlapi.com/api-references/text-models-llm/baidu/ernie-5.0`). **No ERNIE ID here was verified on a Baidu official page by me.**
3. **`GET /models`** — **could not verify.** The secondary source states the catalog is static with **no live model discovery**, which suggests discovery likely does not work — unverified.
4. **Deployment/endpoint id** — no deployment-id requirement found for v2; auth is a `bce-v3/ALTAK-...` API key. `verified=false`.
5. **Official URLs attempted (unreadable)** — <https://cloud.baidu.com/doc/qianfan-api/s/3m7of64lb> ("文本生成"), <https://cloud.baidu.com/doc/qianfan-api/s/vmhejnuy8> ("创建模型响应"), <https://cloud.baidu.com/doc/qianfan/s/rmh4stp0j> ("模型列表"). Secondary: <https://docs.openclaw.ai/providers/qianfan>.
6. **Deprecations** — the secondary source flags `deepseek-v3.2` as a "deprecated onboarding compatibility default" and `ernie-5.0-thinking-preview` as a deprecated alias, **without dates**, and one result mentioned an "ERNIE开源模型上线公告". **No dated deprecation verified on an official Baidu page.**

---

## 9. SenseNova 商汤日日新

**Good confidence.** Official docs render well on the SenseCore help centre.

1. **base_url** — `https://api.sensenova.cn/compatible-mode/v2`, verified at <https://www.sensecore.cn/help/docs/model-as-a-service/nova/overview/compatible-mode>. Auth: `Authorization: Bearer {api-key}` (ModelStudio API Key).
2. **Model IDs + context (official table on the same page)**:
   - `SenseChat-5` — **128K**, ¥0.008 in / ¥0.02 out per 1K tok — best text option
   - `SenseNova-V6.5-Pro` — 128K, ¥0.003/¥0.009 (native image+text+video)
   - `SenseNova-V6.5-Turbo` — 128K, ¥0.0015/¥0.0045 — cheapest 128K
   - `SenseChat-Turbo` — ¥0.0003/¥0.0006 (context not documented)
   - `SenseChat-5-Cantonese` — 32K; `SenseChat-Character-Pro` — 32K; `SenseChat-Character` — 8K; `SenseChat` — 4K; `SenseChat-Vision` — 4K
   Example model strings seen in docs include both `SenseNova-V6-Pro` (older example) and `SenseNova-V6-5-Pro` (newer curl) — prefer the V6.5 names.
3. **`GET /models`** — **not the OpenAI-compatible path.** The documented list endpoint is `GET https://api.sensenova.cn/v1/llm/models` (<https://www.sensecore.cn/help/docs/model-as-a-service/nova/overview/Models/GetModelList>), with a custom response shape (`data[].id`, `type` = `BASE_MODEL`/`FINE_TUNED_MODEL`, `permission[]`). **Whether `GET /v1/models` works on the compatible-mode host could not be verified** — assume it does not and use the documented path.
4. **No deployment id** — plain model string.
5. **Official URLs** — <https://www.sensecore.cn/help/docs/model-as-a-service/nova/overview/compatible-mode>, `.../nova/overview/Models/GetModelList`, `.../nova/release`, `.../nova/pricing/`. Note `https://platform.sensenova.cn/doc` returns **404** — the live docs moved to sensecore.cn.
6. **Deprecations** — **none verified.** The release-notes page (<https://www.sensecore.cn/help/docs/model-as-a-service/nova/release>) shows nothing newer than release-202507 (2025-07-23, SenseNova-V6.5-Pro/Turbo with 32k/64k/128k windows) — i.e. **no dated retirement notice found**, but also no evidence of models newer than V6.5.

**Verdict for the tool: not recommended for >200K** — nothing here exceeds 128K. Usable only if 128K suffices, in which case `SenseNova-V6.5-Turbo` is very cheap.

---

## Cross-cutting notes for the desktop tool

- **Dynamic discovery is not universal.** Confirmed working: SiliconFlow (`GET /v1/models`, Bearer required), Tencent TokenHub (`GET /v1/models`). Documented for StepFun. Confirmed *absent*: iFlytek Spark. Volcengine Ark's list operation is an AK/SK-signed POST OpenAPI, not `GET /models`. Unverified: Qianfan, Baichuan, 01.AI, and SenseNova (which uses a non-OpenAI list path `/v1/llm/models`). **Design for "discovery may 404" and keep a hardcoded fallback list.**
- **One base_url per provider is wrong for Spark.** Chat models are on `/v1/`, reasoning models on `/v2/`, `/x2/`, or `/agent/v1/`.
- **Volcengine Ark does not need `ep-` ids any more, but the model id must be fully versioned.** `doubao-seed-2-0-pro` (bare family) 404s; `doubao-seed-2-0-pro-260215` works. Confirmed by the parent agent against Ark's own doc JSON API (`https://www.volcengine.com/api/doc/getDocDetail?DocumentID=1330310` — note the case-sensitive `DocumentID`), whose table column is literally "模型 ID (Model ID)" with zero occurrences of `ep-`. That same page is the reference for a large set of 即将下线 IDs.
- **Tencent is the biggest change since older integrations were written:** `api.hunyuan.cloud.tencent.com/v1` → TokenHub (`tokenhub*.tencentcloudmaas.com/v1`), and several familiar `hunyuan-*` IDs do not exist on TokenHub.
- **Providers to skip for this use case:** Baichuan (128K max, newest are 32K medical), SenseNova (128K max), 01.AI (nothing verifiable).
- **Doc-site scraping caveat:** `docs.volcengine.com` / `ark.volcengine.com` and all `cloud.baidu.com/qianfan*/doc` pages are JS-rendered and yield empty bodies to a plain fetch. Volcengine exposes a JSON API behind its pages (`?DocumentID=`), and many docs sites expose a `.md` twin (StepFun and Mintlify-based sites do) — useful techniques for future research.

