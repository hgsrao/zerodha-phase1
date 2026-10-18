import pandas as pd
from revision2_external.frozen_decile_analysis import FEATURES, analyze

def test_train_edges_are_retained_for_later_windows():
    rows=[]
    for split, values in (("train",range(10)),("validation",[0,9]),("test",[1,8])):
        for value in values:
            rows.append({"split":split,"label":"TARGET_FIRST" if value>5 else "STOP_FIRST","time_of_day":"09:30",**{k:float(value) for k in FEATURES}})
    report=analyze(pd.DataFrame(rows))
    assert report["features"]["range_atr"]["train_decile_edges"][0]==0.0
    assert len(report["features"]["range_atr"]["cohorts"])>0
