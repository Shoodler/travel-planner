# Project audit and differentiation plan

Audit date: 22 October 2026. Baseline: commit `c6b2513341996744c8ab7e28041d4241df2fad42`, plus the explicitly documented local model configuration change below.

## Assessment

This is a useful demonstration of concurrent model calls, orchestration, and streamed events. It is not yet a trustworthy travel planner, and the number of agents does not establish a research contribution. The hardest missing part is enforcing consistency between suggestions, constraints, and the final itinerary.

The strongest argument in its favor is that its communication, orchestration, and presentation layers are small and understandable. Central orchestration and using one model for several roles do not invalidate a multi-agent design. However, there is no evidence that this decomposition improves feasibility or preferences compared with one well-prompted model. That is a hypothesis to test.

## Scope and evidence

- Read all application source, the original architecture and implementation instructions, dependency specifications, and existing tests.
- Reran the 13 existing offline backend tests: all pass. They cover selected workflow invariants, not end-to-end travel correctness.
- Ran frontend dependency auditing: zero reported vulnerabilities at audit time.
- Ran `pip-audit -r travel-planner/backend/requirements.txt`: 16 advisory records across two packages, representing eight distinct CVEs after deduplication.
- Ran nine safe offline reproductions. Results are in `probe-results.json`; run with `.venv/Scripts/python.exe audit/probes.py` from the repository root. These use isolated TestClient state and fake agents, not the running service or the provider.
- Used the user-supplied credential only through the ignored local environment file and Groq API. The original model failed with `model_not_found`; listing available models succeeded. With an available model, budget splitting and all specialists completed, but final assembly failed with HTTP 400 `json_validate_failed`.
- No destructive testing, provider load testing, production penetration test, or broad claim that the system is secure. Prompt-injection resistance was inspected structurally, not established experimentally.

## Prioritized findings

