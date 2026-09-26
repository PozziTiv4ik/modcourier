def parse(data):
    loader = data["quilt_loader"]
    metadata = loader.get("metadata", {})
    license_value = metadata.get("license", "")
    if isinstance(license_value, dict):
        license_value = license_value.get("id", "")
    if isinstance(license_value, list):
        license_value = license_value[0] if len(license_value) == 1 and isinstance(license_value[0], str) else ""
    dependencies = []
    minecraft = ""
    for dep in loader.get("depends", []):
        if isinstance(dep, str):
            dep = {"id": dep}
        if isinstance(dep, list):
            raise ValueError("Quilt dependency alternatives require an unambiguous release descriptor.")
        if dep["id"] == "minecraft":
            minecraft = dep.get("versions", "")
        dependencies.append((dep["id"], "optional" if dep.get("optional") else "required"))
    return dict(
        mod_id=loader["id"], title=metadata.get("name", loader["id"]),
        version=loader["version"], loaders=["quilt"], minecraft=minecraft,
        description=metadata.get("description", ""), license=license_value,
        environment="", dependencies=dependencies,
    )
