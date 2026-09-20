---
title: vault-tag-taxonomy.md
description: Faceted Classification schema, facet axes, format standard, and vocabulary-control rules governing all tag curation across the vault and memory as one structure.
tags: [obsidian, pkm, taxonomy, faceted-classification, tagging, style-guide, authority-control]
date created: 2026-09-19 00:00:00
date modified: 2026-09-20 12:07:07
---

# 🏛️ Vault Tag Taxonomy & Faceted Classification Standard

> [!ABSTRACT] Executive Summary
> The vault is a **library catalog**, not a folder tree. Every note is classified across independent, orthogonal facets, governed by a controlled vocabulary under formal authority control. This document defines the schema, the format, and the rules of engagement for every librarian module that touches a tag.

> [!IMPORTANT] Authority of this document
> This is the **intent layer**. It governs `tag_librarian.py`, its prompts, its database schema, and any future taxonomy tooling. Where an implementation disagrees with this document, the implementation is wrong.

---

## 🌐 0. Scope — One Structure, One Vocabulary

> [!IMPORTANT] The vault and memory are a single knowledge structure.
> They are two storage substrates for one mind, not two systems. A tag is a **concept**, and a
> concept does not change meaning according to where it is applied — `tech/gis` denotes the same
> thing on a note and on a memory fact. **One controlled vocabulary governs both.** Divergence
> between them requires explicit structural justification, recorded here; it is never incidental.

This follows directly from the standards in §0.1: Getty's authority files, SKOS concept schemes and
FAST all define a vocabulary independently of the resources that use it. Two vocabularies for one
mind is the mechanism by which drift occurs, not a neutral implementation detail.

### 0.1 Where divergence IS justified
Only three, each because the axis is structurally inapplicable rather than merely unused:

| Divergence | Why it is structural |
|---|---|
| **`type/` facet and the §4 class profiles** | A memory fact has no document form. It is not a reference doc or a journal entry, so the form axis — and everything gated on it — does not apply. |
| **`obsidian-graph/*`** | Graph-view flags are vault mechanics. A memory fact is not a node in the Obsidian graph. Memory's administrative namespace is `status/*` instead. |
| **Fast-memory categories (`Cat##-U` / `Cat##-A`)** | A separate classification system in memory, orthogonal to tags. Not part of this taxonomy and not governed by it. |

Everything else — the domain, motif, setting, event and time axes, the format standard (§5), and the
whole of vocabulary control (§6) — applies identically to both.

---

## 📚 0.2 Standards Basis

The schema is assembled from established knowledge-organization standards rather than invented. Each is borrowed for one specific job:

| Standard | Borrowed for |
|---|---|
| **Ranganathan — Colon Classification / PMEST** | The founding principle: orthogonal facets, not a single hierarchy |
| **FAST** (OCLC + Library of Congress) | The pragmatic facet roster — Topic, Place, Time, **Event**, Person, Corporate Body, Title, Form/Genre — and the proof that a faceted vocabulary can be machine-applied at scale. The Event facet is adopted directly (§3.7) |
| **Iconclass** (RKD) | Precedent for **motif as a first-class, open classification** of "subjects, themes and motifs" — ~28,000 definitions across 10 divisions, including *5 Abstract Ideas and Concepts* |
| **DCMI Type Vocabulary** | The canonical, closed list of resource types — supplies the media sub-typing layer |
| **NISO metadata classes** (descriptive / structural / administrative) | The separation that keeps graph-control tags out of the subject catalog |
| **ISO 25964-1** (with ANSI/NISO Z39.19) | The thesaurus relationship model — `USE`/`UF`, `BT`/`NT`, `RT` — a formal data model for it, and the number-form rule (§6.3.2) |
| **Getty AAT / TGN / ULAN** | The authority-file-per-entity-type pattern: concepts, places, and persons get *separate* registries |
| **SKOS / OWL** | Concept vs. Individual — the tags-vs-links boundary (§2) |
| **CIDOC-CRM** | Typed semantic relationships for the link layer, where a plain link is too vague |
| **EDTF / ISO 8601-2:2019** (Library of Congress) | Date semantics — reduced precision vs. unspecified digits (§3.8) |

> [!NOTE] What is deliberately not adopted
> Full RDF/OWL serialization, METS structural encoding, and PREMIS preservation metadata are out of scope. This is a working vault, not a repository — the schema borrows their *distinctions*, not their file formats.

---

## 🎯 1. The Librarian Mandate (Intent)

The tag system exists to answer catalog questions, not to decorate notes. The governing test for any tag:

> **The Catalog Test** — *"If someone searches the catalog for this term, is this a document they should legitimately find?"*

The audit pass is a **curator**, not a rewriter. Its four duties, in order:

1. **Review for appropriateness** — does each existing tag survive the Catalog Test?
2. **Review for format** — does it conform to the format standard in §5?
3. **Collapse flat-dashed synonyms** — map colloquial variants onto one canonical term.
4. **Build the domain/subdomain tree** — organize surviving terms against the Master Taxonomy.

