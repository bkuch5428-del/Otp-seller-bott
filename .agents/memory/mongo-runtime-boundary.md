---
name: Mongo runtime boundary
description: Staged persistence boundary between MongoDB-backed runtime data and deferred SQLite business flows.
---

MongoDB is mandatory before the bot starts and owns users, settings, admin permissions, custom countries, custom payment definitions, inventory, auto prices, balances, deposits, and UPI orders. SQLite remains authoritative for orders until their dedicated migration phase.

**Why:** The migration is intentionally incremental; orders remain deferred while balance and payment state now use MongoDB's atomic event model to avoid duplicate financial effects.

**How to apply:** Preserve the Mongo startup gate and keep future SQLite usage limited to orders. User creation must remain idempotent and must not reset existing fields. Inventory reservations and balance events must remain atomic in MongoDB.

MongoDB's built-in `_id` index should be treated as verified during preparation and never passed to `create_index`; only application-defined indexes should be created.

**Why:** MongoDB rejects an explicit `_id` index creation request that supplies the `unique` option, even though the built-in index is already unique.

**How to apply:** Keep the preparation guard for the single-field ascending `_id` definition while leaving all other index definitions and non-destructive duplicate checks unchanged.