# Arab-ERP Streaming updates

## User synchronization — 2026-10-29

User records can be selected in Streaming Rules and copied in either configured direction, using Shared scope for centrally shared accounts. Missing linked Users are included in new immutable dependency manifests and fetched through the existing dependency path. Normal User validation and installed Role references apply; this does not publish Role definitions or User Permission records.

User identity, enabled state, profile fields and role assignments are synchronized. Passwords, API credentials, password-reset tokens, social login identities, mailbox settings and login/session history stay local. Synchronization sends no welcome email. Existing target credentials remain unchanged. Existing failed events retain their original frozen manifests; an older missing User must be copied separately before retrying that event.

## Branch fields and shared references — 2026-10-29

A Branch Link field can exist on a Shared reference DocType without changing its routing. When fetching a dependency from an enabled Shared rule for the paired Branch, the reference stays Shared even if that rule's normal event direction is the reverse direction. Ordinary root events still require their configured direction; Branch-scoped dependencies still require the authenticated Branch.

For existing Branch-scoped records, fill the configured Branch before previewing a copy. Adding the field does not backfill historical records. Submitted records may need an administrator to assign the initial Branch without reopening the transaction.

## Copy existing records in either direction — 2026-10-29

Open a saved **Streaming Rule → Copy Existing Records**. Choose **Master to Slave** or **Slave to Master** (as allowed by the rule), select a connected Branch, choose how to handle existing destination records, then **Preview Copy → Start Copy**. Run it again whenever needed after the previous run finishes. The master can start either direction; a slave can start its own Slave to Master copy.

**Preserve existing records** is the default. **Follow rule** uses the rule's configured duplicate policy, including Update when explicitly selected there. Recent runs and an **Open Copy Run** link show progress on the source site. **Complete** requires confirmed receiver outcomes; queueing alone is Waiting. Inspect Copy Events for failures and retry the existing capture after correcting the cause. Resume continues a failed source scan; Stop stops scheduling more records, while already queued events continue.

Records are processed in small batches through the existing delivery engine. Source documents are read as their batch runs and are not re-saved just to trigger sync. Saved Branch/conditions and normal business validation apply. Copying does not create missing POS sessions automatically or fix a failed invoice validation.

## Event status and retry — 2026-10-29

- **Delivered**: the other site stored the event; application is still pending or its result is unavailable.
- **Applied**: every destination confirmed successful application. **Failed**: a receiver rejected application; open the event to read its error. Skipped/ignored/partial outcomes stay explicit.
- On a failed **Dispatch Event**, use **Refresh Receiver Status**, inspect the receiver error or **Open Receiver Event**, then **Retry on Receiver** after fixing the cause.
- On the receiving **Capture Event**, use the primary **Retry Now** action. It requeues the same event immediately; normal automatic retries also continue with backoff.
- Retry does not repair missing setup, change a frozen snapshot, bypass validation, or duplicate an invoice. The page shows when receiver status was last checked; refresh after a retry to see the result.

## 2026-10-28 — Customer setup check resolved

Matched the missing Customer custom Branch field on the slave. The server-side configuration check now returns Ready. Direction remains Master to Slave; no Customer records were copied by this repair.

## 2026-10-27 — Customer selector fixed

Fixed premature candidate pagination that hid Customer and other valid business DocTypes. Verified Customer and Customer Group appear in the actual master rule dropdown; a focused isolated search regression passes. Reload the form and select Customer. No new rule was saved and no Customer records were copied by this fix.

## 2026-10-27 — Essential dynamic release 0.2.0

