# Pipeline V2 Phase 3 directory contract

This workspace uses its established flat-module layout. Phase 3 must not create a
second `pipeline_v2/` implementation tree or move frozen files.

Required production and frozen files at the workspace root:

- `l2_canonical_columnar_pipeline_v2.py` — sole public real-data Pipeline V2 API.
- `pipeline_v2_path_guard.py` — non-frozen path/certification policy.
- `l2_dataset_certifier_v2.py` — frozen Certifier V2; read-only.
- `L2_DATASET_CERTIFIER_V2_FREEZE_20260826.json` — sealed freeze record.
- `L2_DATASET_CERTIFIER_V2_FREEZE_20260826.md` — freeze documentation.

Required authoritative tests at the workspace root:

- `test_l2_dataset_certifier_v2.py`
- `test_l2_dataset_certifier_v2_expanded.py`
- `test_l2_canonical_columnar_pipeline_v2.py`
- `test_b1_2_v2_complete_suite.py`
- `test_b1_2_v2_decision_precedence_and_binding.py`
- `test_l2_pipeline_v2_certification_binding.py`

Phase 3 audit helpers live under `scripts/`. Generated evidence stays at the
workspace root. No production runner, Step-2 module, or trading component is in
scope.
