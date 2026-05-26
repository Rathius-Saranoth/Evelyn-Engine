---
title: Notes to Live By (Tech Version).md
date created: 2026-04-09T21:05:13-05:00
date modified: 2026-05-25 19:59:28
tags: markdown, reference, notes, format, best-practices
---

## Notes to Live By (Tech Version)

**To:** Self + AI Agents

**Source:** [Dave's Garage — "Why your NEW computer is SLOWER than your OLD computer"](https://www.youtube.com/watch?v=t992ul_IKtc)

**Theme:** Old software had taste imposed on it by reality. Today we must impose that taste on ourselves.

## A Standard for Things Worth Building

Modern hardware is so far ahead of its software that a machine from today, sent back to 1990, would look like government technology. Yet that machine takes longer to open a chat window than a 486 took to boot and get actual work done. The problem is not that engineers got worse. The problem is that incentives got worse, constraints got looser, and standards quietly slid from excellent to acceptable.

How we got here — the four deteriorations Dave names

**01 - Abstraction without cost accounting**

- Every layer made sense to somebody in isolation. Collectively they form what Dave calls "performance sedimentary rock." The old discipline: if you add a layer, you feel the cost. The new habit: you assume somebody else's hardware will eat the bill — until the bill reaches the user.

**02 - Business incentives misaligned**

- Nobody gets a standing ovation for cutting startup time in half. Feature work gets promotions; performance work gets a polite nod in the retrospective. When nobody owns end-to-end responsiveness, the user just experiences it as "the app feels big and heavy" — a phrase that maps to no single backlog item and gets neglected for years.

**03 - Loss of whole-system understanding**

- We taught people how to ship. We did not always teach them how to make the machine sing. Cache behavior, allocator pressure, N+1 query patterns, branch prediction — these aren't exotic knowledge, they're the literacy that used to be mandatory. The Windows 95 clock has no seconds display because someone actually measured the RAM cost of painting it once per second and cut it.

**04 - Dependency explosion**

- Software today is more assembled than written. The assumption flipped: justify inclusion used to be the default; now it's pull it in first, worry later. Every dependency brings not just functionality but startup cost, memory use, security exposure, update churn, and nested trees you'll never fully understand.

---

## The AI Amplifier

AI reflects the median patterns of its training data. Median code is plausible, verbose, layered, defensive. It solves the stated problem in a recognizable way — not necessarily the best way. Dave's own example: AI-written video preview code that was transmitting frames as base64-encoded JSON across a perfectly good socket interface. Beautifully structured. Catastrophically inefficient. Passed every test.

**The real danger** is not the one awful routine everyone spots. It is a thousand slightly over-abstracted, slightly over-allocating, slightly over-dependent chunks that all look reasonable in review, all pass the tests, and together add up to a product that is bloated, battery-hungry, and mysteriously hard to speed up. A mediocre developer produces mediocre code at human speed. AI produces it at industrial speed.

---

## Part I — Is it… (the Quality gates)

### Correct

Does it do what it claims — fully, not approximately? A unit test passing tells you the code is functionally acceptable. It tells you almost nothing about whether it is elegant, maintainable, efficient, or respectful of the user's machine and time. Correctness is the floor, not the finish line.

**Dave:** "Stop pretending correctness is the whole game."

### Elegant

The right abstraction can make an entire class of bugs disappear and let a small team build what once required an army. But abstractions are never free — they hide complexity by moving it to your CPU, memory subsystem, battery, or network. Elegance means the abstraction earns its cost.

**Test:** Do you know what you're paying for every layer you've added?

### Efficient

Every line of code has mass of some kind. A cache miss isn't academic — it's the difference between smooth and sticky. A memory allocation inside the wrong loop isn't probably fine — it's a stutter on screen or a support call. Don't parse the same thing twice. Don't copy buffers around just because the architecture diagram looked cleaner that way.

**Dave:** "Every line of code has mass of some kind."

### Maintainable

Will a future reader understand the intent without decoding the execution? Maintainability is not comments — it is structure, naming, and separation of concerns. It's also the thing that prevents a thousand small sloppiness decisions from compounding invisibly over time.

**Red flag:** Logic that can only be safely changed by whoever wrote it.

### Sharp, Responsive, and Respectful

Latency is a bug. Idle CPU is suspicious. Startup time really matters. Software should feel immediate — not merely survivable. Every keypress, tap, or request is a moment of trust. "Well, it doesn't suck on the laptop once it's warmed up" is not a standard. As Dave puts it, that's just an apology with a budget attached. Faster hardware should make the experience feel luxurious, not merely functional.

**Dave:** "Software that honors the absurd power of the hardware beneath it instead of casually burning it for warmth."

---

## Part II — Does it… (the Operational disciplines)

### Check Hot Paths

Hot paths deserve human attention — not assumption, not guesswork. They are simply your most-traveled code. Back when CPUs were thousands of times slower, a page fault was something you felt. A cache miss was the difference between smooth and sticky. That instinct doesn't expire just because the hardware got faster. Know where your hot paths are. Treat them differently from the rest of the code.

**Dave:** Fast smoke-test benchmarks on every merge for hot paths. Deeper scenario benchmarks on dedicated hardware nightly.

### Include Profiling and Regression Detection

Performance must be a first-class build artifact, just like correctness. If you can fail a build because a unit test broke, you can fail a build because startup regressed 18% or idle memory grew 300MB for no user-visible gain. These numbers should be visible, historical, and gated — not mysterious figures someone checks when there's a fire.

**Track:** startup time, steady-state memory, key transaction latency, page load, allocation counts, battery impact, idle CPU, network roundtrip time, database query counts.

### Minimize Dependencies

A dependency should answer a simple question: what does this buy the user, and what does it cost them — not the team, the user. If the answer is "it makes this faster to ship," be honest that you are spending the user's RAM and battery to save your schedule. Sometimes that trade is worth it. Often it is not. We should at least make people say it out loud.

**Dave:** "A dependency is treated like a liability until proven useful."

### Treat System Resources as a Scarcity

Memory had a budget. Startup time had a budget. Disk footprint had a budget. CPU usage had a budget. If a feature blew the budget, it didn't ship — or something else got cut. There was no casual assumption that hardware would float all the boats. That discipline didn't expire when RAM got cheap. It became voluntary. Voluntary is harder. Do it anyway.

**Dave's explicit budgets:** x ms max cold start · y MB max idle memory · z CPU wakeups per minute · bounded network requests, not an unbounded party of retries.

---

## Standing Axioms — Directly from the Talk

> Every line of code has mass of some kind.  
> — Dave, on what constraint-era programming taught you to feel

> Constraints are not the enemy of creativity. They are what keeps creativity from turning into sprawl. They promote the best kinds of creativity — the kind required to make apparent miracles happen.  
> — Dave, on why budgets are the right tool

> Old software had taste imposed on it by reality. Today we have to impose that taste on ourselves.  
> — Dave, closing argument

> AI will write you a beautifully structured little catastrophe if you let it.  
> — Dave, on why profiling and regression detection matter even more with AI-generated code

> The answer is not to go backwards. It is to carry forward the right instincts from an era of scarcity into an era of abundance.  
> — Dave, on what good software actually looks like now
