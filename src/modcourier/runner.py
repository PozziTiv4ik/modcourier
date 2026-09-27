from dataclasses import asdict, replace

from .errors import CourierError
from .models import file_hashes, same_file
from .publication import reviewed_publication
from .state import atomic_json, now


def verify(connector, project, artifact, receipt):
    try:
        fresh_project = connector.get_project(project.id)
        if fresh_project is None:
            return replace(receipt, status="accepted_unverified")
        for remote in connector.files(fresh_project):
            if remote.id == receipt.id:
                if not same_file(artifact, remote):
                    raise CourierError("verification_failed", f"Remote file {receipt.id} does not match the expected hash.", uncertain=True)
                return remote
    except CourierError as exc:
        if exc.code == "verification_failed":
            raise
        # A receipt is durable evidence of acceptance, not proof of publication.
        return replace(receipt, status="accepted_unverified")
    return replace(receipt, status="accepted_unverified")


def execute(config, items, state, plan):
    results = []
    failed_platforms = set()
    projects = dict(plan.projects)
    for step in plan.steps:
        name = step.platform
        connector = plan.connectors[name]
        project = projects.get(name)
        base = {"platform": name, "action": step.action, "key": step.key}
        journal_key = name + ":update_page" if step.action == "verify_page" else step.key
        if step.action in {"blocked", "needs_browser"}:
            results.append({**base, "status": step.action, "message": step.reason, **step.details})
            failed_platforms.add(name)
            continue
        if name in failed_platforms:
            results.append({**base, "status": "not_attempted", "message": "An earlier step on this platform needs attention."})
            continue
        try:
            if reviewed_publication(config).sha256 != plan.publication_sha256:
                raise CourierError("publication_changed", "Publication text changed after planning. Inspect the release again.")
            if step.action == "create":
                state.record(step.key, status="started")
                project = connector.create_project(items)
                projects[name] = project
                # Record remote identity before updating the convenience config.
                state.record(step.key, status="accepted", project_id=project.id, url=project.url)
                state.bind(name, project.id)
                config.bind(name, project.id)
                connector.settings = config.settings(name)
                results.append({**base, "status": "draft_created", "url": project.url, "project_id": project.id})
            elif step.action == "update_page":
                state.record(step.key, status="started", project_id=project.id)
                project = connector.update_page(project)
                projects[name] = project
                state.record(step.key, status="accepted", language="en")
                results.append({**base, "status": "page_updated", "url": project.url, "language": "en"})
            elif step.action == "verify_page":
                current = connector.get_project(project.id)
                if current is None or connector.page_action(current):
                    raise CourierError("page_unverified", "The page changed during verification. Inspect again.")
                state.record(name + ":update_page", status="accepted", language="en")
                results.append({**base, "status": "page_verified", "url": current.url, "language": "en"})
            elif step.action in {"upload", "skip"}:
                if project is None:
                    raise CourierError("missing_project", "Cannot upload without a verified project.")
                if not config.settings(name).get("project_id"):
                    state.bind(name, project.id)
                    config.bind(name, project.id)
                artifact = step.artifact
                if file_hashes(artifact.path)["sha256"] != artifact.hashes["sha256"]:
                    raise CourierError("artifact_changed", f"{artifact.path.name} changed; inspect the release again.")
                if step.action == "upload":
                    state.record(step.key, status="started", project_id=project.id, sha256=artifact.hashes["sha256"])
                    receipt = connector.upload(project, artifact)
                    state.record(step.key, status="accepted", receipt=asdict(receipt))
                else:
                    receipt = step.remote
                receipt = verify(connector, project, artifact, receipt)
                state.record(step.key, status="accepted", project_id=project.id,
                             sha256=artifact.hashes["sha256"], receipt=asdict(receipt))
                results.append({**base, "status": receipt.status, "url": receipt.url,
                                "file_id": receipt.id, "sha256": artifact.hashes["sha256"]})
            elif step.action == "submit":
                state.record(step.key, status="started", project_id=project.id)
                status = connector.submit(project)
                state.record(step.key, status="accepted", remote_status=status)
                results.append({**base, "status": status, "url": project.url})
        except CourierError as exc:
            # Do not replace a confirmed upload receipt with an ordinary read error.
            if step.action != "skip":
                state.record(journal_key, status="uncertain" if exc.uncertain else "failed", error=exc.as_dict())
            results.append({**base, "status": "uncertain" if exc.uncertain else "failed", **exc.as_dict()})
            failed_platforms.add(name)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # A local failure after a POST must not turn the next run into a duplicate upload.
            record = state.operation(journal_key)
            uncertain = record.get("status") in {"started", "accepted"}
            error = CourierError("local_error", f"Operation could not finish: {type(exc).__name__}. Keep the journal and inspect again.",
                                 uncertain=uncertain)
            state.record(journal_key, status="uncertain" if uncertain else "failed", error=error.as_dict())
            results.append({**base, "status": "uncertain" if uncertain else "failed", **error.as_dict()})
            failed_platforms.add(name)
    report = {
        "schema_version": 1, "completed_at": now(), "complete": not failed_platforms,
        "results": results,
        "note": "Upload acceptance and pending moderation are not the same as public availability.",
    }
    atomic_json(state.directory / "report.json", report)
    return report
