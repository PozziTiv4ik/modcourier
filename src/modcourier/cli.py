import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from . import __version__
from .config import Config, PLATFORMS
from .connectors import REGISTRY
from .connectors.base import segment
from .errors import CourierError
from .handoff import prepare
from .inspect import artifacts, local_issues, suggested_config
from .models import same_file
from .planner import build_plan
from .publication import Publication
from .runner import execute
from .state import State, atomic_json


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=argparse.SUPPRESS, help="Mod directory (default: current directory)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Stable machine-readable JSON")
    cli = argparse.ArgumentParser(prog="modcourier", parents=[common], description="Publish Minecraft Java mods with an auditable, resumable plan.")
    cli.add_argument("--version", action="version", version="ModCourier " + __version__)
    commands = cli.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", parents=[common], help="Create configuration from built release JARs")
    init.add_argument("--artifact", action="append", help="Relative path to a selected JAR (repeat for variants)")
    inspect = commands.add_parser("inspect", parents=[common], help="Read metadata and discover remote releases")
    inspect.add_argument("--offline", action="store_true", help="Inspect local files without contacting platforms")
    inspect.add_argument("--platform", choices=PLATFORMS, action="append")
    publish = commands.add_parser("publish", parents=[common], help="Publish the current release or resume it")
    publish.add_argument("--dry-run", action="store_true", help="Read-only plan; no local or remote writes")
    publish.add_argument("--platform", choices=PLATFORMS, action="append")
    commands.add_parser("status", parents=[common], help="Read the current remote status of recorded uploads")
    review = commands.add_parser(
        "review-language", parents=[common],
        help="Record the agent/author's English review of the exact publication text",
        description="After reading and translating all publication copy, record that it is English. "
                    "This command does not translate or automatically detect its language.",
    )
    review.add_argument("--language", choices=["en"], required=True, help="Attest that you reviewed the copy as English")
    bind = commands.add_parser("bind", parents=[common], help="Save a project ID verified by the author/agent")
    bind.add_argument("platform", choices=PLATFORMS)
    bind.add_argument("project_id")
    bind.add_argument("--new", action="store_true", help="CurseForge only: browser-verified NEW and EMPTY project")
    bind.add_argument("--slug", help="Verified CurseForge URL slug")
    bind.add_argument("--disclosures-confirmed", action="store_true", help="Modrinth: required disclosures were applied in the website")
    bind.add_argument("--page-confirmed", action="store_true", help="CurseForge: the rendered page matches the reviewed English copy")
    recover = commands.add_parser("recover", parents=[common], help="Resolve an uncertain upload after inspecting the author dashboard")
    recover.add_argument("platform", choices=PLATFORMS)
    recover.add_argument("release_id", help="Exact release ID from inspect/status")
    recovery = recover.add_mutually_exclusive_group(required=True)
    recovery.add_argument("--file-id", help="Verify an existing remote file by ID and hash")
    recovery.add_argument("--absent", action="store_true", help="Record a browser-verified absence, enabling a fresh upload")
    recover.add_argument("--evidence", required=True, help="Author dashboard URL and a short explanation of what was checked")
    return cli


