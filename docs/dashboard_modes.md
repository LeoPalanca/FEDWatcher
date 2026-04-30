# Dashboard Modes

FedWatcher should support two presentation modes:

1. Clean app mode for the final working product.
2. Educational demo mode for fake statement experiments and course presentation.

## Clean App Mode

Clean mode is the normal public version at `fedwatcher.ellep.it`.

It should:

- use the official Federal Reserve website as the document source;
- hide fake-data controls;
- show the dashboard, signals, macro context, and document history;
- avoid admin-only upload/write controls.

## Educational Demo Mode

Educational mode is for testing and explaining the pipeline.

It should:

- let an admin choose between `official Fed` and `FakeFed`;
- make the currently selected source visible in the UI;
- allow an admin to write or update a synthetic FakeFed statement;
- trigger the ingestion pipeline against the selected source;
- clearly label synthetic content as fake/test content.

The source selector should be a modal or admin panel, not a normal public-user control.

## Admin Access

Admin-only actions:

- select source: official Fed or FakeFed;
- create/update a fake statement;
- run document ingestion;
- inspect ingestion status/errors.

Minimum protection for the course project:

- require an `API_TOKEN` or admin password from environment variables;
- never commit credentials;
- keep VM credentials only in `/Users/leonardo/FEDWatcher_Hide/.env`.

## Implementation Direction

The first implementation can be simple:

- FastAPI exposes protected admin endpoints.
- The dashboard opens an admin modal only after successful admin authentication.
- FakeFed write/update saves static HTML on the VM or writes through a controlled backend route.

For the final presentation, keep a clean mode toggle so the same codebase can demonstrate both:

- real app behavior;
- educational fake-statement behavior.
