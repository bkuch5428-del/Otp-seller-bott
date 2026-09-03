---
name: Mongo runtime boundary
description: Staged persistence boundary between MongoDB-backed runtime data and deferred SQLite business flows.
---

MongoDB is mandatory before the bot starts and owns users, settings, admin permissions, custom countries, custom payment definitions, inventory, and auto prices. SQLite remains authoritative for deposits, UPI orders, orders, and balance mutations until their dedicated migration phases.

**Why:** The migration is intentionally incremental; moving financial and order state before its consistency model is defined could split balances or create duplicate business events.

**How to apply:** Preserve the Mongo startup gate and keep future SQLite usage limited to the explicitly deferred collections. User creation must remain idempotent and must not reset existing fields. Inventory reservations must remain atomic in MongoDB.

MongoDB's built-in `_id` index should be treated as verified during preparation and never passed to `create_index`; only application-defined indexes should be created.

**Why:** MongoDB rejects an explicit `_id` index creation request that supplies the `unique` option, even though the built-in index is already unique.

**How to apply:** Keep the preparation guard for the single-field ascending `_id` definition while leaving all other index definitions and non-destructive duplicate checks unchanged.