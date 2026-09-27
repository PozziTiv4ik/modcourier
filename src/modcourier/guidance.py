"""One set of next steps for CLI errors, plans and saved handoffs."""

DOCS = "https://github.com/PozziTiv4ik/modcourier/blob/main/docs/"


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