| ID | Priority | Evidence and impact | Required change |
|---|---|---|---|
| S1 | High before shared/public deployment | `backend/main.py:70` starts provider work without authentication, request throttling, a user quota, or a concurrent-run limit. An offline client created 100 sessions; request 101 received 503. Finished sessions retain their slots until TTL cleanup, so completed requests also fill capacity. With a real key, accepted requests can consume its quota. | Add admission control before client/task creation: concurrent-run semaphore, bounded pending queue, rate limits and spend budget; add identity for a shared service. Evict completed sessions by a documented retention policy. |
| S2 | Medium, deployment-dependent | `backend/main.py:35` allows every CORS origin. An Origin-bearing POST was accepted with `Access-Control-Allow-Origin: *`. This permits cross-origin use at the server policy level. A malicious page reaching a localhost service also depends on browser local-network protections; that browser attack was not demonstrated. | Allow the intended frontend origins only. CORS is not authentication and does not stop direct HTTP clients. |
| S3 | Medium when trip privacy matters | `backend/main.py:96` allows anyone possessing a session UUID to read its full transcript. A second request required no owner identity. UUIDs are not demonstrated guessable; this is a bearer-link access model, not a demonstrated enumeration attack. | Document private bearer links for local demos; for accounts, bind sessions to owners and authenticate streams. Avoid sharing raw session URLs. |
| S4 | Medium, resource risk | `backend/message_bus.py:18` creates unrestricted subscribers with unbounded queues. There is no per-session stream connection limit. Session-count limits do not bound subscriber count. | Limit stream connections and queue sizes, disconnect slow consumers, and set connection lifetime limits. |
| S5 | Upgrade required; exploitability conditional | Backend dependencies contain the advisories listed below. Current application routes do not invoke many affected features. | Upgrade FastAPI and its compatible Starlette dependency together, update dotenv, lock the resolved dependency tree, then rerun the tests and audit. Do not override Starlette to an incompatible version under old FastAPI. |
| C1 | High correctness | `backend/orchestrator.py:199` validates the final plan by aggregate price and time format. A single invented “Unapproved helicopter ride” costing $80 replaced Metro + Cafe + Museum costing $80 and was accepted. Matching totals do not preserve items, prices, meals, or provenance. | Give proposals stable IDs. The final plan must reference accepted proposal IDs; keep prices and venue data authoritative in code, rather than regenerated by the planner. |
| C2 | High correctness | `backend/orchestrator.py:243` zips activities with five fixed slots. Six $10 activities plus $40 in other costs became a $90 plan rather than $100, silently dropping one activity. | Validate cardinality explicitly or schedule every accepted item; enforce that the output item multiset matches the selected proposals. Never silently truncate. |
| C3 | High correctness | Fallback scheduling returned `25:90` unchanged from dining output. Activity durations are generated but discarded; no travel-time, overlap, opening-hours, or date check exists. Sorting start times is not schedule validation. | Typed time intervals, durations, venue IDs, opening windows, date/timezone, travel matrix, and an independent feasibility validator used on every path, including fallback. |
| C4 | Medium correctness | `_specialist_result` accepted `{"meals":[{"cost":0}],"total_cost":0}`. A name, actual meal, and time are not required. It does not enforce breakfast/lunch/dinner or three to five activities. | Typed category-specific schemas with required fields and domain-level constraints. JSON syntax alone is insufficient. |
| C5 | Medium correctness | A specialist's declared $400 activity total with $40 in line items produced a $440 negotiation total but an $80 final display. The two phases use inconsistent accounting rules. | Compute one canonical total from selected line items in integer cents or Decimal. Treat inconsistent declared totals as validation errors or repair requests. |
| C6 | Product limitation, intentional in brief | Two failed revisions still yield `final_plan`: a $100 request produced $140. A plan up to 10% over budget also skips negotiation by design. The frontend does warn, so this is not a hidden-budget bug. | Separate feasible, over-budget, and infeasible results. Offer explicit user-approved relaxation rather than labeling every output simply ready. |
| R1 | Live blocker found; configuration fixed locally | `backend/agents.py:9` originally hardcoded a model unavailable to the configured account. Groq returned HTTP 404. | Model is now configurable via `GROQ_MODEL`, with an available account model selected. Add a model capability/startup check; availability can change. |
| R2 | Live blocker still open | The available model completed specialist work but final assembly returned HTTP 400 `json_validate_failed`. `_parse_json` and `_build_itinerary` never run for that failed call. The existing fallback only covers a returned but unusable response. | Prefer a deterministic schedule assembled from validated proposals. If model assembly remains, handle provider JSON-generation failure with bounded repair or an explicit fallback, and preserve cancellation/auth/rate-limit errors as distinct cases. |
| R3 | Medium reliability | `frontend/src/hooks/useSSE.js:53` closes permanently on the first network error. The backend cannot resume from a last event ID. Page refresh loses the current session; abandoned sessions keep computing until completion/deadline. | Sequence event IDs, bounded reconnect with deduplication, recoverable session state, explicit cancellation, and a session status endpoint. |

### Dependency advisories, without overstating reachability

Raw output: `python-dependencies.json`. The auditor repeats entries; do not interpret its 16 records as 16 independent vulnerabilities.

| Package | Distinct CVEs | Relevance to this code |
|---|---|---|
| python-dotenv 1.0.1 | CVE-2026-28684 | Concerns symlink handling during `set_key`/`unset_key`. The application uses `load_dotenv`, not these rewrite functions. No remote exploit demonstrated. Auditor lists 1.2.2 as fixed. |
| Starlette 0.38.6 | CVE-2024-47874, CVE-2025-54121, CVE-2026-54283 | Multipart or URL-encoded form parsing DoS paths. Current planning endpoint consumes a JSON Pydantic body; no form/file endpoint is present. |
| Starlette 0.38.6 | CVE-2026-48818 | Windows StaticFiles UNC/SMB exposure. This FastAPI app does not mount StaticFiles. |
| Starlette 0.38.6 | CVE-2026-48817 | HTTPEndpoint method dispatch. Current routes use FastAPI function endpoints. |
| Starlette 0.38.6 | CVE-2026-48710, CVE-2026-54282 | Request URL reconstruction/path-security discrepancies. No custom URL-based authorization or redirect logic is present. Current deployment exploitability was not established. |