**Non-negotiable**: the pass evaluates and refines what is already there. It does not replace a note's tag set with a freshly invented one.

### 1.1 Provenance — existing tags carry no curatorial authority
The overwhelming majority of the current vocabulary was produced by automated passes and bulk vault-wide sweeps, not by hand. Hand-applied tags exist but are a small, deliberate minority.

This has a direct consequence, and it is the reason §7 and §9 define **two distinct operating modes**: the existing corpus is *evidence of what the vault is about*, not a curated artifact deserving preservation for its own sake. Conservatism in the steady-state pass is a **safety control against unsupervised drift**, not reverence for machine-generated tags.

---

## 🧭 2. First Principle — Concepts Are Tags, Individuals Are Links

The single most important boundary in the schema. It follows the SKOS *Concept* vs. ontology *Individual* distinction, and mirrors Getty's separation of AAT (concepts) from TGN (places) and ULAN (persons).

| | **Tag** | **Wikilink** |
|---|---|---|
| **Represents** | A class, kind, or concept | A named individual / instance |
| **Answers** | "What *kind* of thing is this about?" | "*Which* thing is this about?" |
| **Authority record** | Row in `master_tag_taxonomy` | The entity's own note |
| **Examples** | `tech/gis`, `motif/combat`, `type/overview` | `[[Jane Doe]]`, `[[Kansas]]`, `[[Elden Ring]]` |
| **Owned by** | `tag_librarian.py` | `link_librarian.py` |

**Consequences:**
- There is **no entity facet**. Named people, places, works, and organizations are never tags. They are links, and the link graph is the relational layer of the schema.
- A tag naming a specific individual is a **defect**. It is converted to a link, not preserved.

> [!TIP] Reconciling this with FAST
> FAST *does* carry Person, Corporate Body, Place, and Title as subject facets — but it backs each with a name-authority file. In an Obsidian vault the entity's own note **is** that authority file. The two models agree; only the storage differs.

---

## 🗂️ 3. The Facet Axes

Facets are **orthogonal**. A note sits at their intersection; it never has to choose between them.

### 3.1 Descriptive axes — the subject catalog

| Axis | Namespace | Cardinality | Governs |
|---|---|---|---|
| **Domain** | *bare-rooted* (`tech/gis`) | 1 or more | Subject matter — what the document is *about* |
| **Type** | `type/` | exactly 1 | Form / class of the document itself |
| **Motif** | `motif/` | 0 or more | Recurring theme, symbol, or felt quality |
| **Setting** | `setting/` | 0 or more | Conceptual space the content occupies |
| **Event** | `event/` | 0 or more | Kind of occurrence the document is anchored to |
| **Time** | `CY-YYYY/MM/DD` | 0 or 1 | Chronological anchor (protected) |

### 3.2 Administrative axis — vault mechanics, not subject matter

| Axis | Namespace | Cardinality | Governs |
|---|---|---|---|
| **Graph / operational** | `obsidian-graph/` | 0 or more | Graph filtering, view control, processing flags |

Per the NISO descriptive/structural/administrative split, these are **administrative metadata**. They describe how the vault *handles* a note, not what the note is about. They are therefore:

- **Excluded from the Catalog Test** — they are not subject terms and must never be judged as such.
- **Never proposed, removed, or reformatted by the semantic audit pass.** The classifier does not see them and has no authority over them.
- **Flat by design.** No semantic subdomains.

Canonical values: `obsidian-graph/contact` (a person actually interacted with, as distinct from any other entity), `obsidian-graph/no-graph` (exclude from graph view). Others may be added operationally.

Two further operational namespaces already exist and are protected under the same rule: `status/*` and `kanban*`. They are administrative by the same logic and are likewise invisible to the classifier.

> [!WARNING] This axis is a firewall
> Mixing operational flags into the descriptive vocabulary is what made `contact/*` look like a subject facet with a role hierarchy. It is not one. Keeping the namespaces separate prevents the classifier from ever reasoning about them.

### 3.3 Domain — the subject axis
- **Bare-rooted.** No wrapper prefix. A document about GIS is `tech/gis`, never `topic/gis` and never `work/gis`.
- **Employment is its own domain, not a wrapper.** A document about professional duties gets `work/duties`. A GIS document produced at a job is still `tech/gis` — the librarian shelves the book by its subject, not by its author's employer. Both may apply simultaneously; that is the point of facets.
- **Multi-topic documents receive one domain tag per genuine subject area.** Collapsing a broad reference document into fewer tags than it has subjects is data loss, not tidying.
- Maximum depth **3** (`domain/subdomain/leaf`).

### 3.4 Type — the form axis
Exactly one per document. It is **separate from subject** and never mixed into the domain tree. This axis also determines the document's **class**, which gates the conditional facets (§4).

