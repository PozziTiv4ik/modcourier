def parse(data, manifest, loader="forge"):
    mods = data.get("mods", [])
    if len(mods) != 1:
        raise ValueError("JARs containing multiple mods require separate publishing artifacts.")
    mod = mods[0]
    version = str(mod.get("version", "")).replace("$" + "{file.jarVersion}", manifest.get("Implementation-Version", ""))
    dependencies = []
    minecraft = ""
    entries = data.get("dependencies", {}).get(mod["modId"], [])
    if any(e.get("modId") == "neoforge" for e in entries):
        loader = "neoforge"
    for entry in entries:
        mod_id = entry["modId"]
        if mod_id == "minecraft":
            minecraft = entry.get("versionRange", "")
        kind = entry.get("type", "required" if entry.get("mandatory", True) else "optional")
        if kind not in {"required", "optional", "incompatible"}:
            kind = "optional"
        dependencies.append((mod_id, kind))
    return dict(
        mod_id=mod["modId"], title=mod.get("displayName", mod["modId"]), version=version,
        loaders=[loader], minecraft=minecraft, description=mod.get("description", "").strip(),
        license=data.get("license", ""), environment="", dependencies=dependencies,
    )
