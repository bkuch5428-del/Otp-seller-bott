---
name: Mongo runtime boundary
description: Staged persistence boundary between MongoDB-backed runtime data and deferred SQLite business flows.
---

MongoDB is mandatory before the bot starts and owns users, settings, admin permissions, custom countries, and custom payment definitions. SQLite remains authoritative for inventory, deposits, orders, and balance mutations until their dedicated migration phases.

**Why:** The migration is intentionally incremental; moving financial and order state before its consistency model is defined could split balances or create duplicate business events.

**How to apply:** Preserve the Mongo startup gate and keep future SQLite usage limited to the explicitly deferred collections. User creation must remain idempotent and must not reset existing fields.