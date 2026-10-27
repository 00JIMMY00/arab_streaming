# Arab-ERP Streaming

Selected ERPNext data synchronization between a master site and branch sites, built for Frappe/ERPNext v15.

Version `0.1.0.dev0` implements the foundation: eight core DocTypes, Streaming Settings, durable internal state and replay identities, explicit site-role guards, ERPNext Branch participation, secure Instance Key generation and Administrator-only secret access. The protocol validator preserves 64-bit sequences as decimal strings and validates bounded, hashed JSON envelopes.

Replication is still under development. Link/Ping, authentication, HTTP transport, source capture, business execution, initial copy, files, stock/accounting adapters and event retention are not available yet. There are no business document hooks or synchronization schedules in this release. Installation leaves the site role unconfigured and does not create connections, keys or business rules.

## Development installation

Use a dedicated test bench and fresh test database. Install ERPNext v15 first, then this app:

```sh
bench --site <TEST_SITE> install-app erpnext
bench --site <TEST_SITE> install-app arab_streaming
bench --site <TEST_SITE> set-config allow_tests true
bench --site <TEST_SITE> run-tests --app arab_streaming --skip-test-records
```

The site-specific configuration key `arab_streaming_site_role` accepts `Master` or `Slave`. Leaving it absent keeps the app unconfigured; invalid values fail explicitly. Do not configure the working master or enable production replication until the documented isolated acceptance gates pass.

Streaming Settings preserves the documented defaults: retention 7 days (blank/zero means effective 60), stock snapshot age 900 seconds (minimum 600 when strict mode is enabled), strict action validation on and strict stock validation on. These settings do not implement stock reconciliation or cleanup by themselves.

ERPNext Branch gains `custom_streaming_enabled`, initially off. New Master Instance forms suggest UOM and Item Group in the initial-copy table; ongoing tables remain empty unless a registered provider contributes explicit rows. Migration preserves saved settings, branch selection, connection rules and installation identity.

## Verification and project records

The canonical repository is `local-personal2:/home/xxx/bench1/my-bench/apps/arab_streaming`. The isolated bench is `/home/xxx/bench1/arab-streaming-test`, with `streaming-master.test` and `streaming-slave.test`, separate MariaDB/Redis services and a paused scheduler. No production migration is part of this foundation delivery.

See [docs/README.md](docs/README.md), [docs/IMPLEMENTATION_CONTRACT.md](docs/IMPLEMENTATION_CONTRACT.md), and [docs/PROGRESS.md](docs/PROGRESS.md) for scope, chosen contracts, task evidence and remaining work. The existing `docs/` ignore rule is preserved; selected project documents must be explicitly force-added when a future commit is prepared. Nothing is staged or committed by this coding session.

The CI workflow targets the v15 branches and installs ERPNext before this app. Local verification does not imply that GitHub Actions has run.

License: MIT.
