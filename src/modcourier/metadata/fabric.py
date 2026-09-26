def parse(data):
    license_value = data.get("license", "")
    if isinstance(license_value, list):
        license_value = license_value[0] if len(license_value) == 1 else ""
    environment = {"client": "client_only", "server": "server_only"}.get(data.get("environment"), "")
    deps = [(key, "required") for key in data.get("depends", {})]
    deps += [(key, "optional") for key in data.get("recommends", {})]
    deps += [(key, "incompatible") for key in data.get("breaks", {})]
    return dict(
        mod_id=data["id"], title=data.get("name", data["id"]), version=data["version"],
        loaders=["fabric"], minecraft=data.get("depends", {}).get("minecraft", ""),
        description=data.get("description", ""), license=license_value,
        environment=environment, dependencies=deps,
    )
