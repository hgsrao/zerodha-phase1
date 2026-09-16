# Pipeline V2 Phase 3 Hardening — Final Report

Generated: 2026-08-26T13:15:39.256523+05:30
Status: **PASS**

## Verification

- Authoritative tests: 72 passed, 0 failed (expected 72 passed).
- Frozen Certifier V2 identity unchanged: True.
- Production runners, Step-2, and trading operations were not invoked.
- Pipeline V2 was not frozen.

## SHA-256 audit trail

- `l2_canonical_columnar_pipeline_v2.py`: `03DEA6D91083FE2465955C5953C78976AC3031F444134A70DADCE4BCC7C9D872`
- `pipeline_v2_path_guard.py`: `14605A868FA681A2EE2959D69DE33DC095909BF64AF3C66AB63B8EAE1BCAF670`
- `test_l2_dataset_certifier_v2.py`: `B0DE7690274249399092C0AD76546C718253E25D6DFCFB1483C10B2ACFCE3014`
- `test_l2_dataset_certifier_v2_expanded.py`: `36C5B59455D9B4A92B1968B5C1A0320BD4946ADDF170335D754AFA03F1A48E24`
- `test_l2_canonical_columnar_pipeline_v2.py`: `1ED897ACD8B4B851FE44A49C34B19A56896E1EB7C606BCF20F39C39ED47EF5A2`
- `test_b1_2_v2_complete_suite.py`: `3BCBF13646448A16F976672873F53C9558751274827D84D7766BF615C9CC99C9`
- `test_b1_2_v2_decision_precedence_and_binding.py`: `02F862F7DA5670EE7D3450C9D8DE6B6D1B89631BF86A2FC1CBCA84051ED43A14`
- `test_l2_pipeline_v2_certification_binding.py`: `53A912CD418963B0FFFE7EDA2802301B7CBC367B1E07DC322CF0BA7AE378FB11`
- `l2_dataset_certifier_v2.py`: `0FBB4E8000D18F951C508DF179DD4A27D9A31F4D5A36C77F62A0D4DEAC7F0568`
- `L2_DATASET_CERTIFIER_V2_FREEZE_20260826.json`: `06B9AA57396D95FE1FD91E01E10B049DE9C08FFFA1DA56B42C2C8D556BF3E678`
- `L2_DATASET_CERTIFIER_V2_FREEZE_20260826.md`: `87D0DE27098DF64D6CFC40D578D86506A40B932B1B94632B223AC06375A2C1D2`
- `DIR_SPEC.md`: `C69D519EA5D146CB526F971D616C4FC563EBC3A20E0E996FBD234553D939C642`
- `entry_points_manifest.json`: `0352D151EC237BD2A9449277786FAD5C3EC0413178CCF62D690F90568304200E`

## Pytest output

