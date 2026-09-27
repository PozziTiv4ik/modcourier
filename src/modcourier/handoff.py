"""Portable browser tasks: the agent uses its own browser, not private APIs."""
from .state import atomic_json
from .publication import Publication
from .guidance import next_action


def prepare(config, items, plan, directory, report=None):
    tasks = []
    publication = Publication.read(config)
    preview = publication.preview(config)
    entries = report["results"] if report is not None else [
        {"platform": step.platform, "status": step.action, "message": step.reason, **step.details}
        for step in plan.steps
    ]
    for entry in entries:
        if entry["status"] not in {"blocked", "needs_browser", "failed", "uncertain"}:
            continue
        code = entry.get("code", "browser_required")
        if entry["status"] == "uncertain" and entry.get("action") in {"create", "upload"}:
            code = "uncertain_creation" if entry["action"] == "create" else "uncertain_upload"
        if not preview["reviewed"]:
            code = preview.get("issue", {}).get("code", "publication_review_required")
        tasks.append({**next_action(code, entry.get("message", ""), entry["platform"]),
                      "reason": entry.get("message", "")})
    if not tasks:
        for name in ("handoff.json", "handoff.md"):
            (directory / name).unlink(missing_ok=True)
        return None
    document = {
        "schema_version": 1, "publication": preview,
        "project_settings": {k: v for k, v in config.project.items()
                             if k not in {"title", "summary", "body", "body_file"}},
        "platform_settings": {name: {key: value for key, value in config.settings(name).items()
                                    if key in {"project_id", "slug", "categories", "author"}}
                              for name in config.enabled()},
        "dependencies": config.raw.get("dependencies", {}),
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
    return str(directory / "handoff.md")
