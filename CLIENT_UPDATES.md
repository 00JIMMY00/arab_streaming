# Arab-ERP Streaming updates

## 2026-10-27 — Foundation implementation

The app now has its core connection, configuration and event schemas, plus protected durable state. Settings and Branch participation are explicit. Instance Keys use generated 15-character credentials, encrypted Password storage and Administrator-only create/delete/reveal guards, including Frappe's generic password endpoint.

Fresh installation, repeated migrations and all 23 focused tests passed on each of two isolated ERPNext v15 sites. The working master has not been migrated or configured, and no services have been restarted. Sharing the source checkout does not mean the new schemas have been deployed to the master.

Synchronization remains under development. Authentication, Link/Ping, initial copy, both event directions, business adapters and real separate-server acceptance remain pending. No business data has been synchronized by this implementation.
