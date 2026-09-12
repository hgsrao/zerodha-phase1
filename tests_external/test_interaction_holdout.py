from revision2_external.interaction_holdout import select_high_vol_morning,summarize

def test_interaction_applies_frozen_threshold_and_fixed_time_window():
    rows=[{"realized_vol_20":3,"time_of_day":"10:20","label":"TARGET_FIRST"},{"realized_vol_20":1,"time_of_day":"10:20","label":"STOP_FIRST"},{"realized_vol_20":3,"time_of_day":"12:00","label":"STOP_FIRST"}]
    chosen=select_high_vol_morning(rows,2)
    assert len(chosen)==1 and summarize(chosen)["target_rate"]==1.0