```text
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-9.1.1, pluggy-1.6.0 -- C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN\.venv_polars_research\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN
configfile: pytest.ini
collecting ... collected 72 items

test_l2_dataset_certifier_v2.py::TestCertifierV2::test_valid_cycle_with_snapshots PASSED [  1%]
test_l2_dataset_certifier_v2.py::TestCertifierV2::test_malformed_json PASSED [  2%]
test_l2_dataset_certifier_v2.py::TestCertifierV2::test_missing_snapshots_dict PASSED [  4%]
test_l2_dataset_certifier_v2.py::TestCertifierV2::test_accounting_invariant PASSED [  5%]
test_l2_dataset_certifier_v2.py::TestCertifierV2::test_file_accounting PASSED [  6%]
test_l2_dataset_certifier_v2.py::TestCertifierV2::test_unknown_symbol PASSED [  8%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_exact_session_boundaries PASSED [  9%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_out_of_session_rejected PASSED [ 11%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_duplicate_timestamp PASSED [ 12%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_reversed_cycle_timestamps PASSED [ 13%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_all_48_authoritative_symbols PASSED [ 15%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_missing_single_symbol PASSED [ 16%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_unknown_symbol_added PASSED [ 18%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_symbol_missing_in_one_cycle_only PASSED [ 19%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_file_accounting_exact PASSED [ 20%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_snapshot_entry_accounting PASSED [ 22%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_depth_complete_five_levels PASSED [ 23%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_depth_sparse_legitimate PASSED [ 25%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_numeric_nonfinite PASSED [ 26%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_decision_pass_complete_session PASSED [ 27%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_decision_hold_incomplete_session PASSED [ 29%]
test_l2_dataset_certifier_v2_expanded.py::TestCertifierV2Expanded::test_decision_fail_malformed_json PASSED [ 30%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_one_complete_cycle_three_symbols PASSED [ 31%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_two_complete_cycles_four_symbols PASSED [ 33%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_sparse_symbol_coverage_no_fabrication PASSED [ 34%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_observation_timestamp_shared_across_cycle PASSED [ 36%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_three_timestamps_separate PASSED [ 37%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_malformed_json PASSED [ 38%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_malformed_one_symbol_doesnt_destroy_cycle PASSED [ 40%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_quality_codes_for_malformed_numeric PASSED [ 41%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_accounting_invariant PASSED [ 43%]
test_l2_canonical_columnar_pipeline_v2.py::TestCanonicalPipelineV2::test_raw_immutability PASSED [ 44%]
test_b1_2_v2_complete_suite.py::TestPipelineV2Expanded::test_48_symbol_cycle_exactly_48_rows PASSED [ 45%]
test_b1_2_v2_complete_suite.py::TestPipelineV2Expanded::test_two_cycles_96_rows PASSED [ 47%]
test_b1_2_v2_complete_suite.py::TestPipelineV2Expanded::test_sparse_cycle_no_fabrication PASSED [ 48%]
test_b1_2_v2_complete_suite.py::TestPipelineV2Expanded::test_three_timestamps_different PASSED [ 50%]
test_b1_2_v2_complete_suite.py::TestPipelineV2Expanded::test_malformed_symbol_doesnt_destroy_cycle PASSED [ 51%]
test_b1_2_v2_complete_suite.py::TestPipelineV2Expanded::test_quality_provenance_field_specific PASSED [ 52%]
test_b1_2_v2_complete_suite.py::TestCrossLayerIntegration::test_certifier_to_pipeline_end_to_end PASSED [ 54%]
test_b1_2_v2_complete_suite.py::TestCrossLayerIntegration::test_recorder_schema_contract PASSED [ 55%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_a_complete_clean_session_pass PASSED [ 56%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_b_complete_with_source_anomalies_pass_with_flags PASSED [ 58%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_c_one_cycle_only_hold PASSED [ 59%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_d_late_start_hold PASSED [ 61%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_e_early_stop_hold PASSED [ 62%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_f_missing_first_100_cycles_hold PASSED [ 63%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_g_missing_last_100_cycles_hold PASSED [ 65%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_h_internal_cycle_gap_hold PASSED [ 66%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_i_unrecoverable_corruption_fail PASSED [ 68%]
test_b1_2_v2_decision_precedence_and_binding.py::TestDecisionPrecedence::test_j_incomplete_with_anomalies_holds_not_flags PASSED [ 69%]
test_b1_2_v2_decision_precedence_and_binding.py::TestCertificationBinding::test_cert_artifact_exists_and_contains_raw_sha PASSED [ 70%]
test_b1_2_v2_decision_precedence_and_binding.py::TestCertificationBinding::test_pipeline_accepts_pass_certification PASSED [ 72%]
test_b1_2_v2_decision_precedence_and_binding.py::TestCertificationBinding::test_pipeline_accepts_pass_with_source_flags_certification PASSED [ 73%]
test_b1_2_v2_decision_precedence_and_binding.py::TestCertificationBinding::test_certifier_identity_sha256_preserved PASSED [ 75%]
test_l2_pipeline_v2_certification_binding.py::test_uncertified_paths_create_no_derived_outputs[missing] PASSED [ 76%]
test_l2_pipeline_v2_certification_binding.py::test_uncertified_paths_create_no_derived_outputs[hold] PASSED [ 77%]
test_l2_pipeline_v2_certification_binding.py::test_uncertified_paths_create_no_derived_outputs[raw_sha] PASSED [ 79%]
test_l2_pipeline_v2_certification_binding.py::test_uncertified_paths_create_no_derived_outputs[certifier_sha] PASSED [ 80%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_1_pass_certification_matching_sha_allow PASSED [ 81%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_2_pass_with_source_flags_allow_flags_preserved PASSED [ 83%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_3_hold_certification_block PASSED [ 84%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_4_fail_certification_block PASSED [ 86%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_5_missing_certification_real_data_blocked PASSED [ 87%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_6_unknown_decision_state_block PASSED [ 88%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_7_raw_sha_mismatch_block PASSED [ 90%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_8_wrong_certifier_source_sha_block PASSED [ 91%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_9_wrong_certifier_freeze_sha_block PASSED [ 93%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_10_superseded_v1_identity_blocked PASSED [ 94%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_11_tampered_certification_detected PASSED [ 95%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_12_correct_certified_allow PASSED [ 97%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_13_raw_file_changed_after_certification_block PASSED [ 98%]
test_l2_pipeline_v2_certification_binding.py::TestPipelineV2CertificationBinding::test_14_different_raw_file_same_session_date_block PASSED [100%]

============================= 72 passed in 5.47s ==============================
```
