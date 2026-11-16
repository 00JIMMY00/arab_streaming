## 2026-11-16 — Capture native db_set and explicit consumer SQL updates

Version 0.2.9 captures transferable changes made through Document.db_set using
before_change/on_change, retaining the original document and transactional source
fence. Unchanged values and the normal save/on_change sequence do not create
extra dispatches; local-only fields do not activate ownership checks. Consumers
that deliberately use raw SQL can call outbox.notify_persisted_change(before_doc)
after their authorized write and before commit. Arbitrary external SQL writes do
not generate model events. The notifier does not grant business write authority.

## 2026-11-16 — Opt-in central User passwords and transaction identity fixes

Version 0.2.8 adds Master-to-Slave User password synchronization when the User
rule's Include Passwords option is enabled. Both peers must be upgraded. Password
changes create ordinary addressed Dispatch/Capture Events containing only an opaque
version reference. Hashes are stored encrypted in protected versions and fetched
only for the addressed event over the existing verified, authenticated transport.
Plaintext passwords, API keys, reset tokens and sessions are never replicated.
Administrator, Guest and integration identities remain excluded. Receiving a new
password clears that user's Slave sessions. Native User saves are captured
immediately; a bounded agent scan also detects native password-reset/Auth updates.

Native fixed transaction counters without a naming_series field now include
Branch and writer partitions. This prevents POS Opening/Closing identity clashes;
existing records and immutable failed events are not renamed or overwritten.
Queued native User contact maintenance restores its origin from persisted replica
state, suppresses echo and checkpoints its local normalization. Posted flags cannot
establish this authority.

## 2026-11-16 — Branch writer guard for interactive applications

Version 0.2.7 exposes `ownership.assert_local_branch_writer(branch)` for consuming
applications. A paired Master cannot operate that Slave's branch; a Slave can
operate only its paired branch. Pausing transport or disabling a peer does not
transfer branch ownership. Unlinking does. This guard does not grant replay
permissions or change rule configuration. Consumers must call it at their
interactive operation boundary. User passwords remain local under the current
protocol; a password-only change intentionally emits no business Dispatch Event.

## 2026-11-15 — Preserve built-in identity references locally

Version 0.2.6 retains Links to Administrator and Guest without freezing their
protected User records as dependencies. Every Frappe site already has these
identities; ordinary users still follow the configured dependency behavior.
This avoids demanding access to the protected Administrator record during a
business snapshot. Source business permissions and native target validation stay
in effect.

## 2026-11-15 — Batch internal export snapshot artifacts

Version 0.2.5 writes the internal Streaming Snapshot Item artifacts in small
batches under the existing system-write context. Business documents still pass
ordinary source read checks and native receiving controllers. Canonical payloads,
file/protected references, identities, digests and the enclosing transaction remain
unchanged. Resolve each row's rule once while the source policy fence is held.
This reduces snapshot preparation overhead without extending its work budgets.

## 2026-11-15 — Avoid exporting preserved receiver definitions again

Before the first export request, a copy run freezes the existing receiver names
for Preserve Existing rows. The master applies them only as additional exclusions
inside the already-authorized selection; Update rows cannot use this exclusion.
The baseline is persisted before the request, remains unchanged on Continue and
does not count as an operator configuration edit. Source graph traversal allows
up to the same 10,000-record scale as roots while retaining depth, byte and time
bounds. Company scaffolds still receive their full Update snapshots.

## 2026-11-15 — Packaged app images during synchronization

Version 0.2.3 accepts `/assets/<installed-app>/...` images after validating that
the path stays inside the app's public directory and the file exists. The receiver
checks its own installed copy and retains the URL; no File upload record or
network download is needed for images shipped in the app release. Uploaded-file
hash transfer and verified HTTPS references keep their existing behavior.
Traversal, unknown apps, missing files and symlinks outside public stay rejected.

## 2026-11-15 — Administrator-owned Instance Keys

Instance Keys may now be owned by Administrator. A dedicated enabled identity is
created automatically for each such key, with the existing rule-scoped execution
permissions. Wire authentication and pairing use that identity; credentials remain
limited to the explicit streaming wire methods. Guest owners and disabled users
remain rejected. Existing non-Administrator keys retain their owner identity.

## 2026-11-15 — Arab Streaming is available in RMS

Installed Arab Streaming on the RMS site and made its workspace available in Management. The requested installation chain is Frappe, ERPNext, Arab Streaming, then RMS. Verified the site's startup/catalog reads and preserved existing business records. No connection, execution key, branch selection or data rule was created; synchronization becomes an operational configuration step after the intended master/branch arrangement is supplied.

## 2026-10-30 — Contacts and addresses now follow customer changes

Deployed the linked-record fix to both sites and repaired the existing missed associations. Contacts/addresses added after the customer, subsequent edits, removed links and normal deletion/renaming now enter the synchronization workflow automatically, including multiple non-primary contacts. Fixed the circular dependency failure and false conflict checkpoints. Shared links and independent local edits retain their safeguards. All 88 automated tests passed, and a temporary live customer completed automatic add/edit/delete verification and cleanup.

## 2026-10-29 — Import button and simpler upload flow

Fixed the client busy state that kept Import Customers disabled after successful validation: use native async/await with try/finally around Frappe's jQuery-style request promises. Removed the separate Validate action and confirmation dialog; uploads validate automatically and Import directly starts the reviewed job. Default changes trigger automatic revalidation. Moved detailed field guidance into an expandable section. A client regression harness verifies ready-state enablement, actual click dispatch, success display and control recovery with Frappe-style thenables. Node syntax check passed. Backend validation, atomicity, duplicate protection and background bulk processing are unchanged.

## 2026-10-29 — Bulk customer import

Added the master Customer Import Tool with original Excel template download, private upload, validation, three phone contacts and two addresses. Removed the initial 500-row restriction at the user's request. Validation and import now run in background jobs. Eight isolated tests pass, including creation of 501 customers in one import.

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


## 2026-11-15 — RMS prepared for future branch connections

Prepared 58 editable RMS rules (41 master-to-branch, 12 branch-to-master and 5 bidirectional), connected its three Store definitions to Branch identities, and supplied 46 initial-copy suggestions for a new branch. Transport, site role, peers and credentials remain unconfigured. Arabic documentation includes every setting's 1/0 meaning and a comparison of 103 reference document types. The reference source was read only; RMS implementation is independent.

Application routing preserves branch ownership without adding fields to every transaction. Replica checks retain original POS states/rounding and prevent duplicate waste/expense movements or invoice consolidation. Security approvals, print queues and generated records stay local. Verified 127 focused read-only/mocked tests, 138 operational scope checks, rule directions/idempotence, unchanged business/ledger counts and the current RMS site's HTTPS health. A real branch connection and two-site financial acceptance have not been tested.

## 2026-11-15 — RMS first-run dependency support

Arab Streaming 0.2.1 includes the existing RMS routing compatibility changes and
an optional-link deferral hook for authenticated consumer bootstrap. Optional
cyclic Store references are restored inside the item transaction with normal
validation. The RMS app supplies the role-selection/connection UI and company
bootstrap; installing the engine alone does not activate a site or pair a branch.