Implemented metadata-driven Streaming Rules, three directions, Branch accounting/inventory dimensions and routing, generic lifecycle execution, authenticated peer push and one-second agents. Connection screens now show central rules instead of editable outgoing child tables. Verified 46 isolated tests plus real HTTPS child/file/Both/invoice lifecycle checks. Ten Branch recipient routes and forged-Branch rejection passed. The user explicitly narrowed the immediate delivery to essentials; the broader load/fault/bootstrap certification is not a completion claim. Deployed version 0.2.0 and completed migrations on both current servers. Supervised agents are active. New UOMs were automatically created successfully in both directions; UOM is configured Both/Shared without name filters for the test Branch. Sales Invoice is selectable and its test-Branch Both configuration passes Test Configuration. Live business invoice posting remains to be tested with compatible prerequisites. See PROGRESS.md for observed evidence.


## 2026-10-27 — Dynamic multi-branch plan replaces fixed catalog expansion

The user clarified the intended workflow: choose business DocType, direction and branch scope in the app; ten or more branches exchange their own transactions with one master; background delivery takes seconds under normal conditions. Current fixed selectors, minute polling and catalog-only execution do not deliver this. Branch is paired on connections but not enforced on business routing, and no Branch accounting dimension is configured on the current sites.

Prepared docs/DYNAMIC_BRANCH_SYNC_PLAN.md with eight implementation gates and concrete generic-document, invoice/ledger, ten-branch isolation, latency and recovery tests. No runtime behavior changed in this planning turn. The prior catalog tests and live results are retained as partial evidence, not multi-branch completion.

## 2026-10-27 — Item can now be configured and synchronized

Implemented and deployed Item catalog support to both servers, including UOM conversions, barcodes, Brand and variant/template/attribute dependencies. Item is already saved in the master connection's Send to Slave table with blank Conditions, so future master Item saves queue automatically. The old generic adapter error now lists the supported types; connection selectors suggest only implemented types.

Verified a selected existing Item through initial copy and a new Item through automatic background delivery. All 35 isolated tests pass. Item creation helpers do not create stock ledgers or independent prices on the receiver. Images/files, price replication, stock quantities and broader financial/transaction adapters remain unfinished. Older Items require an explicit initial-copy selection; adding an outgoing rule affects future saves.

## 2026-10-27 — Working UOM/Item Group synchronization on both servers

Deployed reference-data connection, copy and background event processing to the master and the contabo1 slave. Link/Ping, initial Sync/Continue, Fetch/Execute and event retry/ignore actions now exist. Initial copy completed at 100% for the selected test scope, including the user's testuom. That UOM is present on the slave.

Removed the master UOM name filter that previously excluded new names. Future master UOM saves now queue automatically for this slave. A new UOM and an update made after bootstrap both arrived successfully through background processing without manual Sync/Fetch. Reverse UOM and Item Group scopes remain named tests. Form tabs now say Send to Slave, Send to Master and Copy Existing Records; table help explains blank Conditions versus named filters. See docs/USER_GUIDE.md for click steps.

The latest isolated suite passed all 30 tests. Financial/stock adapters, files/secrets/series bootstrap, complete fault/load acceptance and some recovery/configuration actions remain under development. This delivery is the reference-data phase; 100% copy means the selected rows finished, not that every site record or app feature is synchronized. Earlier entries below are historical.

## 2026-10-27 — Foundation implementation

The app now has its core connection, configuration and event schemas, plus protected durable state. Settings and Branch participation are explicit. Instance Keys use generated 15-character credentials, encrypted Password storage and Administrator-only create/delete/reveal guards, including Frappe's generic password endpoint.

Fresh installation, repeated migrations and all 23 focused tests passed on each of two isolated ERPNext v15 sites. The working master has not been migrated or configured, and no services have been restarted. Sharing the source checkout does not mean the new schemas have been deployed to the master.

Synchronization remains under development. Authentication, Link/Ping, initial copy, both event directions, business adapters and real separate-server acceptance remain pending. No business data has been synchronized by this implementation.

## 2026-10-27 — Instance Key credential display

Added an Administrator-only **Show API Secret** button to saved Instance Key forms. It displays the Access Key and API Secret in a temporary dialog, clears the secret on close, and uses the existing protected server method. No credential regeneration or permission change is involved.
