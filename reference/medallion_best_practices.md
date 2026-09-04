# Medallion Architecture — Best Practices

> Companion reference to `plan.md`. Kept separate since this is research/principles, not the pipeline architecture itself.

## Core Idea

Medallion architecture is **not a technology** — there's no "medallion" feature to turn on in Fabric. It's a naming convention plus a set of agreements about what each table is allowed to contain at each stage. The value comes entirely from **discipline maintained over time**, not tooling.

**Why it exists:** a single-step raw → dashboard pipeline is nearly impossible to debug. When a number looks wrong, you can't tell whether it was the source data, the cleaning logic, or the business logic — they're all fused into one job, and only the final output survives. Splitting into stages means every stage leaves something on disk you can inspect.

---

## What Belongs in Each Layer

### Bronze — raw, append-only
- Exact copy of source data, no transformation. No cleaning, filtering, renaming, or typecasting.
- Malformed rows go in malformed — bronze is a photograph, not a fix.
- **Add load metadata** (nearly free, high payoff): ingestion timestamp, source system name, source file/partition/offset, batch ID.
- **Partition by ingestion date**, not business date — ingestion date is what you'll actually filter on when replaying.
- Store semi-structured payloads as-is (JSON/variant), don't shred into columns yet — shredding is a transformation and belongs downstream.
- Bronze is your **recovery position**: if silver logic breaks for three weeks, you replay from bronze rather than re-pulling from a source system that's already moved on or aged out its history window.
- Put a **retention policy** on it — bronze grows forever, and nobody notices until the storage bill does.

### Silver — clean, typed, conformed
- Cast types, trim whitespace, standardize values (e.g., country codes), deduplicate, join across sources into one conformed entity (one customer, one key, one shape).
- **No business definitions allowed in silver.** Casting a timestamp is cleaning. Deciding what counts as an "active customer" is a business definition — that belongs in gold.
- **The test:** if two departments could argue about it, it goes in gold, not silver. Nobody argues whether a date should be a date; people argue about what counts as active/churned/recognized revenue.
- Slowly changing dimensions (e.g., historical plan tier) belong here too, with valid-from/valid-to columns — that's a structural fact about the entity, not an opinion.

### Gold — business-ready, consumer-shaped
- Shaped for a specific consumer: BI star schema, one wide denormalized table, ML feature table, or a flat extract for another system.
- Built **backwards from the question someone needs answered**, not forwards from what the source happened to provide.
- **Metric definitions live here, each in exactly one place.** If MRR is computed in four gold tables, you get four different MRR numbers — and they disagree in the meeting where it matters most.
- Gold is the layer most of the org should query directly (analysts, execs). Silver is for the data team. Bronze is for engineers/replay only.
- Gold is also the **cheapest layer to rebuild** — everything it depends on is already clean in silver, so definition changes rebuild fast.

---

## The Four Rules That Keep Layers Honest

1. **Data flows downhill only.** Bronze → silver → gold. Gold never reads bronze directly. Reaching back two layers signals something is missing in silver — fix silver, don't route around it.
2. **Every layer is reproducible from the one above it.** Lose bronze, and you're stuck asking a vendor to re-export 18 months of history.
3. **Transformations are idempotent.** Running the same job twice produces the same result, never duplicated rows. In practice: merge on key or overwrite a partition, not blind append. Pipelines fail halfway and get rerun — make reruns boring.
4. **Quality checks live at layer boundaries.** Check on the way into silver (catches source problems) and on the way into gold (catches logic problems): row counts, null rates on key columns, uniqueness on primary keys, referential checks. Decide in advance whether a failed check halts the pipeline or quarantines bad rows — not during an incident.

---

## Five Common Mistakes

1. **Layers as folders, not contracts.** A silver table that's `SELECT * FROM bronze` gives you the vocabulary with none of the benefit.
2. **Business logic leaking into silver.** Once a business definition is baked into silver, every gold table inherits it whether it wants to or not — and changing it means rebuilding everything downstream.
3. **Forcing exactly three layers when the problem doesn't need it.** Nothing is sacred about the number three — a staging layer between bronze/silver, or splitting gold into a shared dimensional core plus consumer-specific marts, is fine. What matters is each layer has a distinct job; a layer with no job is bureaucracy.
4. **No retention/compaction policy.** Bronze accumulates small files and old partitions forever — storage costs triple and query speed drops, and everyone blames the query engine instead of the missing policy.
5. **No ownership per layer.** If nobody owns a layer, it rots — every new pipeline adds a slightly different version of the same table.

---

## When to Skip Medallion Entirely

- Small team, handful of sources, single dashboard → a two-layer raw + mart setup is fine; add a middle layer when you start feeling pain.
- True real-time/sub-second workloads → three materialized layers add latency you likely can't afford; use a streaming architecture instead.
- Source system is already a clean, governed modern warehouse → relayering it as "raw" and recleaning is work with no payoff.
- **Adopt the pattern when you feel a specific pain**: "I can't tell where this number went wrong" or "I can't reprocess without going back to the source." If you don't feel that pain, you probably don't need it.

---

## Applying This to the Fabric / dbt / Dagster Pipeline

- **Staging models (silver) should stay pure cleaning** — typing, dedup, renaming, conforming keys. Worth auditing for any business logic (qualifying thresholds, filtering rules) that may have crept in and should move to marts instead.
- **Gold pre-forecast mart** is the correct home for join logic and metric definitions. Since the forecast notebook writes the *final* gold mart on top of this, make sure no metric gets redefined differently inside the notebook logic than it is in dbt — one definition, one place.
- **Add dbt tests at layer boundaries** — not yet an explicit step in the pipeline plan. Cheap to add via dbt's built-in `tests` config:
  - Into silver: source freshness, not-null, accepted-values checks on raw-derived columns.
  - Into gold: uniqueness on grain keys, referential integrity between joined marts, row-count sanity checks.
- **Bronze retention policy** on the Fabric Lakehouse tables — not yet decided; worth setting explicitly rather than letting raw Parquet/Delta accumulate indefinitely.
- **Ownership** — even as a solo project, worth deciding (even informally) who/what "owns" each layer's schema changes, since dbt model changes in silver ripple into every gold model downstream.
