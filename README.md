## 2026-11-16 — Preserve verified native financial defaults and closing reference identity

Version 0.2.14 preserves an existing posted Item Group when an older source item
snapshot leaves that read-only classification empty for the same Item. Financial
fields still pass native update-after-submit checks. POS closing invoice-reference
rows retain their local IDs only when the invoice link and every other row value
are identical; changed amounts, dates or associations are not ignored. This allows
already-applied native closing dependencies to converge without overwriting a
newer state or weakening general conflict checks.

## 2026-11-16 — Apply native post-submit financial metadata narrowly

Version 0.2.13 handles Sales Invoice status and POS Invoice status/consolidated
invoice through a narrow adapter. The ordinary native financial save still checks
immutable accounting fields, links, permissions and amounts. Only validated native
status metadata is staged and then written through Document.db_set. Consolidation
links must reference a readable Sales Invoice in the same Company containing this
POS Invoice. No global ignore_update_after_submit flag is used and no ledger rows
are copied or reposted for a status-only update.

## 2026-11-16 — Keep localized tax rendering local

Version 0.2.12 excludes other_charges_calculation (native localized tax-breakup
HTML) from transaction snapshots. Actual tax rows, rates, totals and financial
controller validation remain unchanged. Legacy immutable events may still contain
the HTML; the local adapter discards only that derived display field. Prior local
checkpoint digests can be recognized including their old rendered field, without
accepting an unrelated business-data change. This prevents locale/currency-symbol
HTML differences from blocking native updates after submission.

## 2026-11-16 — Recover native User contact row normalization safely

Version 0.2.11 accepts an otherwise identical User Contact whose only differences
are generated phone/email child row IDs. All actual values, user identity, link
ownership and other fields must still match; real content conflicts remain blocked.
Password fetch authorization also checks the current pairing generation, and an
unchanged password hash replay leaves existing receiver sessions intact. Queued
User contact replay requires its persisted connection to remain linked.

## 2026-11-16 — Invalidate provisioned permissions after commit

Version 0.2.10 clears provisioned DocType/user permission caches again after the
permission transaction commits. Concurrent workers can otherwise cache the old
committed metadata during policy publication even though the new grants are
visible in the database. Immediate local invalidation is retained. No broader
roles or permission bypasses are granted.

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

## RMS release-group installation — 2026-11-15

For the RMS product, include the applications in one release group in this order: **Frappe → ERPNext → Arab Streaming → RMS**. Arab Streaming already requires ERPNext; RMS now declares Arab Streaming as a required application. The current RMS site uses Frappe/ERPNext 15.122.0 and Arab Streaming 0.2.0. Its native site installation and workspace were verified; existing other apps were retained.

The RMS site is not configured as a Master or Slave and has no peers/rules yet. Installing the app does not start remote synchronization. The existing historical master/slave deployment described below is a different environment. Hosting-provider group registration is outside this site-installation verification.

## Linked contacts and addresses

Customer synchronization includes linked Contacts and Addresses, including non-primary records. New records and later edits, unlinking, deletion and ordinary renaming follow the Customer rule automatically. Shared associations are projected for each peer, unrelated receiver links are preserved, and conflicting local edits require review. Normal permissions, validation and rule direction/branch restrictions still apply. Linked-only updates avoid saving an unchanged Customer; ERPNext may legitimately update a stored primary-address display when the address itself changes.

## Customer Import Tool

Master System Managers can open `/app/customer-import-tool` or choose Bulk Import Customers in the Customer list. Download the original 18-column Excel template, fill one customer per row, upload privately, validate, and import. Customer Name and Customer Type are required. Supports three phone contacts and two optional addresses; each supplied address needs street, city and country. Existing customer names are skipped. There is no fixed row-count limit. Validation/import run in the long background queue with a two-hour timeout; file-size protections remain 5 MB compressed / 25 MB expanded. Failed imports roll back the whole batch. Keep the page open while processing.

# Arab-ERP Streaming

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

Customer rule c58n8hl0ie now passes Test Configuration after matching its custom Branch field on the slave. Current direction remains Master to Slave; select Slave to Master for reverse-only delivery.

Version 0.2.0 implements the essential dynamic synchronization workflow for Frappe/ERPNext v15. Configure business parent DocTypes in **Streaming Rule** on the master, choose **Master to Slave**, **Slave to Master**, or **Both**, and choose Branch scope or explicit Shared reference data. New business saves are captured atomically and delivered automatically. The old connection child tables are retained for migration and hidden from normal setup.

The rule selector now correctly searches the full eligible translated DocType list, including Customer. Reload an already-open form after the selector fix.

