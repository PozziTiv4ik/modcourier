"""Portable browser tasks: the agent uses its own browser, not private APIs."""
from .state import atomic_json
from .publication import Publication


def prepare(config, items, plan, directory):
    tasks = []
    publication = Publication.read(config)
    preview = publication.preview(config)
    for step in plan.steps:
        if step.action not in {"blocked", "needs_browser"}:
            continue
        if not preview["reviewed"]:
            instructions = [
                "Translate the project title, summary, complete description and current release changelog into English.",
                "Read the actual files referenced by body_file/changelog_file. Prefer separate README.en.md and CHANGELOG.en.md when the originals use another language.",
                "Preserve meaning, technical identifiers, links, attribution and warnings. Do not append translated language sections.",
                "Read every resulting field and run review-language --language en to record your review. The command does not translate or detect English for you.",
                "Run inspect/publish again. Do not paste the unreviewed source material into either website.",
            ]
        elif step.platform == "curseforge":
            instructions = [
                "Open https://authors.curseforge.com/ in the author's signed-in browser.",
                "Search My Projects including drafts, rejected and pending projects. Compare mod ID, source repository and owner; a title match is insufficient.",
                "If found, copy its numeric project ID and use the bind command. Do not use --new for an existing project.",
                "Create or update the page using only publication.fields for the English title, summary and body. Preserve the mod's license and technical identity.",
                "Verify the saved rendered page, then bind its numeric ID with --page-confirmed. Add --new and --slug only for a newly created EMPTY project.",
                "If catalog API access is unavailable for an existing project, inspect its Files tab and upload only missing variants through the website.",
                "Use publication.fields.changelog and each prepared release_name. Do not copy the original JAR title or untranslated release notes.",
                "Check file names, loader tags, Minecraft versions and moderation status. Record the page URLs in the conversation; do not report approval while pending.",
            ]
        else:
            instructions = [
                "Open the project's settings at https://modrinth.com/dashboard in the author's signed-in browser.",
                "Check ownership, drafts, moderation messages and any required content disclosures.",
                "Use only publication.fields for website copy and each prepared release_name. Publication pages and release notes must be English.",
                "For substantial AI assistance, set the applicable disclosures in the website. AI-generated page images and primarily AI-generated public projects are prohibited.",
                "Bind the verified project ID; add --disclosures-confirmed only after actually applying the required disclosures.",
                "Run inspect again, then publish if the request already authorizes publication.",
            ]
        tasks.append({"platform": step.platform, "reason": step.reason, "instructions": instructions})
    if not tasks:
        return
    document = {
        "schema_version": 1, "publication": preview,
        "project_settings": {k: v for k, v in config.project.items()
                             if k not in {"title", "summary", "body", "body_file"}},
        "artifacts": [{**a.as_dict(), "release_name": publication.release_name(a) if preview["reviewed"] else None}
                      for a in items],
        "tasks": tasks,
        "bind_example": "python <ModCourier>/modcourier.py bind curseforge PROJECT_ID --project <mod-directory>",
        "new_project_example": "python <ModCourier>/modcourier.py bind curseforge PROJECT_ID --new --slug SLUG --page-confirmed --project <mod-directory>",
    }
    atomic_json(directory / "handoff.json", document)
    lines = ["# ModCourier publication handoff", "",
             "All website copy must be English. Complete any translation/review before using the websites.", ""]
    for task in tasks:
        lines += ["## " + task["platform"], "", task["reason"], ""]
        lines += [f"{i}. {instruction}" for i, instruction in enumerate(task["instructions"], 1)]
        lines.append("")
    lines += ["## Prepared material", "", "See handoff.json beside this file for fields, artifacts and hashes.",
              "Credentials belong in local environment variables, never in these files.", ""]
    (directory / "handoff.md").write_text("\n".join(lines), encoding="utf-8")
