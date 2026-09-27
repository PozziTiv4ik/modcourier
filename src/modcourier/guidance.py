"""One set of next steps for CLI errors, plans and saved handoffs."""
from .models import ATTENTION_STATUSES, REJECTED_STATUSES

DOCS = "https://github.com/PozziTiv4ik/modcourier/blob/main/docs/"
BROWSER_CODES = {"browser_required", "page_review_required", "catalog_unavailable", "disclosures_required", "remote_attention"}
BLOCKING_STATUSES = {"blocked", "needs_browser", "failed", "uncertain", "started"}
WAITING_STATUSES = {"uploaded", "accepted_unverified", "pending_moderation", "processing", "scheduled", "early_access"}


def result_action(result):
    """The same result produces the same guidance in plans, reports and handoffs."""
    status = result.get("status", result.get("action"))
    code = result.get("code") or result.get("details", {}).get("code")
    message = result.get("message") or result.get("reason", "")
    if status in {"started", "uncertain"}:
        action = result.get("action") or result.get("key", "").partition(":")[2]
        if action == "create":
            code = "uncertain_creation"
        elif action in {"update_page", "verify_page"}:
            code = "page_unverified"
        elif action == "submit":
            code = "submission_unverified"
        else:
            code = code if code == "verification_failed" else "uncertain_upload"
    elif status in REJECTED_STATUSES and status != "failed":
        code = "remote_rejected"
        message = message or f"The remote file is {status}."
    elif status in ATTENTION_STATUSES:
        code = "remote_attention"
        message = message or f"The remote file is {status}."
    elif status in WAITING_STATUSES:
        code = code or "verification_pending"
        message = message or f"The upload is {status}; public availability is not yet confirmed."
    elif status not in BLOCKING_STATUSES and not code:
        return None
    action = next_action(code or "local_error", message, result.get("platform"))
    return {**action, **{key: result[key] for key in ("key", "project_id", "file_id", "url") if result.get(key)}}


def next_actions(results):
    return [action for result in results if (action := result_action(result)) is not None]


