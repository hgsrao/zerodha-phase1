# Isolated flat-price grading: Block 1

Diagnostic variants only; frozen source snapshots, 48 symbols and five Stage-B sessions. The sealed CLI identity guard was not invoked, and these results are not sealed V3 reference certification. No active-engine grading change is promoted.

| Measure | Baseline | Neutral grading |
|---|---:|---:|
| Trades | 58 | 58 |
| BUY | 1 | 1 |
| SELL | 57 | 57 |
| Gross P&L | -4984.11 | -5169.97 |
| Friction | 3036.27 | 3118.51 |
| Net P&L | -8020.38 | -8288.47 |
| ID approvals | 8535 | 8518 |
| ID rejections | 78544 | 78558 |
| Final gate input | 443 | 430 |
| Final gate passes | 58 | 58 |
| Final gate rejects | 385 | 372 |

Net P&L change: -268.09. BUY/SELL distribution is unchanged. Full ledger hashes differ; this is not a neutral change. No six-block expansion.

Input/configuration identity checks: {'params_sha256': True, 'protocol_sha256': True, 'slice_sha256': True, 'symbols': True, 'sessions': True}. Both workers printed zero-state receipts and ran as distinct processes. Gross minus friction equals net for both arms; final-gate input equals passes plus rejects.

ID rejection counters are aggregate diagnostics; this experiment does not establish exact rejection-reason accounting by side. No efficiency, profitability, or symmetry verdict follows from this single block.

Complete controller reasons are retained in each full_report.json. The isolated patch changes flat forward-outcome study grading only; it does not patch the ID RR formula or PA directional/log normalization.
