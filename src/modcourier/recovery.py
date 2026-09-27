"""Read-only release status and explicit recovery of uncertain uploads."""
from dataclasses import asdict

from .connectors import REGISTRY
from .errors import CourierError
from .guidance import next_actions
from .inspect import artifacts
from .models import RemoteFile, RESERVED_RELEASE_IDS, check_remote_file


def status(config, state):
    results = []
    connectors, projects = {}, {}
    for key, operation in state.data["operations"].items():
        if not operation.get("receipt"):
            if operation.get("status") in {"started", "uncertain", "failed"}:
                error = operation.get("error", {})
                results.append({"platform": key.split(":")[0], "key": key,
                                "status": operation["status"], "code": error.get("code", "local_error"),
                                "message": error.get("message", "Inspect the journal and author dashboard.")})
            continue
        name = key.split(":")[0]
        receipt = RemoteFile(**operation["receipt"])
        message = "Last acknowledged upload; current public status could not be verified."
        fresh = None
        error = None
        try:
            if name not in REGISTRY:
                raise CourierError("invalid_platform", f"No connector is installed for the journal platform {name}.")
            if name not in connectors:
                connectors[name] = REGISTRY[name](config)
            connector = connectors[name]
            project_key = (name, operation["project_id"])
            if project_key not in projects:
                projects[project_key] = connector.get_project(operation["project_id"])
            project = projects[project_key]
            fresh = connector.find_file(project, receipt.id, receipt) if project else None
            if fresh:
                receipt = fresh
                message = "Verified against the platform."
                check_remote_file(fresh)
        except CourierError as exc:
            message = str(exc)
            error = exc
        fallback = operation["status"] if operation["status"] in {"started", "uncertain", "failed"} else "accepted_unverified"
        if error and error.code in {"verification_failed", "metadata_conflict"}:
            fallback = "uncertain" if error.code == "verification_failed" else "failed"
        results.append({"platform": name, "key": key, "status": receipt.status if fresh else fallback,
                        "last_known_status": operation["receipt"]["status"],
                        "journal_status": operation["status"],
                        "project_id": operation["project_id"], "file_id": receipt.id,
                        "url": receipt.url, "message": message, "verified_now": fresh is not None,
                        **({"code": error.code} if error else {})})
    return {"schema_version": 1, "results": results, "next_actions": next_actions(results),
            "message": "No recorded uploads." if not results else "Recorded release status."}


def recover(config, state, args):
    key = args.platform + ":" + args.release_id
    previous = state.operation(key)
    if args.release_id in RESERVED_RELEASE_IDS or not previous.get("sha256") or not previous.get("project_id"):
        raise CourierError("not_upload", "Recovery applies only to file uploads. For uncertain project creation, locate the draft and bind its ID.")
    if previous.get("status") not in {"started", "uncertain"}:
        raise CourierError("not_uncertain", "Only an uncertain upload can be recovered.")
    if args.absent:
        state.record(key, status="failed", receipt=None, recovery={"absent": True, "evidence": args.evidence})
        return {"message": "Absence recorded. The next publish may attempt this upload again."}
    items = artifacts(config)
    artifact = next((a for a in items if a.key == args.release_id), None)
    if not artifact or artifact.hashes["sha256"] != previous.get("sha256"):
        raise CourierError("artifact_changed", "Recovery requires the exact original artifact.")
    connector = REGISTRY[args.platform](config)
    project = connector.get_project(previous["project_id"])
    if not project:
        raise CourierError("project_inaccessible", "The project is not visible to the API. Keep the journal and verify in the dashboard.")
    remote = connector.find_file(project, args.file_id, artifact)
    if not remote:
        raise CourierError("verification_failed", "Remote file does not match the original artifact hash.")
    check_remote_file(remote)
    state.record(key, status="accepted", receipt=asdict(remote), recovery={"evidence": args.evidence})
    return {"message": "Recovered the acknowledged file; a repeated publish will not duplicate it."}
