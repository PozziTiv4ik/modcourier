# Publisher audit — 2026-09-27

Reviewed the CLI, configuration, all four metadata readers, English-publication
checks, both connectors, HTTP transport, planning, execution, recovery, journal,
handoffs, documentation, standalone build and CI.

## Findings and changes

| Scenario | Previous risk | Corrected behavior |
|---|---|---|
| Repeated --platform arguments | Two uploads planned before either completed | One plan per selected platform |
| No enabled targets | Empty run reported success | Actionable configuration error |
| Custom release ID such as create | File receipt overwrote a project operation | Reserved IDs rejected |
| CurseForge catalog statuses | Incorrect status values, including malware reported as publication | Official enums; availability and early-access checks |
| Rejection immediately after upload | Run reported complete | Failure reported while the acknowledged receipt is preserved |
| Modrinth version with multiple files | First file compared even if another matched | Correct file selected by hash within the verified project/version |
| Status verification | Any matching hash could mask a stronger mismatch | Strongest common hash used consistently |
| Missing pagination or malformed lookup | Incomplete reads could imply absence, or stop both platforms | Block affected platform; keep independent work |
| Variant initialization | Unknown loader environment inherited the first variant | Unknown values preserved for explicit completion |
| Universal JAR descriptors disagree | First descriptor supplied compatibility | Agreement required before inferring compatibility |
| Generic or stale handoff | Token errors led to browser instructions; old tasks persisted | Shared typed guidance; runtime failures included; resolved tasks removed |
| File recovery used on project creation | Uncertain creation could be cleared | Recovery limited to actual upload operations |
| First agent visit | Repeated trial-and-error commands | Local doctor check plus next_actions with focused documentation |

## Connector contract

Shared code decides whether to create, upload, skip, verify or stop.
Connectors supply normalized projects/files, complete discovery, validation,
page updates and submission requirements. Direct file lookups and platform
response parsing remain inside the connector. No third-party runtime dependency,
translation service, server or model provider was added.

## Verification and limits

The scenario suite uses the real CLI and HTTP transport against local fixture
servers. It covers accepted uploads, uncertain writes, repeat runs, partial
completion, the audit cases above and portable application execution outside the
checkout.

Live read-only checks performed: Modrinth's game-version, loader, category and
license catalogs, plus the public Fabric API project and a direct version lookup
to verify project identity, file shape, status and SHA-512 availability.

Authenticated production uploads and signed-in browser actions were not exercised
by this audit. Moderation and private-project access still depend on the author's
accounts and the platforms. Separate machines must serialize their own releases;
a local journal lock cannot coordinate independent checkouts.

The status mappings and pagination checks follow the official
[CurseForge catalog reference](https://docs.curseforge.com/rest-api/#filestatus),
including ModStatus, FileStatus, isAvailable and Pagination. Multi-file version
verification follows [Modrinth's version response](https://docs.modrinth.com/api/operations/getversion/).