1. Open **Streaming Rule** on the master and create a rule.
2. Choose DocType, direction and participating branches. Transactions use **Branch** scope; UOM/Item catalog data can use **Shared**.
3. Enable and save. **Test Configuration** checks the receiving schema and permissions.
4. Save a business document. For a transaction, choose its Branch on the master; a branch site fills its own Branch. Check **Capture Event** for Success or a specific error.

**Ping** checks connectivity. **Copy Existing Records** explicitly backfills older data; it is separate from automatic delivery of new changes. Conditions left blank include every qualifying record. Existing named-test conditions are preserved during migration. On the current pair, the approved test configuration now uses Both/Shared UOM with blank conditions; Item Group retains its named-test condition.

The engine uses metadata rather than a fixed business DocType list, normal document permissions/controllers, stable child identities, immutable event/dependency/file versions, durable receipts, per-record conflicts, and normal submit/cancel behavior. User accounts are supported; other framework/authentication records and generated GL/stock rows are not independent business roots. Compatible Company/Warehouse/account setup or explicit **Streaming Value Mapping** is required for transactions; the engine does not infer accounting identities.

A dedicated, supervised site agent polls healthy peers every second; after-commit jobs also wake delivery. The minute scheduler is recovery. Both sites need the same release and an approved non-Administrator execution user. Start an agent from a bench sites directory with `../env/bin/python -m arab_streaming.agent --site SITE` and supervise it with a service manager. Business saves perform no network requests.

Verified in isolated sites: 46 automated tests; real verified-HTTPS custom records with added/removed child rows and private attachments; sequential Both-direction edits; invoice submit/cancel with matching GL and no echo; ten Branch invoice recipient routes and forged-Branch rejection. This is the essential release requested after the user narrowed scope. Full ten-peer load/fault certification and the broader original 120-task acceptance matrix remain separate work. Advanced stock reconciliation is opt-in through `arab_streaming_advanced_stock_checks`; it is not part of this essential rollout.

Version 0.2.0 is deployed and migrated on both current sites. Automatic UOM delivery was verified in both directions; the test-Branch Sales Invoice rule passes schema/permission preflight. This does not claim live invoice posting or completion of every original requirement.

See [operator steps](docs/USER_GUIDE.md), [deployment evidence](docs/PROGRESS.md), and [the broader requirements](docs/DYNAMIC_BRANCH_SYNC_PLAN.md). Do not run the test suite against a working business site. Fresh installs remain inert until `arab_streaming_site_role` and `arab_streaming_enabled` are explicitly configured.

Saved Instance Key forms provide an Administrator-only **Show API Secret** button. Its temporary dialog clears on close; the saved Password remains encrypted and masked. Do not record credentials in documentation or logs.

Canonical source: local-personal2:/home/xxx/bench1/my-bench/apps/arab_streaming. Slave: contabo1:/home/jimmy/arab-streaming-test/apps/arab_streaming. Documentation retains the existing Git ignore policy. No Git commit or push is implied by deployment.

License: MIT.


### RMS consumer profile and application routing

RMS can prepare its general 58-rule profile without activating delivery. Its optional Store-to-Branch link routes POS wrappers; an installed `app:rms_store` resolver and existing Branch Assignment records route native ERP transactions. The engine supports installed `arab_streaming_branch_resolvers`, `arab_streaming_after_apply`, `arab_streaming_local_doctypes`, `arab_streaming_local_fields` and `arab_streaming_managed_branch_routing` hooks. Resolver callbacks receive `(document, config, peer=None, incoming=False)`; post-apply callbacks receive `(peer, source_document, target_document, config)`. These are server-installed extensions, never executable code from a peer payload.

Linked/app scopes are checked per document during copy and stock reconciliation; supplied paths do not create extra Branch fields. App-managed routing opts out of automatic dimensions, while ordinary deployments keep dimension setup. Operational naming-series documents receive writer/Branch partitions even when not submittable. Incoming amendment restrictions cannot be bypassed by disabled ordinary creation. Use matching app/engine code and complete first-pair lifecycle acceptance; the RMS site's prepared defaults have no connected peer and do not certify financial history import.

## First-run consumer bootstrap (0.2.1)

Installed consumers can register `arab_streaming_defer_initial_links` callbacks
returning optional Link/Table field names for a new authenticated bootstrap document.
The executor rejects mandatory/scalar fields and restores the original snapshot
through normal permissions and controllers in the same transaction. RMS uses this
only for first-run Store and Brand/Menu cycles; normal event execution is unchanged without an
explicit consumer decision. Company scaffolding and Setup Wizard ownership remain
in RMS. Use matching 0.2.1 engine/RMS sources for its Master/Slave first-run flow.

Generated execution-role assignments stay local during User transfer. Price List
execution includes its native default-list writes to Buying/Selling Settings with
scoped permissions. Failed initial copies retain a context-free diagnostic stack.
