from .forge import parse as parse_forge


def parse(data, manifest):
    return parse_forge(data, manifest, loader="neoforge")
