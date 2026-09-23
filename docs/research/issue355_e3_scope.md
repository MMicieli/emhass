# Issue #355 E3 research scope

This branch is an **offline Gate-E feasibility spike** only. It is not a production feature and must not be merged or enabled in a live EMS without a separate promotion decision.

## Frozen evidence basis

The discharge-loss surface is the frozen Gate A/B M4; no E3 controller or economic result may refit its coefficients.

For Gate-E decision relevance the branch models **incremental dispatch loss**, not a complete inverter auxiliary-load model:

- PV-only: exact released v0.18.3 behaviour.
- Charging: exact released v0.18.3 behaviour.
- Positive-PV discharge: `L(PV + discharge) - L(PV)`.
- Zero-PV discharge: `L(discharge) - L(0)`.

The independently observed approximately 145-159 W idle/auxiliary draw is deliberately excluded from the dispatch correction. It is not decision-dependent and its actuator/charging semantics are not yet proven. A future full physical model may require a separate AC-balance auxiliary term.

## Architecture invariants

Derived from project issues #313 and #308:

- EMHASS remains the sole optimiser.
- No Package18 compensation.
- No post-solve battery correction.
- No tracker correction.
- No widening of Sigenergy 40034.
- No second Home Assistant controller.
- Existing PV and load forecast semantics are unchanged.
- Feature absent/false must reproduce released v0.18.3.
- Replay results may measure decision/economic materiality but must not retune the frozen loss model.
- Production promotion is a separate later gate.

## Validated / unsupported domains

The frozen daylight candidate has retrospective support across the zero-to-daylight transition, with known residual bias around approximately 0.5-3 kW PV proxy retained as uncertainty rather than fitted away.

The research result surface must explicitly flag:

- discharge intervals with total `PV + discharge > 15 kW`;
- simultaneous PV curtailment and battery discharge.

Those regions are not promoted to validated physics by the continuity tail used to keep the offline MILP solvable.

## E3 acceptance questions

E3 answers only:

1. Does the feature-off path preserve exact baseline behaviour?
2. Can the frozen incremental PWL be represented reliably in native EMHASS?
3. What variable/constraint and HiGHS runtime burden does it add at H288?
4. If technically reliable, does a paired replay show material first-action, SOC, import/export, throughput or economic consequences?

No production change is authorised by this branch.