Canonical values: `type/overview`, `type/reference`, `type/guide`, `type/manual`, `type/journal-entry`, `type/dream`, `type/creative`, `type/media`, `type/recipe`, `type/list`, `type/log`, `type/notes`, `type/report`, `type/moc`.

**Media sub-typing** uses the DCMI Type Vocabulary as its closed second level:

```text
type/media/sound            type/media/moving-image
type/media/still-image      type/media/text
type/media/interactive      type/media/software
type/media/dataset          type/media/physical-object
```

Sub-typing is **required** on `type/media` and unavailable elsewhere. It answers "what form is the work?" — the domain axis still answers what it is about, and motif still answers what recurs in it.

### 3.5 Motif — the theme axis *(conditional)*
The retrieval key for experiential and creative material. Its purpose is the query:

> *"I dream about this a lot — what else in my vault connects to it?"*

- Applies to **dream, creative, and media** classes. **Forbidden on reference material**, where the domain is already the retrieval key.
- **Open vocabulary.** Motif grows through the §6.1 proposal queue like any other axis. This follows Iconclass, which expanded to ~28,000 iconographic definitions precisely because a closed motif list cannot anticipate what recurs in a body of work.
- **A single occurrence is sufficient.** Motif is explicitly **exempt from the §6.3 subsumption threshold**. A dream may be the only one of its kind ever recorded and still deserve its motif — rarity is not irrelevance, and the whole point of the axis is to make such a note findable later. The §6.1 proposal queue is the quality control; a volume threshold would suppress exactly the material the axis exists to capture.
- What disqualifies a motif is being *incidental*, not being rare: a detail the document merely mentions is not a motif. A motif is an element the document is thematically *about*.
- Depth **2** maximum (`motif/combat`, `motif/loss/grief`).

### 3.6 Setting — the conceptual-space axis *(conditional)*
Holds **kinds of space**, never named places.

- `setting/biome/tropical`, `setting/biome/supermarket`, `setting/urban`, `setting/wilderness` — legitimate: these are classes.
- `setting/place/usa/kansas` — **not legitimate**. Kansas is a named individual and becomes `[[Kansas]]` per §2.
- Applies to the same classes as motif.

### 3.7 Event — the occurrence axis *(conditional)*
Adopted from FAST's Event facet, adapted to the §2 boundary. It supplies life-context readable from the frontmatter alone, without opening the note.

- Holds **kinds of occurrence**, not named events: `event/surgery`, `event/funeral`, `event/move`, `event/job-change`, `event/travel`.
- A **named** event is an individual and becomes a link — `[[Grandmother's Funeral 2019]]`, not `event/grandmothers-funeral-2019`.
- **Orthogonal to Time**: `event/` answers *what kind of occurrence*; `CY-` answers *when*. Both may apply.
- **Not a subject tag.** A document *about* funeral customs as a topic takes a domain tag (`culture/death-rites`); the event facet means the document is *anchored to* an occurrence of that kind.
- Depth **2** maximum.

### 3.8 Time — the chronological anchor
**Protected**: never lowercased, never removed, never proposed for removal by any pass. It is the sole exemption from §5.

Date tags follow **EDTF (ISO 8601-2:2019)** semantics, which distinguishes two things a naive scheme conflates:

| Form | Meaning | Example |
|---|---|---|
| `CY-YYYY/MM/DD` | Full date | `CY-2026/09/19` |
| `CY-YYYY/MM` | **Reduced precision** — anchored to the month; no day was ever intended | `CY-2026/05` |
| `CY-YYYY` | Reduced precision — anchored to the year | `CY-2025` |
| `X` in any position | **Unspecified digit** — a value exists but is unknown | `CY-XXXX/11/16` (November 16, year unknown) |

- **Reduced precision and unspecified digits are not interchangeable.** `CY-2026/05` asserts "May 2026"; `CY-2026/05/XX` asserts "a specific day in May 2026 that we do not know." Use the form that is actually true.
- `X` is uppercase per EDTF. Lowercase `u` was the superseded draft syntax and is not accepted.
- Pattern: `^CY-[0-9X]{4}(/[0-9X]{2}){0,2}$`

---

## 📋 4. Document Classes & Facet Profiles

The pass determines the document's **class first**; the class then decides which facets are required, permitted, or forbidden. Motif is not a global facet — it is earned by class.

| Class (`type/`) | Domain | Motif | Setting | Event | Time |
|---|---|---|---|---|---|
| `reference`, `guide`, `manual` | **required** | ⛔ forbidden | ⛔ forbidden | ⛔ forbidden | optional |
| `overview`, `moc`, `list` | **required** | ⛔ forbidden | ⛔ forbidden | ⛔ forbidden | optional |
| `log`, `report` | **required** | ⛔ forbidden | ⛔ forbidden | optional | **required** |
| `journal-entry` | **required** | optional | optional | optional | **required** |
| `dream` | **required** | **required** | **required** | optional | **required** |
| `creative` | **required** | **required** | optional | optional | optional |
| `media` | **required** | **required** | optional | optional | optional |
| `recipe`, `notes` | **required** | ⛔ forbidden | ⛔ forbidden | ⛔ forbidden | optional |

