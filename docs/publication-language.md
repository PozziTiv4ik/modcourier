# English website publications

ModCourier's documentation and CLI are in English. Every publication uses English
for the project title, summary, description, release display name and changelog.
The chat language, operating-system locale and JAR's display name do not select
the language sent to a website. Mod IDs, slugs, filenames and in-game translations
remain technical/source data; publishing does not rewrite the mod binary.

## Agent workflow

1. Read the current page, mod metadata and release notes.
2. Write the English title/summary in modcourier.json. Translate the complete
   description and current release notes; preserve meaning, links, requirements,
   credits and warnings. An English description must still describe the actual mod.
3. For non-English source documents, keep the originals and point body_file and
   changelog_file to English files such as README.en.md and CHANGELOG.en.md.
4. Read every outgoing field, including visible link labels and image captions.
   Run `review-language --language en --project <mod>`.
5. Run inspect/publish as usual. The agent can perform the review itself; this is
   not an extra request for the user's permission.

No external translation service, model key or Python package is required. The
calling agent translates; a terminal-only user supplies their own English copy.
The CLI cannot independently translate arbitrary prose.

## What the check establishes

The review command records `publication.language: "en"` and a SHA-256 digest of
the exact title, summary, body and changelog. File-backed fields are hashed by
their contents. Editing any of those fields invalidates the review. Payloads are
checked again before mutation, and release names use the reviewed English title.
There is no fallback to untranslated JAR metadata.

The CLI also rejects non-Latin prose as an obvious translation problem while
allowing Unicode typography, accented Latin names, URLs and literal code.
This is a conservative script check, **not a language detector**: French, German
or Spanish can use the same alphabet as English. The agent/author must actually
read and review the text before recording the attestation. A falsely asserted
review cannot prove a text's language. Use English/transliterated display names,
and retain literal technical identifiers in code spans or code blocks.

Old configurations remain readable but need their first English review before
publishing. inspect --offline --json includes the exact copy and any outstanding
language issue. publish and dry-run block unreviewed copy before remote requests.

## Existing pages

Modrinth can update title, summary and body via the official API. A publish plan
shows an update_page step when they differ from the reviewed English copy.
Icons, license, project ID and slug are not changed by that step.

CurseForge's author Upload API does not document editing project-page text.
The agent must update the page in the author website and compare the rendered
result with the English copy, then run:

```sh
python modcourier.py bind curseforge PROJECT_ID --page-confirmed --project /path/to/mod
```

Where the catalog is available, the CLI checks title/summary and records the
observed page digest. This supports HTML generated from reviewed Markdown without
guessing that the two source formats are identical. Changes to the expected copy
or the observed page invalidate that browser review. A changed changelog alone
does not require checking an unchanged project page again.

For a new empty page still absent from the catalog, --page-confirmed records the
agent/author's browser attestation. It is scoped to the reviewed page copy and
bound project. It is not independent API verification. Once the page is visible,
its actual content must match or be checked again. --new may be combined with
--page-confirmed only for a verified new and empty project.

English checks apply to all newly sent copy and the current project page. Existing
historical file changelogs are not bulk-edited by a normal new-release publication.

## Native multilingual support: checked 2026-09-27

| Platform | Documented behavior | ModCourier behavior |
|---|---|---|
| Modrinth | One title/summary/body per project; English description required, other translations may be included in that body | Publish one English page |
| CurseForge | One project-page description; English required and other languages may follow it | Publish one English page |
| CurseForge Localization | Phrase keys and per-locale strings for project files, including Lua substitution/export | Do not treat it as translated website pages |

The documented APIs do not expose separate locale variants for these publication
fields. Translating the site's own interface or a mod's in-game strings is a
different capability. Therefore no automatic set of popular languages is selected,
and no multilingual blocks are appended to the description.

Both connectors declare `page_translations: false`. A future connector should
enable native page translations only after verifying a real supported interface;
English remains the primary copy. Do not use a browser's translate button or
duplicate projects to simulate native language variants.

Sources:

- [Modrinth content rules, section 2.2](https://modrinth.com/legal/rules)
- [Modrinth project editing API](https://docs.modrinth.com/api/operations/modifyproject/)
- [CurseForge moderation policies, English content](https://support.curseforge.com/support/solutions/articles/9000197279)
- [CurseForge project localization](https://support.curseforge.com/support/solutions/articles/9000197356-project-localization)
- [CurseForge localization substitutions](https://support.curseforge.com/support/solutions/articles/9000197354-localization-substitutions)
- [CurseForge author Upload API](https://support.curseforge.com/support/solutions/articles/9000197321)

Apply the platform's current AI-content disclosures to any generated page text
or translations when required; language review does not replace those disclosures.
