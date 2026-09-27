# Changelog

## Unreleased

- Add a read-only local doctor command and shared next_actions for agent onboarding.
- Simplify connector submission and file lookup contracts; remove platform API
  details from CLI recovery and platform-name branches from the planner.
- Correct CurseForge catalog file/project status enums and check download
  availability before reporting publication.
- Prevent repeated platform arguments and reserved release IDs from duplicating
  uploads or overwriting project-operation journal entries.
- Require complete pagination, valid lookup payloads and matching project identity.
- Verify all files in a Modrinth version, using the strongest common hash for
  publishing, recovery and status; retain acknowledged receipts after rejection.
- Preserve unknown compatibility for loader variants and universal JARs.
- Include execution failures in handoffs, remove resolved handoffs and return
  structured JSON even for argument errors.
- Consolidate project documentation in English.
- Require reviewed English title, summary, description and changelog on both platforms.
- Tie language reviews to exact text/file contents and recheck before sending.
- Use the English project title for release display names instead of JAR metadata.
- Synchronize existing Modrinth page copy; prepare and verify CurseForge page edits
  through the author website.
- Document that neither connector supports native translated page variants;
  CurseForge Localization applies to project-file strings, not page descriptions.

## 1.0.0

- Standalone Python CLI and reproducible single-file .pyz distribution.
- Fabric, Forge, NeoForge and Quilt metadata readers.
- Modrinth project creation, release upload and review submission.
- CurseForge author uploads, catalog discovery and first-project browser handoff.
- Shared release planning, verified bindings and explicit dependency mappings.
- Per-platform journal, atomic writes and interrupted-upload reconciliation.
- Read-only previews, machine-readable output and current release status.
- Initial bilingual quick starts and a portable agent entry point.
- Dependency-free scenario tests and cross-platform CI.