**"Forbidden" is enforced, not advisory.** A motif tag proposed on a reference document is rejected before it reaches disk.

The administrative axis (§3.2) is **outside this table entirely** — it applies to any class and is never gated.

---

## ✍️ 5. Format Standard

One rule, no exceptions:

> **Lowercase always. Hyphens join words. Slashes join levels.**

```text
tech/gis                     gov/ng911
motif/combat                 setting/biome/tropical
type/journal-entry           type/media/moving-image
3d-modeling/uv-mapping       obsidian-graph/contact
```

- **No casing branch exists.** Proper nouns follow the same rule as concepts — and per §2 most of them should be links anyway. The absence of a branch is the feature: there is no decision to get wrong, and therefore no way to fork `ai` from `Ai`.
- Acronyms are lowercased (`gis`, `ng911`, `uv-mapping`). Display casing is Obsidian's concern, not the catalog's.
- No spaces, no underscores, no trailing/leading slashes.
- Maximum depth **3** levels on any axis.
- `CY-YYYY/MM/DD` is exempt from all of the above.

---

## 🔐 6. Vocabulary Control

### 6.1 Authority Control — who may mint a master tag
**Subject Indexing and Authority Control are separate processes and must not share a call.**

- **The registry is shared.** `master_tag_taxonomy` is neither vault data nor memory data — it governs both, so it lives in a store that both subsystems read and propose against. A taxonomy owned by one substrate would make the other's vocabulary ungoverned by construction, which is exactly the state §8 records.
- The per-document audit pass may **only apply tags that already exist** in the registry.
- A term not in the taxonomy is emitted as a **proposal**, queued for human approval. It is never written to a note or to the taxonomy by the document pass.
- This is the direct control on orphan growth: a vocabulary that any single document can extend is not a controlled vocabulary.

**Every proposal must carry its own evidence.** A bare term is not reviewable — approving it means guessing whether an equivalent already exists among thousands. A proposal record therefore includes:

| Field | Purpose |
|---|---|
| Proposed term | The candidate, already in §5 format |
| Target axis | Which facet it would join — determines the rules that apply |
| **Nearest existing terms** | Top 3–5 semantic neighbours **with distance scores**, from the Tag RAG index |
| Source document(s) | What triggered it, so the judgement can be made in context |
| Suggested parent | The `BT` it would hang from, if any |

The nearest-neighbour field is the decisive one: it converts approval from a recall problem into a comparison. If the closest existing term is near, the answer is usually "use that instead" — which is a `UF` mapping (§6.2), not a new term. If everything is far, the term is genuinely novel. The distance machinery for this already exists in the Tag RAG retrieval path.

### 6.2 Equivalence Mapping (`UF` / "Used For")
Per ISO 25964-1 and Z39.19. When flat synonyms collapse onto a canonical term, the collapse is **recorded, not merely executed**:

```text
tailor-size-guide            ─┐
custom-clothing-measurements ─┤
clothing-fit-details         ─┼─ UF ──▶ craft/tailoring
body-dimensions-for-suits    ─┤
sewing-measurements-list     ─┘
```

A deleted synonym with no `UF` record will be re-minted by the next import. The alias is what makes the collapse permanent, and it doubles as a retrieval expansion for RAG.

### 6.3 Subsumption (`BT`/`NT`) — when a subdomain may branch
A subdomain is created only when the volume justifies it. A branch carrying a single document is noise.

- Threshold: **≥5 documents** sharing a prospective narrower term before it branches from its parent.
- Below threshold, the document takes the parent term.
- **Motif is exempt** (§3.5). The threshold governs when a branch *splits*, not whether a theme is worth naming.

> [!WARNING] The threshold governs branching, never whether a category may exist
> Child count measures how much of a category has been **classified so far** — not whether the
> category is legitimate. `routine` holding one term is still plainly a domain: the vault contains
> many routines that nothing has tagged yet. Dismantling sparse namespaces destroys exactly the
> categories that are about to fill, and it does so silently, because the evidence that they were
> real is the material that has not been classified.
>
> A sparse root is therefore only altered when its **shape** marks it as an artifact rather than a
> category — a compound whose head is an established root (`ai-behavior` where `ai` holds 128 terms)
> is a missed nesting, and a conjunction (`preference-and-interest`) is two categories a generator
> could not choose between. A recognisable single word keeps its namespace no matter how little sits
> under it.

### 6.3.1 The sibling test — when a compound should become a level
`personal-growth` or `personal/growth`? `me-cfs` or `me/cfs`? The question recurs constantly
and reads as a matter of taste, but it has a measurable answer.