def emit(value, machine=False):
    if machine:
        output = json.dumps(value, ensure_ascii=False, indent=2)
    else:
        lines = ["ModCourier " + __version__]
        if "message" in value:
            lines.append(value["message"])
        for problem in value.get("issues", []):
            lines.append("  ! " + problem)
        for item in value.get("artifacts", []):
            lines.append(f"  {item['title']} {item['version']} | {', '.join(item['loaders'])} | {', '.join(item['game_versions']) or 'Minecraft versions needed'}")
        for step in value.get("steps", value.get("results", [])):
            lines.append(f"  {step['platform']}: {step.get('status', step.get('action', 'unknown'))} — "
                         f"{step.get('reason', step.get('message', step.get('key', '')))}")
            if step.get("url"):
                lines.append("    " + step["url"])
        if value.get("handoff"):
            lines.append("  Browser handoff: " + value["handoff"])
        if value.get("error"):
            lines.append("  " + value["error"]["code"] + ": " + value["error"]["message"])
        if value.get("note"):
            lines.append(value["note"])
        if value.get("publication"):
            publication = value["publication"]
            lines.append("  Publication language: English; " + ("reviewed" if publication["reviewed"] else "translation/review needed"))
            if publication.get("issue"):
                lines.append("  " + publication["issue"]["message"])
        output = "\n".join(lines)
    # Defense in depth: never emit active token values, even in platform error text.
    for key, secret in os.environ.items():
        if secret and len(secret) >= 8 and any(term in key.upper() for term in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
            output = output.replace(secret, "[REDACTED]")
    print(output)


def ensure_ignore(root):
    path = root / ".gitignore"
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    if ".modcourier/" not in text.splitlines():
        path.write_text(text.rstrip("\n") + "\n.modcourier/\n", encoding="utf-8")


def status(config, state):
    results = []
    by_platform = {}
    for key, operation in state.data["operations"].items():
        if not operation.get("receipt"):
            if operation.get("status") in {"started", "uncertain", "failed"}:
                results.append({"platform": key.split(":")[0], "key": key,
                                "status": operation["status"], "message": "Inspect the journal and author dashboard."})
            continue
        name = key.split(":")[0]
        receipt = operation["receipt"]
        message = "Last acknowledged upload; current public status could not be verified."
        fresh = None
        try:
            if name not in by_platform:
                connector = REGISTRY[name](config)
                project = connector.get_project(operation["project_id"])
                by_platform[name] = connector.files(project) if project else []
            fresh = next((f for f in by_platform[name] if f.id == receipt["id"]
                          and any(f.hashes.get(h) == value for h, value in receipt["hashes"].items())), None)
            if fresh:
                receipt = asdict(fresh)
                message = "Verified against the platform."
        except CourierError as exc:
            message = str(exc)
        results.append({"platform": name, "key": key, "status": receipt["status"] if fresh else "accepted_unverified",
                        "last_known_status": operation["receipt"]["status"],
                        "url": receipt["url"], "message": message, "verified_now": fresh is not None})
    return {"schema_version": 1, "results": results,
            "message": "No recorded uploads." if not results else "Recorded release status."}


def recover(config, state, args):
    key = args.platform + ":" + args.release_id
    previous = state.operation(key)
    if previous.get("status") not in {"started", "uncertain"}:
        raise CourierError("not_uncertain", "Only an uncertain upload can be recovered.")
    if args.absent:
        state.record(key, status="failed", recovery={"absent": True, "evidence": args.evidence})
        return {"message": "Absence recorded. The next publish may attempt this upload again."}
    items = artifacts(config)
    artifact = next((a for a in items if a.key == args.release_id), None)
    if not artifact or artifact.hashes["sha256"] != previous.get("sha256"):
        raise CourierError("artifact_changed", "Recovery requires the exact original artifact.")
    connector = REGISTRY[args.platform](config)
    project = connector.get_project(previous["project_id"])
    if not project:
        raise CourierError("project_inaccessible", "The project is not visible to the API. Keep the journal and verify in the dashboard.")
    if args.platform == "curseforge":
        data = connector.api.get(f"/v1/mods/{segment(project.id)}/files/{segment(args.file_id)}")["data"]
        remote = connector.remote_file(data, project)
    else:
        remote = next((f for f in connector.files(project) if f.id == args.file_id and same_file(artifact, f)), None)
    if not remote or not same_file(artifact, remote):
        raise CourierError("verification_failed", "Remote file does not match the original artifact hash.")
    state.record(key, status="accepted", receipt=asdict(remote), recovery={"evidence": args.evidence})
    return {"message": "Recovered the acknowledged file; a repeated publish will not duplicate it."}


def dispatch(args):
    root = Path(getattr(args, "project", ".")).resolve()
    if not root.is_dir():
        raise CourierError("missing_project", "The --project directory does not exist.")
    config = Config(root)
    if args.command == "init":
        if config.path.exists():
            raise CourierError("config_exists", "modcourier.json already exists; edit it instead of overwriting it.")
        if args.artifact:
            config = Config(root, {"artifacts": args.artifact})
        items = artifacts(config)
        raw = suggested_config(root, items)
        atomic_json(config.path, raw)
        ensure_ignore(root)
        return {"message": "Created modcourier.json. Prepare English title, summary, description and changelog; "
                           "fill release details, then run review-language --language en after reading the copy.",
                "artifacts": [a.as_dict() for a in items], "config": raw}, 0
    if args.command == "review-language":
        if not config.path.exists():
            raise CourierError("missing_config", "Run init once to create modcourier.json.")
        with State(root).lock():
            config = Config(root)
            publication = Publication.read(config).validate(config, require_review=False)
            config.raw["publication"] = {"language": "en", "reviewed_sha256": publication.sha256}
            atomic_json(config.path, config.raw)
        return {"message": "Recorded your English-language review. Changes to the text require a new review.",
                "publication": publication.preview(config)}, 0
    if args.command == "status":
        return status(config, State(root)), 0
    if args.command == "bind":
        if args.platform == "curseforge" and not args.project_id.isdigit():
            raise CourierError("invalid_id", "CurseForge needs the numeric project ID from its author dashboard.")
        if args.new and args.platform != "curseforge":
            raise CourierError("invalid_argument", "--new applies only to browser-created CurseForge projects.")
        state = State(root)
        with state.lock():
            state = State(root)
            existing = config.settings(args.platform).get("project_id") or state.data["projects"].get(args.platform)
            if existing and str(existing) != args.project_id:
                raise CourierError("binding_conflict", "This directory is already bound to another project.")
            extra = {}
            if args.new:
                if any(k.startswith(args.platform + ":") for k in state.data["operations"]):
                    raise CourierError("not_empty", "--new cannot be used after a recorded operation on that platform.")
                if not args.slug:
                    raise CourierError("missing_slug", "--new requires the project's verified --slug.")
                extra["bootstrap_version"] = artifacts(config)[0].version
            if args.slug:
                extra["slug"] = args.slug
            if args.disclosures_confirmed:
                if args.platform != "modrinth":
                    raise CourierError("invalid_argument", "--disclosures-confirmed applies to Modrinth.")
                extra["disclosures_confirmed"] = True
            if args.page_confirmed:
                if args.platform != "curseforge":
                    raise CourierError("invalid_argument", "--page-confirmed applies to CurseForge.")
                extra["page_review"] = REGISTRY["curseforge"](config).confirm_page(args.project_id)
            config.bind(args.platform, args.project_id, **extra)
            Config(root)  # Validate the persisted binding before it can be used.
            state.bind(args.platform, args.project_id)
        return {"message": f"Bound {args.platform} to {args.project_id}. Run inspect to verify the release plan."}, 0
    if args.command == "recover":
        with State(root).lock():
            return recover(config, State(root), args), 0
    items = artifacts(config)
    if args.command == "inspect" and args.offline:
        return {"schema_version": 1, "artifacts": [a.as_dict() for a in items],
                "publication": Publication.read(config).preview(config),
                "issues": local_issues(config, items), "message": "Local inspection; platforms were not contacted."}, 0
    if not config.path.exists():
        raise CourierError("missing_config", "Run init once to create modcourier.json.")
    if args.command == "inspect" or args.dry_run:
        plan = build_plan(config, items, State(root), args.platform)
        return {**plan.as_dict(), "artifacts": [a.as_dict() for a in items],
                "publication": Publication.read(config).preview(config)}, 0 if plan.as_dict()["ready"] else 2
    with State(root).lock():
        # All reads involved in a publish are repeated under the exclusive lock.
        config = Config(root)
        items = artifacts(config)
        state = State(root)
        plan = build_plan(config, items, state, args.platform)
        prepare(config, items, plan, state.directory)
        report = execute(config, items, state, plan)
        if any(step.action in {"blocked", "needs_browser"} for step in plan.steps):
            report["handoff"] = str(state.directory / "handoff.md")
        return report, 0 if report["complete"] else 2


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result, code = dispatch(args)
    except CourierError as exc:
        result, code = {"schema_version": 1, "error": exc.as_dict()}, 2
    except KeyboardInterrupt:
        result, code = {"message": "Interrupted. Keep .modcourier/state.json; inspect before resuming."}, 130
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result, code = {"error": {"code": "invalid_input", "message": f"{type(exc).__name__}: {exc}"}}, 2
    emit(result, getattr(args, "json", False))
    return code
