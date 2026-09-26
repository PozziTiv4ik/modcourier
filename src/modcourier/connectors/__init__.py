from .modrinth import Modrinth
from .curseforge import CurseForge

REGISTRY = {"modrinth": Modrinth, "curseforge": CurseForge}
