"""Read-only local onboarding; no account requests or filesystem changes."""
from .connectors import REGISTRY
from .errors import CourierError
from .guidance import next_action
from .inspect import artifacts, local_issues
from .publication import Publication


def diagnose(config, selected=None):
    checks, actions, credentials = [], [], []

    def issue(name, error):
        checks.append({"name": name, "status": "needs_attention", **error.as_dict()})
        actions.append(next_action(error.code, str(error)))

    if not config.path.exists():
        issue("configuration", CourierError("missing_config", "Run init to create modcourier.json after building the mod."))
    else:
        checks.append({"name": "configuration", "status": "ok"})
    try:
        items = artifacts(config)
        problems = local_issues(config, items) if config.path.exists() else []
        if problems:
            issue("artifacts", CourierError("release_metadata", " ".join(problems)))
        else:
            checks.append({"name": "artifacts", "status": "ok", "count": len(items)})
    except CourierError as exc:
        issue("artifacts", exc)
    if config.path.exists():
        try:
            Publication.read(config).validate(config)
            checks.append({"name": "publication", "status": "ok", "language": "en"})
        except CourierError as exc:
            issue("publication", exc)
    for name in config.enabled(selected):
        for credential in REGISTRY[name].credential_status(config):
            credentials.append(credential)
            if not credential["present"]:
                actions.append(next_action("credential_missing",
                    f"Set {credential['variable']} for {credential['purpose']}.", name))
    return {
        "schema_version": 1,
        "ready_for_inspect": all(check["status"] == "ok" for check in checks)
                             and all(c["present"] or c["browser_fallback"] for c in credentials),
        "checks": checks, "credentials": credentials, "next_actions": actions,
        "message": "Local checks only. Credential presence does not verify account access or permission to publish.",
    }
