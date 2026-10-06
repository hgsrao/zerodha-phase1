# Codex cross-check of Claude closure review

Claude job 11260a29-926f-4ef8-9a02-46a84b59307d completed exit 0. Read-only; no shared checkout edits.

Agreed: separate explicit experimental lifecycle entrypoint, actual-source identity, no sealed-result relabelling, strategy isolation harness retained separately, fleet-loading prototype not integrated, Codex owns execution and release.

Corrections before implementation:

1. execute_block is not limited to one symbol: prepare_block iterates manifest.files and supplies all frames. The one-symbol restriction is in the already-run test fixture, not the production executor. Full 48-symbol execution remains unproven.
2. CostedPaperBrokerAdapter already implements ensure_protection, request_product_conversion, conversion_receipt and ensure_cnc_gtt. No missing-method replacement is required. Contract coverage and lifecycle composition are required.
3. Passing next_timestamp to morning recovery does not unblock .run(): orchestrator.run explicitly refuses _morning_recovery_prepared. A session_driver which just calls prepare then run would fail. Recovery must use certified continuation/reconstruction or implement complete compatible hydrated execution before claiming admission.
4. Runtime-less morning recovery is an unsupported composition, not a reason to make identity silently null-safe: explicit runtime prerequisites and source/policy identity must be preserved.
5. Proposed completed-trade/lifecycle-row equality is only valid with CLOSE and no remaining active positions. PERSIST acceptance must account for active B positions and their protection separately.
6. Baseline fingerprint parity must compare identical source/data/params/runtime composition; changing active governor logic while retaining a sealed label is not a valid baseline comparison.

Implementation packages must start from a snapshot including all current dirty and untracked required modules. The proposed entrypoint is a suitable Claude bounded package. Recovery changes and fleet integration need precise integration contracts before parallel edits. Nothing was merged, committed, pushed or live-enabled in this discussion turn.
