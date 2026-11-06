"""Backtest and calibration of the xPts and minutes models. NOT BUILT YET.

Rolling origin: for each GW g from 3 to the current GW, fit only on data available before the g
deadline, predict g, and compare to ``event/{g}/live`` actual points.
Report per position and overall: MAE, RMSE, Spearman rank correlation, top-k hit rate,
reliability curves (predicted vs. realised, binned), Brier score for P(start), P(60+),
P(CS) and P(DefCon), and comparison against naive baselines (season PPG, last-5 form).
Gate: the optimizer must not consume a model version whose calibration report has not been
generated and reviewed (see CLAUDE.md).
"""
