---
description: A structured self-review checklist based on the "Notes to Live By" engineering standards
---

# Quality Review Workflow

Run this after completing any significant feature or refactor.

## 1. Quality Gates

Walk through each gate for the code you just wrote or modified:

- [ ] **Correct** — Does it do what it claims, fully?
- [ ] **Elegant** — Does every abstraction earn its cost? Can you name the cost?
- [ ] **Efficient** — Any redundant parsing, unnecessary copies, or hot-loop allocations?
- [ ] **Maintainable** — Will a future reader understand intent without decoding execution?
- [ ] **Responsive** — Did you measure or estimate the latency impact? Is startup affected?

## 2. Operational Disciplines

- [ ] **Hot Paths** — Did you identify which paths are performance-critical? Did they get special attention?
- [ ] **Profiling** — Did you measure before and after? Can you state the delta?
- [ ] **Dependencies** — Did you add any new packages? For each: what does it buy the *user*, and what does it cost?
- [ ] **Resource Budget** — Memory, CPU, startup, network: did any budget move? Is the move justified?

## 3. AI Amplifier Check

- [ ] Review AI-generated code for median-pattern traps: over-abstraction, unnecessary serialization, dependency bloat
- [ ] Confirm no "beautifully structured catastrophe" snuck through

## 4. Verdict

If any gate failed, fix before merge. Document trade-offs for intentional violations.
