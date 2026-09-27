# Extend ModCourier

The CLI, planner and runner use the same normalized data. Connectors own the
platform-specific wire format and capabilities.

## Add a platform

1. Add a Connector subclass in src/modcourier/connectors/.
2. Implement discover, files, validate, upload and get_project. Declare the
   credential variable names/purposes for the local doctor check.
3. Declare capabilities. Implement create_project only for a documented supported
   flow. Return an actionable browser_required error when a website step is needed.
   Override needs_submission(project) and submit(project) if drafts require a
   separate submission. The planner must not check platform names to decide this.
   Keep page_translations false unless a native locale-aware page interface has
   been verified. All primary publication copy remains English.
4. Return RemoteProject and RemoteFile objects. Statuses must distinguish an
   acknowledgement, moderation and public availability.
5. Register the connector in REGISTRY and the platform name in config.PLATFORMS.
   Extend the platform configuration validation and credential documentation.
6. Add local HTTP fixtures for successful upload, failed read, duplicate release,
   partial completion and a lost response after server acceptance.

Reuse Http for bounded responses, credential redaction, streaming multipart
uploads and explicit handling of uncertain writes. API hosts are fixed in
connectors; user config cannot redirect credentials elsewhere.
Decorate public API operations with api_operation(); use write=True for mutations
so malformed acknowledgements cannot become permission to retry a write.

files_for(project, file_id) returns all files represented by that platform ID.
Its default implementation filters files(project); override it with a direct
lookup where supported. Modrinth IDs identify versions, which can contain more
than one file. The shared find_file method selects by the strongest common hash.
The CLI's recovery/status commands and the runner use this same contract.
Never treat incomplete pagination, malformed responses or a failed read as absence.

Use Connector.publication() for every outgoing title, summary, description and
changelog; its release_name method deliberately avoids untranslated JAR titles.
Implement page_action/update_page for existing page copy or return a browser
handoff. Recheck the content review before any remote mutation.

The planner performs reads only. Remote mutations happen in runner, with a
journal entry persisted **before** each request and a receipt persisted as soon
as the platform acknowledges it.

## Add a loader

Add a reader under metadata/ and connect its descriptor path in read_artifact.
Return the common metadata shape. Read the built archive without extracting or
executing it. Keep metadata reads bounded. Do not infer runtime compatibility
from the filename or expand a dependency range into untested Minecraft releases.

## Add another interface

A future GitHub Action, MCP server or GUI should call the same planner and runner.
The CLI's --json output is already suitable for agent integration. Such interfaces
are optional: the publisher remains runnable from a local checkout or .pyz.
Reuse guidance.next_action for actionable failures instead of inventing separate
instructions in each interface. doctor.ready_for_inspect is local readiness only;
remote discovery and validation still belong to inspect.

## Verify

```sh
python -m unittest discover -s tests -v
python scripts/build_release.py
python dist/modcourier.pyz --version
```

Tests use unittest and a local HTTP server. They do not need keys, network access
to mod platforms or paid services. Keep authenticated upload checks separate and
report them accurately.
