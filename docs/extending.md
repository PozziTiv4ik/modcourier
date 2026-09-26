# Extend ModCourier

The CLI, planner and runner use the same normalized data. Connectors own the
platform-specific wire format and capabilities.

## Add a platform

1. Add a Connector subclass in src/modcourier/connectors/.
2. Implement discover, files, validate, upload and get_project.
3. Declare capabilities. Implement create_project only for a documented supported
   flow. Return an actionable browser_required error when a website step is needed.
4. Return RemoteProject and RemoteFile objects. Statuses must distinguish an
   acknowledgement, moderation and public availability.
5. Register the connector in REGISTRY and the platform name in config.PLATFORMS.
   Extend the platform configuration validation and credential documentation.
6. Add local HTTP fixtures for successful upload, failed read, duplicate release,
   partial completion and a lost response after server acceptance.

Reuse Http for bounded responses, credential redaction, streaming multipart
uploads and explicit handling of uncertain writes. API hosts are fixed in
connectors; user config cannot redirect credentials elsewhere.

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

## Verify

```sh
python -m unittest discover -s tests -v
python scripts/build_release.py
python dist/modcourier.pyz --version
```

Tests use unittest and a local HTTP server. They do not need keys, network access
to mod platforms or paid services. Keep authenticated upload checks separate and
report them accurately.
