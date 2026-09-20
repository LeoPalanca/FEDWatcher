# Dashboard Modes

FedWatcher ships one codebase with two configurations, selected by the `FAKEFED_ENABLED`
environment variable.

| | `FAKEFED_ENABLED` unset/false | `FAKEFED_ENABLED=true` |
|---|---|---|
| Intended use | public deploy (`fedwatcher.ellep.it`) | self-hosted instance, course demo |
| Document source | official Federal Reserve only | official Fed + synthetic FakeFed |
| `POST`/`DELETE /api/fakefed/statements` | 404 | available, password-protected |
| `MonitorFakeFedAgent` | refuses to run | ingests FakeFed statements |
| `/api/documents`, `/api/snapshot` | synthetic rows filtered out | all rows |
| Dashboard header | no source control | FakeFed source toggle |

## Public Mode (default)

The public site is read-only: dashboard, signals, macro context, and document history over
official Fed documents. No admin controls, no synthetic content, no write endpoints. The
footer links to the repository so anyone can run the full pipeline themselves.

## Self-Host / Demo Mode

Set `FAKEFED_ENABLED=true` (and `FAKEFED_PUBLISH_PASSWORD`) in `.env`. This is the mode
used to explain and test the pipeline:

- the dashboard shows a FakeFed source toggle, merging synthetic statements into the feed;
- an admin can write or update a synthetic FakeFed statement through the API;
- the ingestion pipeline can be triggered against the FakeFed source;
- synthetic content is labelled as fake test content everywhere it appears.

## Admin Access

Admin-only actions (self-host mode only):

- create/update/delete a fake statement;
- run document ingestion;
- inspect ingestion status/errors.

Protection:

- `FAKEFED_PUBLISH_PASSWORD` is required on every write, sent as `X-Fakefed-Password`;
- `FAKEFED_ENABLED` must be true, otherwise the routes do not exist at all;
- never commit credentials; VM credentials stay in `/Users/leonardo/FEDWatcher_Hide/.env`.

## Implementation

- `app/main.py`: `fakefed_enabled()` / `require_fakefed_enabled()` guard the write routes;
  `hide_fakefed_rows()` filters synthetic documents out of the read paths; `/api/health`
  reports the flag.
- `fedwatcher/assets/explorer.js`: `renderSourceSwitch()` injects the FakeFed control only
  when `/api/health` reports `fakefed: true` (fail-closed if the fetch fails).
- `agents/monitor_fakefed.py`: `main()` exits unless the flag is set.
- `fakefed.ellep.it` stays deployed with `X-Robots-Tag: noindex` and no `/api/` proxy, so
  the feature can be re-enabled later without redeploying the fixture site.
