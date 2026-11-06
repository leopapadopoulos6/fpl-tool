"""Chip planner. NOT BUILT YET.

For each remaining chip (from ``fpl.state.available_chips``) and each GW in its window,
re-solve the MILP with the chip forced in that GW and compare with the best no-chip plan
over the same horizon -> table of expected gain per (chip, GW) with confidence bands.
Must respect: one chip per GW, FH not in consecutive GWs, first-half chips expire after the
GW19 deadline (2027-01-01 18:30 UTC this season), WC/FH retain banked FTs.
Bench Boost value depends heavily on the minutes model (joint P all 15 play).
"""