> **A hierarchy level must have siblings.** If the leading token of a compound already acts as a
> namespace elsewhere — other terms live under it — then it is a real level and the term nests.
> If nothing else lives under it, the compound is a single term of art and stays flat.

| Term | Level tested | Children it has | Verdict |
|---|---|---|---|
| `work-stress` | `work` | 409 | nest → `work/stress` |
| `system-design` | `system` | 285 | nest → `system/design` |
| `me-cfs` | `me` | 1 | keep `me-cfs` — ME/CFS is one disease name |
| `health/self-care-routine` | `health/self` | 0 | keep `self-care` — a lexicalized compound |

This is why the choice *feels* intuitive but resists explanation: the real question is whether the
first word means anything on its own **in that position**. Sibling count is the observable proxy.

Measured across this vault's 122 flat-vs-nested pairs, the test resolved 117 to nested and 5 to
compound — and the five it spared were precisely the terms of art.

### 6.3.2 Number form — singular, by invoked exception
ANSI/NISO Z39.19-2005 §6.5 splits nouns by countability: **count nouns** (those answering "How
many?") *"should normally be expressed as plurals"*, while **mass nouns** ("How much?") take the
singular. By the default rule, `dreams`, `projects` and `goals` would all be plural.

**This vault uses the singular throughout**, invoking the exception the standard provides at
§6.5.1.1: *"If in the domain of the controlled vocabulary there is literary or user warrant for the
expression of count nouns in the singular, establishment of terms in that form is acceptable."* The
standard's own examples are body parts in biomedicine and objects in a museum catalog.

The warrant here is demonstrated, not assumed: across 161 singular/plural pairs in this corpus, the
singular was the established form in 124. A personal knowledge vault behaves like a museum catalog —
each note is a unique item, and `dream` marks *"this note concerns a dream"* rather than counting
dreams.

> [!IMPORTANT] This is a deliberate deviation, recorded as one
> Choosing singular is standards-compliant **because the exception is invoked knowingly**, not
> because usage happened to fall that way. A vocabulary that quietly diverges from the standard it
> cites is worse than one that never cited it. Any future pass that "corrects" terms to plural for
> conformance is undoing a decision, not fixing a defect.

### 6.3.3 Literary warrant — a category is earned in the prose, not in the tags
When deciding whether a sparse namespace is real, **count how often its word appears in the vault's
own writing**, not how many terms have been filed under it.

This is *literary warrant* in the sense Z39.19 §6.5.1.1 uses the phrase: a term belongs in a
controlled vocabulary because the domain's literature uses it. Tag population measures something
different — how much classification has happened so far — and using it as the test inverts the
problem:

| Root | Terms tagged under it | Times written in the vault | Verdict |
|---|---|---|---|
| `architecture` | 1 | 1,697 | keep — a category nobody has finished filing |
| `database` | 1 | 860 | keep |
| `analysis` | 1 | 841 | keep |
| `social-relations` | 1 | 0 | dismantle — a generated label |
| `pet-name` | 1 | 0 | dismantle |
| `food-prep` | 1 | 0 | dismantle |

> [!IMPORTANT] Population is evidence of past work, not of legitimacy
> A root tagged once and written 800 times is the *strongest* case for keeping a namespace, not the
> weakest — the gap between the two numbers is unclassified material, which is the condition this
> whole migration exists to fix. Judging by population deletes precisely the categories about to
> fill, and does it silently, because the evidence they were real is the material nobody tagged yet.

**Measurement rules that matter:**
- **Compounds are matched as phrases.** Searching for `mental-state` as a hyphenated token finds
  nothing, since prose writes "mental state". Treating that zero as evidence would condemn every
  multi-word root by construction.
- **Entities score −1 regardless of frequency.** The operator's name appears 1,481 times and is
  still not a domain (§2).
- **Unknown roots are kept.** Absent evidence, the safe default is to preserve; dismantling without
  warrant data is the failure mode this section exists to prevent.
- **Dismantling never yields a long flat compound.** Two-segment terms collapse; deeper ones drop
  the junk root and keep their structure. A 50-character flat tag is one nobody would ever search.

> [!NOTE] Warrant measures the word, not the topic
> This test is lexical. A theme genuinely present in the vault can score zero because it is written
> in other words — `astronomy` appears nowhere, yet notes about stars and space plainly exist, and
> two voices writing the same corpus (one plain, one ornate) widen that gap further. Warrant 0 means
> *this string is absent*, never *this subject is absent*.
>
> That is an accepted trade for the first consolidation pass, whose job is to get the registry to a
> defensible starting point rather than a perfect one. Terms removed here are recoverable: later
> classification passes work from document content, so a real theme re-earns its tag on evidence
> rather than on a label nobody wrote. A semantic warrant — embedding the root against note content
> instead of string-matching it — is the obvious refinement when precision starts to matter more
> than cleanup.

### 6.4 Association (`RT`) — the cross-domain layer
ISO 25964 `RT` maps concepts that are related but neither is broader than the other: `motif/cosmic-horror` RT `fantasy/eldritch`. This is the layer that answers *"what else in the vault connects to this?"* across domains that share no parent — the question motif exists to make askable, answered at the vocabulary level rather than per-document.

**Population — curated, and not from where this section first said.**
`RT` relations are never inferred and activated automatically; they are approved by hand.

> [!WARNING] The "bank them during clustering" plan did not survive contact with the data
> This section previously claimed associative candidates were a free byproduct of synonym
> clustering: pairs that come back close but fail the merge test. Two things were wrong.
>
> First, **the banking was never implemented** — step 5 recorded only `UF` aliases, so there was no
> pre-populated list to curate.
>
> Second, and more important, **the near-miss band is not associative material.** Measured on this
> corpus at 0.88–0.92: 36% are same-root siblings, 13% are parent/child pairs the slash already
> encodes, and most of the remainder are merges the threshold simply missed —
> `home/maintenance` ~ `household/maintenance`, `3d-printing` ~ `additive-manufacturing`. A band
> that "fails the merge test" mostly contains merges whose threshold was too tight, not concepts
> that are related but distinct.
>
> Genuine `RT` material — the `motif/cosmic-horror` ~ `fantasy/eldritch` shape — is sparse here, and
> is better curated deliberately once the vocabulary settles than harvested from a leftover band.

> [!CAUTION] An `RT` must never link a term to its own ancestor or descendant
> The hierarchy already states that relationship. Recording it again is redundant, and *merging*
> such a pair destroys a level outright — collapsing `relationship/dynamics/support` into
> `relationship/dynamics` erases a distinction the tree was built to hold.

**Runtime — weighted expansion.**
A query on a term also returns documents related through `RT`, **ranked below direct matches**:

```text
QUERY: motif/cosmic-horror
  ├─ direct matches ................ weight 1.0
  └─ via RT (fantasy/eldritch) ..... weight 0.4   ← starting value, tunable
```

- The cross-domain connection reaches retrieval and RAG, but cannot displace precise matches.
- A mediocre relation degrades ranking rather than poisoning results — the failure mode is gradual and observable, not silent.
- The expansion weight is a **calibration value, not a constant**. `0.4` is a starting point to be tuned against live retrieval quality.
- Expansion is **one hop only**. Relations do not chain transitively; `A` RT `B` RT `C` never surfaces `C` for a query on `A`.

### 6.5 Registry consistency — the read path lags the write path
Registration writes to SQLite immediately, but the **vector read path that every consumer actually
queries is updated through a staging queue**, drained in batches by a background worker. A single
term becomes proposable within seconds; a bulk registration of thousands takes far longer.

**Measured throughput: ~5.5 terms/sec.** Rebuilding the 5,257-term collection took ~16 minutes. The
limit is embedding computation, not the drain loop's cadence — reasoning from batch size and sleep
interval alone underestimates it by roughly 5x.

- **Any pass that registers terms and then depends on reading them back must wait for the drain.**
  This is not a theoretical race: a curation pass that registers a batch and immediately queries the
  vector store will retrieve the pre-registration vocabulary and mint duplicates of what it just
  approved.
- Bulk operations (§9 steps 3, 6) therefore verify queue depth has reached zero before the next
  dependent step begins, rather than assuming propagation.

### 6.6 Novelty steering
The Tag RAG distance signal (`min_dist`) classifies the taxonomy's coverage of a document — HIGH / MODERATE / LOW. This directive **must reach the classifier prompt**. It is the mechanism that decides whether a document is filed under an existing branch or nominates a new one.

---

## ⚙️ 7. Operating Modes

Two modes, two risk profiles, two rule sets. Conflating them is what produced the destructive behavior this standard exists to prevent.

### 7.1 Steady-state audit (autonomous, unsupervised)
Runs on idle against the live vault. Conservative by mandate:

1. **Explicit-removal only.** A tag is removed only if the model was shown it *and* named it for removal. **Silence means keep.** Reconstructing a note's tag set from only the terms the model echoed back is prohibited — unshown tags are silently destroyed that way.
2. **No truncation of the judged set.** If the pass asks the model to audit a note's tags, it must show the model *every* tag. Summarizing 36 tags to "5 and others" and then acting on the verdict is an unsound audit.
3. **Suggestion limit applies to additions, not to totals.** A pass proposes **5–10 new terms** maximum. There is **no cap on how many tags a document may carry** — a 26-section reference document legitimately carries more tags than a 40-line snippet.
4. **Existing tags participate in candidate retrieval.** A note's current tags are not excluded from the Tag RAG candidate pool. The model must be able to see that a term it already has *is* the canonical master term.
5. **Classification quality is never traded for latency.** Timeouts are solved with scope, batching, or scheduling — never by degrading the classifier's reasoning or its view of the document. A document that cannot be classified within budget is **deferred, not partially processed**.
6. **Fail closed.** Any pass that does not produce a valid, complete decision writes nothing. A partial or unparsed result is a no-op, never a partial rewrite.
7. **Structural input.** The classifier receives the document's skeleton — heading outline, stored gist, opening prose — not a raw character slice that truncates inside a table of contents.
8. **No authority over the administrative axis** (§3.2).

### 7.2 Migration sweep (supervised, one-time, reversible)
Bulk transformation of the legacy corpus. Permitted to be aggressive **because** it is batch-reviewed before commit, version-controlled, and reversible — the safeguards live in the process, not in the pass.

- Deterministic transformations (format, namespace moves, deletions of retired namespaces) need no LLM and must not use one.
- LLM-driven steps produce a **reviewable diff manifest** first; nothing is written until the manifest is approved.
- Every sweep is a registered, versioned migration step per AGENTS.md §5. No ad-hoc scripts.

---

## 📊 8. Current-State Deltas

Measured against the live vault on 2026-09-19. These are the gaps this standard is written to close.

| Observation | Live value | Standard |
|---|---|---|
| Flat, un-nested tags | **4,584 of 5,228 (88%)** | Collapsed into the domain tree via §6.2 |
| Single-use orphan tags | **4,413 (84%)** | Prevented at source by §6.1 |
| Casing collisions | `ai`/`Ai`, `tech`/`Tech`, `work`/`Work` | Eliminated by §5 (no casing branch) |
| `location/` coherence | Place and biome conflated under one root | Split: biome → `setting/`, place → links (§2, §3.6) |
| `contact/*` | 8 role subdomains under a descriptive root | Retired. Collapses to `obsidian-graph/contact` (§3.2) |
| `relationship/*` | **36 tags**, artifacts of flat `#relationship-goals`-style tagging; never used as a real domain | **Retired entirely** |
| `motif/` maturity | 12 terms; `motif/connection` is 86% of all usage | Grown deliberately as an open vocabulary (§3.5) |
| `type/` coverage | 4 terms, 1 use each | Mandatory, exactly one per document (§3.4) |
| Documents audited | 7 of 4,115 | — |
| **Vocabulary sharing** | **593 of 12,263 memory terms (4.8%) exist in the taxonomy** | One registry governing both (§0, §6.1) |
| **Memory governance** | No `master_tag_taxonomy` table in the memory store; 11,670 terms ungoverned | Folded into the shared registry at step 3 |
| Duplicate concept shapes across stores | e.g. a hierarchical and a flat form of the same term coexisting | Merged by the combined-corpus pass at step 5 |
| Operator identity as a tag | Present in memory in two spellings | Entity, not concept — becomes a link at step 4 (§2) |

---

## 🚧 8.5 WORK IN PROGRESS — Resume Here

> [!NOTE] Steps 1–6 complete and applied as of 2026-09-20 (`000.006.169`)
> Vocabulary is normalized, namespace-clean, entity-free, collapsed onto preferred terms, roots
> consolidated, and every flat compound with an established head is nested. **Step 7 — the
> associative layer — is next.**

**State:** 15,013 terms across **361 roots**, 144 holding five or more terms. 3,849 `UF`
equivalences recorded (316 lexical, 1,618 reviewed, 533 root, 1,382 adopted). Zero stale variants in
either store.

**The unfinished part of step 6: 6,915 flat terms remain.** Every lexical avenue is exhausted —
their heads name nothing in the tree, so nesting them would be guesswork rather than inference.
They need classification from **document content**, which is step 8's job, not a rule applied to the
strings themselves. Do not invent a lexical rule for them; that is how the sparse-root mistake
happened.

**Known open item:** depth violations. `projects/coding/python-projects/tools/comfy-ui-projects/organization/obsidian`
is seven levels deep against §5's maximum of three. No step currently addresses this.

**Invariant that must not be broken:** every path writing a tag resolves through
`taxonomy_db.canonicalize_tags()` — `tag_librarian`, `fact_extractor` (facts and procedures),
`fact_deduplicator`, `fact_splitter`. The deduplicator is the dangerous one: merging recombines tags
from the **source** rows, so a retired variant returns and undoes a finished migration hours later.

**Artifacts in gitignored `scratch/`** (curated corpus facts containing personal vocabulary, AGENTS.md §4):
`tag_merge_decisions.json`, `tag_merge_review.md`, `.tag_embeddings.npz`.

> [!CAUTION] Do not run a taxonomy rebalance before the tree is built
> `maintain_master_taxonomy()` deletes every term with zero *vault* usage — much of the vocabulary
> now lives in memory rather than notes. The 15% circuit breaker does not catch it. It requires
> `rebalance_taxonomy=true` and nothing triggers it automatically.

---

## 🧭 9. Migration Sequence

Two ordering constraints are load-bearing:
- **Format normalization runs first**, because synonym detection is unreliable while one term exists in several spellings and casings.
- **The registry is unified before any clustering**, because clustering each store against a partial vocabulary produces mappings that are wrong the moment the stores are joined.

Steps operate on **both substrates as one corpus** unless marked otherwise (§0).

| # | Step | Mode | Scope | State |
|---|---|---|---|---|
| 1 | **Format sweep** — apply §5; merge casing collision classes; recompute usage | deterministic | both | ✅ `000.006.147` / `000.006.148` |
| 2 | **Namespace retirement — vault** — drop the `topic/` wrapper (§3.3); retire `relationship/*`; collapse `contact/*` roles to `obsidian-graph/contact`; move `location/biome/*` to `setting/biome/*` | deterministic | vault | ✅ `000.006.149` |
| 3 | **Registry unification** — extract the taxonomy API into `taxonomy_db.py` so ownership is explicit and no subsystem reaches into another's store; rebuild the shared `evelyn_tag_taxonomy` vector collection from the post-sweep registry | deterministic | both | ✅ `000.006.150` |
| 4 | **Entity extraction** (§2) — remove tags the link graph already carries, at a 100% redundancy threshold; drop tags restating their record's own subject and close the writer that produced them | deterministic | both | ✅ `000.006.151` / `000.006.152` |
| 5 | **Synonym collapse** (§6.2) — cluster the combined corpus once; propose `UF` mappings; review; apply | supervised | both | ✅ `000.006.153–155`, `161–163` |
| 6 | **Domain tree construction** (§6.3) — consolidate roots by literary warrant (§6.3.3), then nest flat compounds with an established head. 6,915 flat terms remain for step 8 | supervised | both | ✅ `000.006.166–169` |
| 7 | **Associative curation** (§6.4) — review banked `RT` candidates now that the terms they relate are stable. Leave expansion disabled until the weight is calibrated | curated | both | pending |
| 8 | **Classification backfill** — enable the steady-state pass (§7.1) to assign `type/`, `motif/`, `setting/`, `event/` | autonomous | vault (`type/` is vault-only per §0.1) | pending |
| 9 | **`relationship/*` retirement — memory** — gated on step 8 | deterministic | memory | pending |

> [!WARNING] Step 9 closes a deliberate, temporary asymmetry
> `relationship/*` was retired from the vault at step 2 but remains live in memory, where 903 fact
> rows carry it and 666 have no other tag. Retiring it there without replacement would strip those
> facts of their only retrieval handle, so it waits until steps 5–8 have given those rows
> replacement domain terms. Until step 9 runs, the two stores differ on this one namespace and any
> cross-store reasoning must account for it.

Why steps 5 and 6 run over the combined corpus rather than per store: memory carries roughly twice
as many distinct terms as the vault. Clustering the vault alone would build a domain tree shaped by
the smaller half of the vocabulary, then force the larger half into it afterwards. One pass, one
review, one result.

> [!IMPORTANT] Visible to clustering ≠ registered
> Step 5 reads **both stores directly**, so no term is invisible to it. But the registry only ever
> receives terms that survive curation (step 6). Importing raw extraction output into
> `master_tag_taxonomy` to "cover the corpus" would make the registry a record of every string ever
> emitted — the opposite of a controlled vocabulary, and a direct contradiction of §6.1.

| 10 | **Tool-layer format alignment** — once the vocabulary is settled, `MODEL_TOOL_DEFINITIONS` teaches the same format the prompts do, so every surface that emits a tag agrees | deferred | both | pending |

> [!NOTE] Why step 10 waits
> Putting format rules into the tool layer is a deliberate addition, not a correction. Steps 6–9
> keep changing what "correct" looks like, so encoding the current shape into tool descriptions now
> would bake in a form that is still moving. The prompts are the right single home until the domain
> tree settles; then the two are aligned rather than competing.

Each step is a registered migration and is measured against §8 before the next begins.

---

## 🎚️ 10. Calibration Values

The schema-level decisions are closed. What remains are three numbers that cannot be reasoned into correctness and must be set against live data. Each is a **placeholder carrying a default**, not an open design question — implementation proceeds on the defaults and tunes afterward.

| Value | Default | Tune against |
|---|---|---|
| Subsumption threshold (§6.3) | 5 documents | Whether the resulting tree is too flat or too deep once built |
| `RT` expansion weight (§6.4) | 0.4 | Retrieval precision — does related material help or intrude? |
| Motif recurrence | *none* (single occurrence) | Proposal-queue volume; tighten only if review becomes a burden |

---

## 🔗 Related Notes

- [[vault-note-style.md]] — Visual PKM formatting standard for note bodies
- [[engine_architecture.md]] — librarian subsystem architecture
- [[AGENTS.md]] — workspace rules, DRY and identity-parameterization boundaries