See the [Starlette maintainer advisories](https://github.com/Kludex/starlette/security/advisories) for prerequisites. These findings justify dependency maintenance, not a claim that this app has eight reachable remote exploits.

### Prompt injection and secrets

City and interests are interpolated directly into task instructions; specialist output becomes input to the final planner. This enables attempted instruction contamination across stages. Without external tools, arbitrary code execution, database writes, or booking actions, the principal current risk is corrupted planning output, not demonstrated credential theft or remote code execution. The key is passed to the SDK, not the model prompt.

No application use of `eval`, `exec`, pickle deserialization, or raw HTML rendering was found. React's normal text rendering limits the straightforward HTML injection path. No credential was found in tracked source; the real `.env` is ignored and untracked. These observations do not constitute exhaustive security guarantees.

Future retrieved webpages and tools must return typed untrusted data. Do not let venue text change agent instructions or grant tools permissions. Claims and numbers need provenance and validation, not just a stronger instruction to the model.

## Why it currently feels generic

1. **It has orchestration without evidence of better plans.** Four prompts share one model and independently invent suggestions. No evaluation establishes an advantage over one model.
2. **The bus is an event log/broadcast layer, not the execution router.** The orchestrator directly invokes `Agent.run`; assignments are recorded in the bus, but the bus does not deliver work to agents. That is acceptable for a POC, but architecture descriptions should say so.
3. **It demonstrates budget feedback, not joint planning.** The transport agent cannot see the proposed attraction locations before choosing transport. Dining cannot coordinate lunch with the activity route. The fixed category split and “revise the most expensive category” rule ignore marginal preference value and cross-category tradeoffs.
4. **The visible graph explains execution, not quality.** It shows a response arrived, but not which constraint it satisfies, which source supports it, or what was rejected and why. A “thinking” label is a program stage, not access to internal model reasoning.
5. **There is no grounded world model.** No visit date, starting point, coordinates, opening windows, or current prices. The live transport response claimed a Tokyo Metro ticket covered JR lines, conflating distinct ticket products. [Tokyo Metro distinguishes its ticket products and valid networks](https://www.tokyometro.jp/lang_en/ticket/value/1day/index.html). The generated dining response also proposed 07:30 despite the requested 08:00–22:00 day.
6. **There is no persistent user decision.** Users cannot lock a must-see stop, compare alternatives, or repair an existing plan after a disruption. Every run starts over.

More agents, a map, chat, voice input, and a different model can improve presentation or usability. None alone provides a defensible novelty claim.

## Recommended project direction

**A travel planner that repairs a disrupted day while preserving the user's commitments, with independently checked constraints and visible evidence for every change.**

Example demonstration: the user locks a 17:00 reservation and a favorite museum. At 13:00, inject a 45-minute delay and a venue closure. Preserve completed stops and locked commitments, repair only the remaining schedule, and explain any genuinely impossible combination. Show the old and new plan alongside the exact violated constraints and changed stops.

### Build these capabilities as one coherent system

| Capability | Implementation | Observable acceptance condition |
|---|---|---|
| Grounded proposals | Begin with one curated city and a versioned, licensed dataset of venues, prices, durations, coordinates, sources, and opening windows. Label snapshot data and simulated disruptions explicitly. | Every selected stop has a stable venue/proposal ID and source timestamp; model-generated facts cannot overwrite catalog fields. |
| Independent feasibility check | Use a constraint solver or small deterministic scheduler. Hard constraints cover time windows, travel, non-overlap, exact budget, meal requirements, and user locks. LLMs propose preferences and explain decisions. | Every accepted plan passes the same validator; infeasible requests return explicit conflicting constraints rather than invented availability. Validation is only as accurate as supplied data. |
| Minimal-change repair | Store plan versions and completed/locked stops. On a disruption, optimize first for hard constraints, then preserving commitments and minimizing changes, then user utility. Make this objective explicit. | No completed/locked stop is silently altered. Display changed items, lost preference value, walking/time/cost changes, and any requested relaxation. |
| Meaningful negotiation | Specialists propose candidate sets with IDs, costs, and preference scores; they receive concrete conflict feedback such as a closed venue or impossible transfer. Revisions refer to the conflicting proposal IDs. | A replay shows an initial conflict, a targeted proposal change, and a successful independent recheck. No need to display private chain-of-thought; show concise decision records. |
| Counterfactual controls | “What if I have $20 less?”, “I want less walking,” or “keep this stop.” Reuse the current proposal set and constraints. | Before/after comparison attributes each edit to a constraint or preference; runtime and provider usage are measured. |

OR-Tools already supports [routing with time windows](https://developers.google.com/optimization/routing/vrptw); scheduling technology itself is not novel. Restrict the first version to a small candidate set, one traveler, one city, and one day. Model explicitly what is known, estimated, or missing; avoid unsupported claims such as “verified accessible” from model inference.

### The strongest counterargument to the novelty claim

Travel planning under constraints and multi-agent planning already exist. [TravelPlanner](https://github.com/OSU-NLP-Group/TravelPlanner) supplies an established planning benchmark, and [LRPLAN](https://aclanthology.org/2025.findings-emnlp.440/) studies collaborative agents for explicit and implicit constraints. Combining agents with a solver is therefore not sufficient to claim a new research method.

The defensible contribution to investigate is **whether targeted repair preserves user commitments and preferences with fewer changes and lower inference cost than full regeneration, while matching an independent feasibility standard**. This is a project hypothesis, not an established result or a guarantee of global novelty. A publication claim would require a wider literature review and experiments.

### Evaluation that can change the verdict

Start with 30 independently constructed scenarios over the same city snapshot, covering closures, delays, weather restrictions, budget cuts, and incompatible locks. Include both feasible and deliberately infeasible cases. Repeat model conditions across five runs and report spread rather than one best result.

Compare: (A) one well-prompted model regenerating the day; (B) the current four-role workflow; (C) a deterministic optimizer over the same candidate catalog; (D) the proposed repair system. Give all methods the same information and candidate access. Include an ablation removing agent negotiation from D; otherwise solver gains may be misattributed to agents.

Measure hard-constraint pass rate, infeasibility detection and explanation accuracy, locked-stop violations, changed-stop count, retained preference utility, source coverage, repair latency, token usage, and failure rate. Have a human independently inspect a sample for assumptions the validator misses. Distinguish solver-validated snapshot feasibility from actual real-world availability.

If D costs more but does not outperform A or C, simplify the agent design. If it preserves user commitments reliably under disruption, that is a substantially stronger demonstration than an animated collection of generated recommendations.

## Delivery order

1. Fix canonical proposal IDs, typed schemas, accounting, and the final-assembly failure; add regression cases from this audit. Make exact-budget policy explicit.
2. Upgrade and lock backend dependencies. Add admission control, origin restriction, bounded subscribers, cancellation, and session lifecycle handling before any public deployment.
3. Introduce one-city catalog, independent validator, date/start location, and feasible/infeasible result types.
4. Add plan versions, user locks, disruption injection, and minimal-change repair. Make before/after comparison the primary demo; keep the agent graph as a secondary explanation.
5. Run the baseline/ablation experiment and write the results, including negative results. Add map/export polish only after the planning claim holds.

## Changes made during this audit

Saved the supplied credential only in ignored `.env`; configured `GROQ_MODEL`; changed the application to read that setting with default `openai/gpt-oss-20b`; increased the bounded completion allowance and set low reasoning effort for GPT-OSS models; updated the environment example and README. Restarted only this project's backend on port 8001. The 13 existing regression tests still pass.

No broad security fixes or proposed new features have been implemented. The final live assembly failure remains open. Audit evidence and these small configuration changes are local and have not been pushed to GitHub during this audit.
