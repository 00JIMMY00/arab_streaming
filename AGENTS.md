# Working instructions for `elsakr_app`

Read [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md), [docs/DECISIONS.md](docs/DECISIONS.md), and [docs/PROGRESS.md](docs/PROGRESS.md) before making a change. Check the relevant source files too; the documentation is a guide, not a substitute for the code.

For each meaningful change to this app or its deployment:

1. Update `docs/PROGRESS.md` with the date, what changed, how it was checked, and any remaining work. Put the newest entry first. Distinguish local verification from a production deployment.
2. Update `docs/DECISIONS.md` when a lasting technical or operational choice is made. Record the reason and consequences; do not log routine edits as decisions.
3. Update `docs/PROJECT_CONTEXT.md` when the architecture, app list, environments, or deployment process changes.
4. Keep `README.md` and `CLIENT_UPDATES.md` aligned with user-facing or operational changes.

Use dates in `YYYY-MM-DD` format. Describe only observed results. Do not record credentials, tokens, passwords, private customer data, or backup contents in these files. Before marking an item deployed, verify the workflow result and the live site.

This repository contains a Frappe v15 app and the deployment configuration for the single Elsakr site. Follow the existing project style and run focused checks for files you change. Treat edits to `apps.yaml`, `.github/workflows/ci.yml`, and `deploy/` as production-impacting changes.
