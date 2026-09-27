# Changelog

## Unreleased

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
