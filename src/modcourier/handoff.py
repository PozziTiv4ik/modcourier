"""Portable browser tasks: the agent uses its own browser, not private APIs."""
from .state import atomic_json


def prepare(config, items, plan, directory):
    tasks = []
    for step in plan.steps:
        if step.action not in {"blocked", "needs_browser"}:
            continue
        if step.platform == "curseforge":
            instructions = [
                "Open https://authors.curseforge.com/ in the author's signed-in browser.",
                "Search My Projects including drafts, rejected and pending projects. Compare mod ID, source repository and owner; a title match is insufficient.",
                "If found, copy its numeric project ID and use the bind command. Do not use --new for an existing project.",
                "If no project exists, create a Minecraft Java / Mods project using the prepared title, summary, description, license, icon and categories.",
                "For a newly created EMPTY project, bind its numeric ID with --new and --slug. This assertion is restricted to the inspected mod version.",
                "If catalog API access is unavailable for an existing project, inspect its Files tab and upload only missing variants through the website.",
                "Check file names, loader tags, Minecraft versions and moderation status. Record the page URLs in the conversation; do not report approval while pending.",
            ]
        else:
            instructions = [
                "Open the project's settings at https://modrinth.com/dashboard in the author's signed-in browser.",
                "Check ownership, drafts, moderation messages and any required content disclosures.",
                "For substantial AI assistance, set the applicable disclosures in the website. AI-generated page images and primarily AI-generated public projects are prohibited.",
                "Bind the verified project ID; add --disclosures-confirmed only after actually applying the required disclosures.",
                "Run inspect again, then publish if the request already authorizes publication.",
            ]
        tasks.append({"platform": step.platform, "reason": step.reason, "instructions": instructions})
    if not tasks:
        return
    document = {
        "schema_version": 1, "project": config.project,
        "description": config.text("project", "body"),
        "changelog": config.text("release", "changelog"),
        "artifacts": [a.as_dict() for a in items], "tasks": tasks,
        "bind_example": "python <ModCourier>/modcourier.py bind curseforge PROJECT_ID --project <mod-directory>",
        "new_project_example": "python <ModCourier>/modcourier.py bind curseforge PROJECT_ID --new --slug SLUG --project <mod-directory>",
    }
    atomic_json(directory / "handoff.json", document)
    lines = ["# ModCourier browser handoff", "", "The CLI needs the following platform-specific actions.", ""]
    for task in tasks:
        lines += ["## " + task["platform"], "", task["reason"], ""]
        lines += [f"{i}. {instruction}" for i, instruction in enumerate(task["instructions"], 1)]
        lines.append("")
    lines += ["## Prepared material", "", "See handoff.json beside this file for fields, artifacts and hashes.",
              "Credentials belong in local environment variables, never in these files.", ""]
    (directory / "handoff.md").write_text("\n".join(lines), encoding="utf-8")