def next_action(code, message, platform=None):
    kind, page = "configure", "configuration.md"
    instructions = [message, "Correct the reported input, then run inspect --json again."]
    if code in {"missing_config", "config_exists"}:
        instructions = [message, "Use init for a new mod directory; edit modcourier.json for an existing configuration."]
    elif code in {"no_artifacts", "missing_artifact", "multiple_versions", "artifact_changed", "invalid_artifact"}:
        kind = "build"
        instructions = [message, "Build and test the mod using its own instructions, then select the intended release JARs."]
    elif code.startswith("publication_"):
        kind, page = "review_copy", "publication-language.md"
        instructions = [
            message,
            "Read and translate the title, summary, full description and changelog, including referenced files, into English.",
            "Preserve technical identifiers, links, attribution and warnings; do not append other languages.",
            "Run review-language --language en after reviewing every field, then inspect --json.",
            "Do not paste unreviewed source material into either website.",
        ]
    elif code in {"missing_token", "authentication", "credential_missing"}:
        kind, page = "credentials", "setup.md"
        instructions = [message, "Configure the named environment variable locally, or check the existing token's account and scopes.",
                        "Never paste credentials into chat, source files or a handoff. Run inspect --json again."]
    elif code in {"uncertain_upload", "uncertain_creation", "verification_failed"}:
        kind, page = "reconcile", "recovery.md"
        instructions = [
            message, "Keep .modcourier/state.json and inspect the signed-in author's projects and files, including pending uploads.",
            "For an upload, verify its original hash with recover --file-id. Use recover --absent only after checking the author dashboard.",
            "For project creation, locate and bind the existing draft. Never clear the journal or blindly repeat a write.",
        ]
    elif code in {"version_conflict", "metadata_conflict", "remote_rejected", "project_rejected",
                  "project_inaccessible", "binding_conflict", "source_mismatch", "ownership"}:
        kind, page = "resolve_conflict", "recovery.md"
        instructions = [message, "Check the bound project, release and moderation messages in the author's dashboard.",
                        "Preserve acknowledged uploads and the journal. Correct the identity, compatibility tags or release before inspecting again."]
    elif code in {"policy_blocked", "policy_input"}:
        kind, page = "policy", "setup.md"
        instructions = [message, "Check the platform rules and record AI usage truthfully; do not change disclosures to bypass a restriction."]
    elif code == "page_review_required":
        kind, page = "browser", "publication-language.md"
        instructions = [message,
                        "Update the bound project's title, summary and description using the reviewed English copy; preserve its useful content.",
                        "Verify the rendered page, then run bind curseforge PROJECT_ID --page-confirmed and inspect --json."]
    elif code == "disclosures_required":
        kind, page = "browser", "setup.md"
        instructions = [message,
                        "Apply the required AI disclosures in the project's settings, then bind modrinth PROJECT_ID --disclosures-confirmed.",
                        "If this is the first release, check existing drafts before creating a draft in the author website.",
                        "Run inspect --json again."]
    elif code == "catalog_unavailable":
        kind, page = "browser", "setup.md"
        instructions = [message,
                        "Configure CURSEFORGE_API_KEY (or the configured api_key_env) locally to enable API inspection.",
                        "For the browser route, check the bound project's Files tab, including pending files; upload only missing releases using the reviewed English fields.",
                        "Retain file URLs and moderation status. An unavailable catalog never proves absence."]
    elif code == "remote_attention":
        kind, page = "browser", "recovery.md"
        instructions = [message, "Inspect this file's status and moderation messages in the existing project's author dashboard.",
                        "Resolve the file or draft before running inspect --json again. Keep the journal and do not upload another copy."]
    elif code in {"page_unverified", "submission_unverified"}:
        kind, page = "retry_read", "recovery.md"
        instructions = [message, "Keep the journal and run inspect --json to reconcile the project's current page or submission status.",
                        "If it remains unresolved, inspect the existing project in the author dashboard."]
    elif code == "verification_pending":
        kind, page = "verify", "recovery.md"
        instructions = [message, "Run status --json later to check this acknowledged upload; do not upload it again.",
                        "If it remains unavailable, check the existing file and moderation messages in the author dashboard."]
    elif code in {"browser_required", "ambiguous_project", "slug_taken"}:
        kind, page = "browser", "setup.md"
        instructions = [
            message, "Check existing projects, including drafts, in the signed-in author dashboard; verify owner and source repository.",
            "Bind an existing project's verified ID. Create a project only after verifying that none exists.",
            "Use only the reviewed English fields and release names. Preserve useful existing page content.",
        ]
        if platform == "curseforge":
            instructions += [
                "Verify the saved English page, then bind curseforge PROJECT_ID --page-confirmed.",
                "Add --new --slug SLUG only for a newly created EMPTY project you actually verified.",
                "Without catalog access, check the Files tab and complete only missing uploads in the website; retain file URLs and moderation status.",
            ]
        elif platform == "modrinth":
            instructions += ["Apply any required disclosures in project settings before using bind --disclosures-confirmed."]
        instructions += ["Run inspect --json again, then publish if already authorized."]
    elif code in {"network", "invalid_response", "response_size", "listing_incomplete", "search_incomplete"} or code.startswith("http_"):
        kind, page = "retry_read", "recovery.md"
        instructions = [message, "Rerun the read-only inspect command when the service responds normally.",
                        "A failed or incomplete lookup is not evidence that a project or release is absent."]
    elif code in {"local_error", "locked", "invalid_state", "state_version"}:
        kind, page = "reconcile", "recovery.md"
        instructions = [message, "Keep the journal. Resolve the local problem, then inspect before attempting another publish."]
    return {"code": code, "kind": kind, **({"platform": platform} if platform else {}),
            "instructions": instructions, "documentation": DOCS + page}
