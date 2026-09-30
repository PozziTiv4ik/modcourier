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

The follow-up refactor replaces stateful validate/upload coupling with
validate_project, prepare_uploads and upload(project, artifact, upload_data).
Prepared payloads belong to the plan; another inspection cannot replace them.
The CLI delegates status and recovery to a separate module. All paths share the
same hash and compatibility check, and result guidance has one implementation.

## Follow-up findings in 1.1.0

| Scenario | Previous risk | Corrected behavior |
|---|---|---|
| Another plan reused a connector | Pending metadata silently replaced or read from later config | Each plan carries its prepared upload data |
| Page/submission resume | Unrelated version catalogs and dependencies could block it | Project/policy checks are separate from upload preparation |
| Matching resource pack during automatic discovery | Bound-project type check was bypassed | Discovered projects must also be Java mods |
| Empty object as a file list or incomplete author record | Malformed data could imply no existing release/project | Validate record lists and required identity fields |
| Foreign project in a version listing | Shared code could consider unrelated files | Check project ID on listings and upload acknowledgements too |
| Empty Modrinth draft version | Its occupied release identity disappeared from planning | Resolve the draft before another upload |
| CurseForge changes during pagination | Repeated/omitted files could hide an existing release | Require stable totals and unique IDs |
| Invalid identity/hash or an unknown status | Broken remote data could become a receipt or success | Validate identities/hashes and require a recognized usable status |
| Status after compatibility changes | Matching bytes could still be reported as published | Compare saved loader/Minecraft tags as well |
| Rejected acknowledgement followed by failed read | Acceptance could mask rejection | Keep the receipt and report the rejection |
| Project rejected during submission | File acceptance could leave the overall report complete | Report the project failure and preserve successful file receipts |
| Status and browser guidance | Missing actions or unrelated project-creation advice | Specific action codes with operation/file context |
| Corrupt operation state or binding | Journal entries could reach execution unchecked | Report invalid_state before use |

## CI notification review — 2026-09-30

The repository had one remaining notification, for the initial
[failed CI run](https://github.com/PozziTiv4ik/modcourier/actions/runs/36235535954).
Its six Windows/macOS jobs failed in
`test_init_preserves_variant_specific_versions`: artifact paths were resolved,
but the project root still used an alias. Windows used a short directory name;
macOS used `/var` for a directory resolved under `/private/var`. Comparing these
paths with `relative_to` raised `ValueError`. All three Linux jobs passed.

[Commit 9e08b2f](https://github.com/PozziTiv4ik/modcourier/commit/9e08b2f4d42ac66a8e83335ae1a08d40f747e4af)
already fixed this by resolving the root before generating artifact paths.
The four subsequent CI runs succeeded, including all nine jobs in the
[1.1.0 run](https://github.com/PozziTiv4ik/modcourier/actions/runs/36321701320).
No further application change was needed for this notification.

Two dedicated regression scenarios now check the same failure condition:
generating and reloading multiple variants through a root containing `..`, and
reading artifact paths plus English description/changelog files through a
directory symlink. The first scenario deterministically reproduces the original
`ValueError` on the current machine when the normalization fix is removed in
memory; the source files remain unchanged. The symlink scenario runs where the
OS permits link creation and explicitly skips known permission/availability
restrictions, including Windows error 1314.

Local verification for this review used Windows and Python 3.13.13: all 124
unittest scenarios completed successfully, with only the directory-symlink
scenario skipped for insufficient privileges. Compilation and the standalone
build passed. The built archive's SHA-256 matched `SHA256SUMS`; `--version`,
`--help`, `init` and offline `inspect` ran successfully in a temporary directory
outside the checkout with isolated Python and `PYTHONPATH` cleared.

The GitHub review was limited to this repository: notification threads,
workflow/job results and failure logs, issues, pull requests, comments on the
failed commit and security-alert endpoints. There were no issues, pull requests,
commit comments or open secret-scanning alerts. Code scanning had no analysis,
and Dependabot alerts were disabled; these responses are not a vulnerability
assessment. Neither mod platform was contacted or used for a production upload.

## Verification and limits

The scenario suite uses the real CLI and HTTP transport against local fixture
servers. It covers accepted uploads, uncertain writes, repeat runs, partial
completion, the audit cases above and portable application execution outside the
checkout.

The 1.1.0 local run passed 122 unittest scenarios on Windows with Python 3.13.13.
The portable archive was built and executed from a temporary directory outside
the checkout, with PYTHONPATH cleared and user site packages disabled.

Live read-only checks performed for 1.1.0: Modrinth's game-version, loader, category and
license catalogs, plus the public Fabric API project and a direct version lookup
through the actual connector to verify project identity, file shape, status and
SHA-512 availability. These checks are reproducible with scripts/smoke_api.py.

Authenticated production uploads and signed-in browser actions were not exercised
by this audit. Moderation and private-project access still depend on the author's
accounts and the platforms. Separate machines must serialize their own releases;
a local journal lock cannot coordinate independent checkouts.

The status mappings and pagination checks follow the official
[CurseForge catalog reference](https://docs.curseforge.com/rest-api/#filestatus),
including ModStatus, FileStatus, isAvailable and Pagination. Multi-file version
verification follows [Modrinth's version response](https://docs.modrinth.com/api/operations/getversion/).
