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
