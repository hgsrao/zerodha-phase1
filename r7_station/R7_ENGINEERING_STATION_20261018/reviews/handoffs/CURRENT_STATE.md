# R7 Multi-Model Current State

Updated: 2026-10-18

## Authoritative locations

- Active Git repository: `/home/srinivas/projects/zerodha-r6-no-adaptation`
- Active branch: `r7-governor-corrections`
- Consolidated station: `/home/srinivas/Documents/Codex/2026-10-17/referenced-chatgpt-conversation-this-is-an/outputs/R7_ENGINEERING_STATION_20261018`
- Main safety condition: `LIVE_TRADING_ENABLED=False`

## Model status

- ChatGPT/Codex: engineering coordination and consolidation.
- Claude: dispatcher preflight completed read-only with NO-GO; local Claude Code execution audit was started separately.
- Gemini: independent review workspace is preserved under `reviews/gemini/`.
- Qwen 3.8: isolated workspace and OpenHands review are preserved under `reviews/qwen_38/`; block-by-block execution is the approved workflow.

## Change policy

Model findings and proposed patches remain isolated until reviewed. No model may modify the main R7 branch or enable live trading without explicit approval.
