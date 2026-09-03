---
name: Mongo runtime boundary
description: MongoDB-backed runtime boundary with SQLite retained only for staged migration and legacy data.
---

MongoDB is mandatory before the bot starts and owns users, settings, admin permissions, custom countries, custom payment definitions, inventory, auto prices, balances, deposits, UPI orders, and runtime orders. SQLite remains available for staged migration and legacy data only.

**Why:** Runtime purchase flows must share MongoDB's atomic inventory, balance, and numeric-order boundaries so a callback retry cannot create split-brain business state.

**How to apply:** Preserve the Mongo startup gate and keep SQLite usage limited to staged migration and legacy reads. User creation must remain idempotent and must not reset existing fields. Inventory reservations, balance events, and runtime order creation must remain MongoDB-backed and retry-safe.

MongoDB's built-in `_id` index should be treated as verified during preparation and never passed to `create_index`; only application-defined indexes should be created.

**Why:** MongoDB rejects an explicit `_id` index creation request that supplies the `unique` option, even though the built-in index is already unique.

**How to apply:** Keep the preparation guard for the single-field ascending `_id` definition while leaving all other index definitions and non-destructive duplicate checks unchanged.