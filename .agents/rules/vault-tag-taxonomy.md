---
trigger: manual
description: Faceted Classification schema, facet axes, format standard, and vocabulary-control rules governing all tag curation across the vault and memory as one structure.
---

# Vault Tag Taxonomy & Faceted Classification Specification

## 1. Scope & System Authority

1. **Unified Knowledge Store**: The vault notes and memory facts (`context_entries`) form a single knowledge structure governed by one shared controlled vocabulary. A tag represents a concept whose meaning remains identical across both storage substrates.
2. **Post-Coordinate Indexing**: Concepts are maintained as discrete atoms at indexing time and combined at query time. Pre-composed hierarchical strings (e.g., `work/routine/morning` or compound strings representing distinct concepts) are prohibited.
3. **Concepts vs. Entities**: Tags represent classes, kinds, or abstract concepts. Named individuals (people, specific places, organizations, creative works) must never exist as tags:
   - In vault notes, individuals are referenced as wikilinks (`[[Jane Doe]]`, `[[Kansas]]`).
   - In memory context entries, individuals are referenced via the name register table (`tag_entities`).
4. **Substrate-Specific Divergences**:
   - `type/` and document class profiles apply only to vault notes (memory facts lack document forms).
   - `obsidian-graph/*` applies only to vault notes. The memory administrative namespace is `status/*`.
   - Fast-memory categories (`Cat##-U` / `Cat##-A`) operate independently of this taxonomy.

---

## 2. Facet Namespaces

Facets are orthogonal coordinates. A note or fact exists at their intersection.

| Axis | Namespace / Format | Cardinality | Purpose |
|---|---|---|---|
| **Domain** | Atomic, no prefix (`gis`, `routine`) | $\ge 1$ | Subject matter of the content |
| **Type** | `type/<form>` | Exactly 1 | Document structural class (vault notes only) |
| **Motif** | `motif/<atom>` | $\ge 0$ | Recurring abstract theme or symbol (conditional) |
| **Setting** | `setting/<atom>` | $\ge 0$ | Conceptual environment or space type (conditional) |
| **Event** | `event/<atom>` | $\ge 0$ | Class of life occurrence anchoring the note (conditional) |
| **Time** | `occurred: <EDTF>` | 0 or 1 | Frontmatter date attribute; not a tag |
| **Administrative** | `obsidian-graph/`, `status/` | $\ge 0$ | Engine operational flags; excluded from catalog audits |

### 2.1 Media Sub-Typing
The `type/media` facet requires a secondary sub-type selected strictly from the DCMI Type Vocabulary:
`type/media/sound`, `type/media/moving-image`, `type/media/still-image`, `type/media/text`, `type/media/interactive`, `type/media/software`, `type/media/dataset`, `type/media/physical-object`.

### 2.2 Time Property Standards
Dates are stored in the frontmatter `occurred:` key using EDTF (ISO 8601-2:2019) syntax (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`, or uppercase `X` for unspecified digits, e.g., `XXXX-11-16`). Dates are not tags.

---

## 3. Document Class Profiles

The document's `type/` determines which conditional facets are permitted, required, or prohibited.

| Class (`type/`) | Domain | Motif | Setting | Event | Time (`occurred:`) |
|---|---|---|---|---|---|
| `reference`, `guide`, `manual` | Required | Prohibited | Prohibited | Prohibited | Optional |
| `overview`, `moc`, `list` | Required | Prohibited | Prohibited | Prohibited | Optional |
| `log`, `report` | Required | Prohibited | Prohibited | Optional | Required |
| `journal-entry` | Required | Optional | Optional | Optional | Required |
| `dream` | Required | Required | Required | Optional | Required |
| `creative` | Required | Required | Optional | Optional | Optional |
| `media/*` | Required | Optional | Optional | Optional | Optional |
| `recipe`, `notes` | Required | Prohibited | Prohibited | Prohibited | Optional |
| `profile` | Required | Prohibited | Prohibited | Prohibited | Optional |
| `stub` | None | Prohibited | Prohibited | Prohibited | Optional |

---

## 4. Syntax & Format Invariants

1. **Casing**: Lowercase ASCII alphanumeric characters only. Acronyms must be lowercased (`gis`, `gps`).
2. **Compounds**: Hyphens join words strictly within a single concept (`3d-printing`, `machine-learning`). Coordinate combinations must be separated into individual tags.
3. **Delimiter**: Slashes indicate a facet namespace prefix only. A tag may contain at most one slash (`motif/combat`, `type/guide`), with the sole exception of DCMI media sub-typing (`type/media/<dcmi-token>`).
4. **Number Form**: Singular count and mass nouns by default per ANSI/NISO Z39.19 exception warrant (`dream`, not `dreams`).

---

## 5. Vocabulary Authority Control

1. **Registry Authority**: `master_tag_taxonomy` is the sole source of authorized terms. Document audit passes may only apply tags present in this registry.
2. **Proposal Quarantine**: Any term not present in the master taxonomy must be routed to the human proposal queue with its top 3–5 nearest semantic neighbors and vector distance scores from Tag RAG. No unvetted term may be committed directly to disk.
3. **Relational Model**:
   - `UF` (Used For / Equivalence): Flat alias mappings routing retired or colloquial synonyms to canonical terms.
   - `RT` (Related Term): Associative relationship between distinct authorized terms. Expands search queries by one hop at weight `0.4`.
   - `BT`/`NT` (Narrower/Broader): Hierarchical concept links stored as flat relational rows (`kind = narrower`) without path inheritance.
4. **Write-Path Invariant**: Every automated pass writing a tag must resolve terms through `taxonomy_db.canonicalize_tags()`.

---

## 6. Runtime Operating Guardrails

### 6.1 Steady-State Audit Pass (Autonomous)
- **Explicit Removal Only**: Silence preserves tags. A tag is removed only if presented in the prompt and explicitly targeted for deletion.
- **No Input Truncation**: The auditor must receive the complete tag set of the document.
- **Suggestion Caps**: A pass may suggest 5–10 new additions maximum; it cannot enforce an arbitrary limit on total note tags.
- **Fail-Closed**: Parsing errors, token budget overruns, or incomplete responses abort execution without writing partial diffs.
- **Administrative Firewall**: The pass has zero read or write authority over `obsidian-graph/*` or `status/*`.

### 6.2 Supervised Migrations
- Bulk transforms must execute deterministically without an LLM where possible.
- LLM-assisted sweeps must produce an inspectable diff manifest prior to database or disk write.