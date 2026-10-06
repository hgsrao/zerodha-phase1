# D02 inspection gate

Scope: reproduction only. No production edits or Claude edit authorization.

Branch: `feature/engine-ab-handoff-lifecycle`. HEAD: `411712afaecaeb44a6899a12db94ac547fef0186`. The checkout is dirty; source hashes and the observed dirty tree are preserved in `inspection_receipt.json`. Existing modifications are retained.

Independent D01 verification: the two existing component/integration test modules passed: **12 passed, 32 warnings in 5.14s**. Integration coverage is one TITAN session and SELL; this is not full Block 1 certification.

The reports mention D02 but do not define it. The activity register separately defines F-N02 and the acceptance audit names R5-D-STALE-CNC: conversion to CNC, flattening, then a new entry inherits CNC. Actual source inspection corroborates the suspected mechanism: `_apply_fill_to_position` reuses the symbol dictionary; `place_order` uses `setdefault` for MIS; `ensure_protection` rejects a product mismatch before `register_fill` persists its record. This is a source finding, not a newly executed D02 reproduction.

D02 reproduction remains pending clarification of its identity. No mapping from D02 to stale CNC is assumed.
