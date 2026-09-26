# Accounts, credentials and first publication

ModCourier runs on the author's computer or CI runner. Each author provides their
own platform credentials. A public copy of ModCourier never includes a shared key.

## Modrinth

Create a personal access token in [Modrinth settings](https://modrinth.com/settings/pats).
Allow reading the relevant user/projects/versions, uploading versions, and, for a
new project, creating and editing projects. The write endpoints document
`PROJECT_CREATE`, `PROJECT_WRITE` and `VERSION_CREATE`. Use the least access needed
for the operations you will run.

Set `MODRINTH_TOKEN` in the process environment. Existing organization/team projects
may be bound explicitly, but the account must have an accepted membership and the
platform must permit the requested operation.

For a new project, provide title, summary, body, license and 1–3 Modrinth categories
in modcourier.json. Category names come from
[the official catalog](https://api.modrinth.com/v2/tag/category). An optional icon is
a local path inside the mod directory. Creation produces a draft, uploads its
release files, and submits the completed project for moderation.

### AI disclosures

Record the actual creation process in `policy.ai_usage`:

| Value | Meaning |
|---|---|
| `none` | No generative AI contribution |
| `assisted` | Minor help such as questions, review or small refactors |
| `substantial` | Substantial generated code, assets or text; disclosures required |
| `primary` | Primarily generated content; public publication on Modrinth is blocked |
| `unknown` | The agent must determine the real situation first |

If required disclosures cannot be set through the documented API, use the website.
Create the draft in the website if needed; apply disclosures there, then run:

```sh
python modcourier.py bind modrinth PROJECT_ID --disclosures-confirmed --project /path/to/mod
```

This records a completed browser step, not permission to bypass a rule. Recheck
disclosures when the project's content changes. Images for page icons, banners and
gallery must satisfy the current platform rules.

[Current AI policy](https://support.modrinth.com/en/articles/16551575-disclosure-and-usage-of-ai)
and [content rules](https://modrinth.com/legal/rules).

## CurseForge: two different credentials

1. Create an **author Upload API token** from your CurseForge author account.
   Set `CURSEFORGE_UPLOAD_TOKEN`. This authorizes uploads to projects you manage.
2. The **catalog API key** is separate. Apply through
   [CurseForge's API program](https://support.curseforge.com/support/solutions/articles/9000208346).
   Set `CURSEFORGE_API_KEY`. Discovery, dependency resolution and verification use it.

Never substitute one credential for the other. Read access also depends on project
distribution settings. A 404 can mean unavailable content, not a nonexistent mod.
Automatic unbound discovery additionally needs `platforms.curseforge.author` to
match the public author name, plus an exact source-repository match.

### A new CurseForge project

The documented author API uploads files to an existing project. It does not
document creating a Minecraft project page.

1. Open [the author dashboard](https://authors.curseforge.com/) in a signed-in browser.
2. Check all your projects, including drafts and pending submissions.
3. If none is the intended mod, create a Minecraft Java **Mods** project using the
   prepared material in .modcourier/handoff.json.
4. Copy the numeric project ID and URL slug.
5. If this is a **new and empty** project, run:

```sh
python modcourier.py bind curseforge 123456 --new --slug my-mod --project /path/to/mod
python modcourier.py publish --project /path/to/mod
```

The --new assertion is scoped to the currently inspected mod version. It permits
the first upload while the new project is still absent from the public catalog.
The journal prevents a second upload after acknowledgement. Other versions need
normal discovery once the project is visible.

For an existing project, omit --new:

```sh
python modcourier.py bind curseforge 123456 --project /path/to/mod
```

If your existing project cannot be read through the catalog, the agent must check
and, when necessary, upload via the author website. This path depends on the
agent's browser tools and signed-in account; the standalone CLI cannot automate
arbitrary browsers. Use the prepared fields and hashes, then verify file status.

## Set credentials without committing them

In PowerShell, enter a token using a masked prompt:

```powershell
$secure = Read-Host "Modrinth token" -AsSecureString
$env:MODRINTH_TOKEN = [System.Net.NetworkCredential]::new("", $secure).Password
$secure.Dispose()
```

Repeat with the appropriate variable for CurseForge. The values exist in this
terminal session. For CI, use the runner's secret store. Custom variable names can
be set through `token_env` and `api_key_env`; those fields contain names, never values.
ModCourier does not automatically load .env files.

## What a completed run means

- `published`: the platform currently reports the file available.
- `pending_moderation`: uploaded and awaiting review.
- `uploaded` / `accepted_unverified`: acknowledgement received; public visibility
  has not been independently verified yet.
- `blocked` / `needs_browser`: a specific prerequisite needs attention.
- `uncertain`: the request may have succeeded; reconcile before retrying.

Exit code 0 from publish means all planned operations completed, including skips.
It does **not** mean the moderation team has approved everything. Code 2 means
at least one action needs attention; code 130 means the command was interrupted.

## API references

- [Modrinth overview](https://docs.modrinth.com/api/)
- [Create project](https://docs.modrinth.com/api/operations/createproject/)
- [Create version](https://docs.modrinth.com/api/operations/createversion/)
- [CurseForge author Upload API](https://support.curseforge.com/support/solutions/articles/9000197321)
- [CurseForge catalog API](https://docs.curseforge.com/rest-api/)

Implementation checked against these interfaces on 2026-09-26. Platform policy
and access requirements can change independently of ModCourier.
