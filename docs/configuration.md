# Configuration

Run `init` after building your mod. Keep `modcourier.json` in the **mod's directory**,
not in the publisher checkout. It contains public metadata and project IDs.
See [examples/modcourier.json](../examples/modcourier.json) for a complete example.

## Project

| Field | Purpose |
|---|---|
| `mod_id` | Must match the built JAR |
| `title`, `summary` | English project-page copy |
| `slug` | Stable project URL identity; do not rename it to translate prose |
| `body` or `body_file` | English project description; UTF-8 Markdown |
| `license` | SPDX ID, ARR, or LicenseRef-Custom; keep the author's actual license |
| `license_url` | Required with LicenseRef-Custom |
| `source_url` | Repository URL used to verify discovery matches |
| `issues_url`, `wiki_url`, `discord_url` | Optional project links |
| `icon` | Optional local icon path, relative to the mod directory |

New Modrinth pages are created from these fields; existing title/summary/body are
synchronized to the reviewed English copy. CurseForge page edits use the prepared
browser handoff. Preserve existing useful content while translating. Icons,
licenses, IDs and slugs are not changed by a page-copy update.

## Publication language

`publication.language` is fixed to `en`. After preparing and reading all outgoing
copy, run `review-language --language en`. It writes `publication.reviewed_sha256`;
do not invent this hash or mark untranslated text as reviewed. Later edits to
the title, summary, body or changelog require another review.

The agent performs the translation/review itself. The CLI enforces its recorded
content fingerprint and checks for obvious non-Latin prose; it is not an English
language detector. See [publication language](publication-language.md).

## Release and artifacts

- `release.type`: release, beta or alpha.
- `release.game_versions`: exact versions actually tested, such as ["1.21.1"].
- `release.environment`: select the client's/server's real requirements.
- `release.changelog` or `changelog_file`: English notes for **this** release.
  init prefers CHANGELOG.en.md, then CHANGELOG.md; translate and extract the current
  section if the source uses another language or includes history.

The environment values are:

| Value | Use |
|---|---|
| `client_and_server` | Required on both |
| `client_only` | Client only |
| `client_only_server_optional` | Client required, server optional |
| `singleplayer_only` | Singleplayer only |
| `server_only` | Server only |
| `server_only_client_optional` | Server required, client optional |
| `dedicated_server_only` | Dedicated server only |
| `client_or_server` | Either side independently |
| `client_or_server_prefers_both` | Either side, both preferred |

CurseForge receives its coarser Client/Server tags; Modrinth receives the full
version environment. Unknown environments are not guessed.

`artifacts` is a list of relative JAR paths or objects:

```json
{
  "path": "fabric/build/libs/demo-1.0.0.jar",
  "game_versions": ["1.21.1"],
  "environment": "client_only",
  "release_id": "1.0.0+fabric.mc1.21.1"
}
```

Only path is mandatory. Per-artifact fields override the common release settings.
Loaders, mod ID and mod version come from the actual JAR. By default, a stable
release ID combines mod version, loader and Minecraft versions, so variants remain
distinct. release_id can match an existing naming scheme when migrating.

If artifacts is omitted, discovery looks in build/libs and one-level submodules.
Different mod IDs or stale versions require explicit selection. Each release
artifact represents one mod; multi-mod JARs are rejected with an explanation.
Multi-loader JAR descriptors must agree on ID and version.

## Dependencies

Built-in game/loader/Java requirements are not uploaded as project dependencies.
Other dependencies discovered in metadata need platform mappings:

```json
"dependencies": {
  "fabric-api": {
    "modrinth": "fabric-api",
    "curseforge": "306612"
  },
  "internal-module": {
    "ignore": "Packaged inside this release JAR; no separate download"
  },
  "shared-library": {
    "modrinth": "verified-project-id",
    "curseforge": "verified-project-id",
    "type": "embedded"
  }
}
```

Supported type overrides: required, optional, incompatible, embedded. Mappings
are validated against the target platform. The tool never drops a dependency
because its lookup or upload failed.

## Platform settings

Both platforms are enabled by default. Use `enabled: false` or the repeatable
`--platform` CLI option to select a subset.

- Both: project_id, token_env.
- Modrinth: categories and disclosures_confirmed.
- CurseForge: author, slug, api_key_env, bootstrap_version and page_review.

Use `bind` to save project IDs and browser confirmations. bootstrap_version is
written by `bind curseforge ID --new`; it asserts that the new author project was
empty at that inspected version. It is not a switch for treating failed lookups
as empty projects.

`bind curseforge ID --page-confirmed` records the rendered page's correspondence
to the reviewed English copy. It writes page_review (expected and, when available,
observed remote digests). Editing the copy or remote page requires a new check.

## Files and JSON output

All referenced local files must be within the mod directory. Paths are resolved
before reading, including symlinks. Token values and arbitrary API endpoints are
not configurable.

`--json` produces a single JSON document with schema_version: 1:

- inspect / dry-run: ready, steps and artifacts.
- publish: complete, results and completion time.
- status: results with verified_now for fresh platform verification.
- expected errors: error.code, error.message and, where relevant, error.uncertain.

The journal records operations separately for each platform/release. A process
lock prevents simultaneous publishers in the same mod directory. Across separate
machines, configure CI concurrency or otherwise serialize releases; local locks
cannot coordinate independent computers.
