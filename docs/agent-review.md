# Setup memory and evidence-based AI review

`python3 main.py run` uses these features automatically for new research packets. Existing portfolio limits, structural entry rules, and protective exits are unchanged. This implementation adapts architectural ideas from the supplied R4I-CALLING reference; it does not import or execute that project's code.

## Decision contract

New research packets use version 2. A review includes the existing action, concise rationale, invalidation, source IDs and expiry, plus:

- `supporting_evidence`: up to three observations, each citing an existing input `/market/` or `/news/` JSON pointer.
- `opposing_evidence`: up to three conflicting observations with the same source requirement.
- `wait_for`: a nonempty observable reconsideration condition for WAIT; empty for LONG/SHORT.

Directional proposals require at least one supporting observation. Missing references, changed direction, malformed fields, expiry violations and unsupported schema fields are rejected. Source references demonstrate provenance only: code cannot prove an AI interpretation is true. An empty opposing list does not establish safety or high confidence. No confidence probability is introduced.

The AI still makes one request per review, with at most three attempted reviews per portfolio scan. It cannot change stop levels, leverage, risk limits, slots or account permissions. `wait_for` is advisory text: the normal scanner and fresh AI review must confirm a subsequent setup. There is no automatic execution of remembered requests. Historical version-1 packets remain valid under their original schema.

## Memory boundaries

`setup_memory` in `runtime/scans.sqlite3` stores chronology by strategy version, pair, direction, structure model and BOS timestamp. New packets receive at most three earlier records of that same setup. These contain prior scanner state, candidate prices, AI action, rationale, supporting/opposing evidence and invalidation. Repeated skipped scans with unchanged screen status/reasons do not add duplicate records. The current packet is never included in its own memory; future records and other strategy versions are excluded.

Where a saved review links to the reference signal tracker, its status/stage/quality may be attached as context, only if its observation cursor is no later than the review time. These are reference price touches, not executed fills, realized PnL or win labels. Missing reference evidence is omitted. Current memory does not compute a learned success rate, aggregate unrelated setups or change strategy parameters. Actual trade evidence remains in the portfolio ledger and separate improve reports.

Prior AI text is explicitly untrusted historical opinion. Current market evidence takes precedence. Full reviews are journaled in `ai_analyses`; active position evidence also retains the structured review and supplied setup memory. Public signal cards continue to show the concise rationale rather than internal evidence pointers.

## Provider health

`provider_health` and `provider_usage` in the research database persist across research subprocesses and engine restarts. The profile is a digest of endpoint, model and credentials; raw credentials and provider response bodies are not stored there. Changing key/model/endpoint permits a new attempt.

| Failure | Next eligible attempt |
|---|---|
| HTTP 402 | Retry eligibility after 15, 30, then 60 seconds; after the third retry fails, pause 15 minutes. The cause is unconfirmed. |
| HTTP 401/403 | After 30 minutes; authentication or permission issue |
| HTTP 400/404 | After 30 minutes; request/model configuration issue |
| HTTP 429 | 60 seconds, doubling on repeated failures, capped at 30 minutes |
| Other request failures | 15, 30, then 60 seconds; subsequent failures pause 15 minutes |

A 90-second lease prevents overlapping requests for the same profile. A crashed request's lease expires; a late response cannot overwrite a newer lease. A successful API response clears provider failures and records available token counts. Success at the API level does not imply the proposal passed schema validation. There is no automatic retry within a request, no model substitution, and no inferred dollar cost from token counts.

Cooldowns are reported as `SKIPPED / AI_PROVIDER_COOLDOWN`, separately from market WAIT or an actual failed request. No API call is made while a cooldown/lease is active. Current provider status is included in the research report and `/status`; owner notifications report unhealthy provider state. Scanning and independent position management continue. A technical pause is not a reason to relax the scanner or risk checks.

## Evaluation limits

This adds chronology and explicit evidence review; it does not establish improved profitability. No additional entry model is activated, no previous approval is reused, and no automatic strategy optimization occurs. Compare versioned demo outcomes, rejection reasons, latency, and token usage before considering a larger multi-agent review. Offline tests cover the new contracts, causal memory, source references, provider backoff/recovery, concurrency leases, and research integration. Live provider compatibility with the expanded schema still requires a new eligible review.

## Publication during provider outages

Transport failures and provider cooldowns can publish a fresh passing `structure_bos_retest_v1` setup with a deterministic **Technical Rationale**. The card explicitly says AI review is unavailable; it does not invent AI findings, news analysis or confidence. Invalid AI proposals and AI WAIT decisions do not trigger this fallback. Signal tracking records the publication basis, and existing delivery deduplication still applies. Reference outcomes remain separate from trade PnL.

The analysis remains ERROR/SKIPPED and stores `technical_proposal` separately. Portfolio entry still requires `status=VALIDATED` and an AI proposal. Publication does not authorize trading, bypass daily limits or fill a slot.

Failed-review pairs persist in `pending_setups` for at most 15 minutes from the original failure. Pending pairs receive priority within the 20-pair scan cap even if discovery ranking changes. Every retry fetches and screens new market data; expired or no-longer-eligible setups are removed, and no cached price is used for entry. A validated AI WAIT also removes the pending candidate.

The supervisor schedules a fresh scan when the retry becomes eligible (15/30/60 seconds for transient errors and 402). These are minimum delays: scan duration, the three-review budget and polling can make the actual request later. Requests are not retried in a sleeping research process; position supervision continues independently. HTTP 429 retains its longer quota backoff, and authentication/configuration failures retain their 30-minute pause. No provider/model is switched automatically. Recovery notifications require a validated AI response.
