"""Roblox material library metadata bundled with the Blender add-on.

The library is intentionally limited to the 43 materials exported from Roblox
Studio in ``assets/roblox_materials``.  It does not fall back to generated
procedural approximations when an asset is missing.
"""

from __future__ import annotations

from pathlib import Path


MATERIAL_DEFINITIONS = (
    ("Plastic", "Plastic", "Plastic1"),
    ("SmoothPlastic", "Smooth Plastic", "Smoothplastic1"),
    ("Concrete", "Concrete", "Concrete1"),
    ("Brick", "Brick", "Brick1"),
    ("Wood", "Wood", "Wood1"),
    ("WoodPlanks", "Wood Planks", "Woodplanks1"),
    ("Metal", "Metal", "Metal1"),
    ("CorrodedMetal", "Corroded Metal", "Corrodedmetal1"),
    ("DiamondPlate", "Diamond Plate", "Diamondplate1"),
    ("Glass", "Glass", "Glass1"),
    ("Neon", "Neon", "Neon1"),
    ("Slate", "Slate", "Slate1"),
    ("Granite", "Granite", "Granite1"),
    ("Marble", "Marble", "Marble1"),
    ("Sand", "Sand", "Sand1"),
    ("Ice", "Ice", "Ice1"),
    ("Fabric", "Fabric", "Fabric1"),
    ("Foil", "Foil", "Foil1"),
    ("Basalt", "Basalt", "Basalt1"),
    ("CrackedLava", "Cracked Lava", "Crackedlava1"),
    ("Limestone", "Limestone", "Limestone1"),
    ("Pavement", "Pavement", "Pavement1"),
    ("Pebble", "Pebble", "Pebble1"),
    ("Cobblestone", "Cobblestone", "Cobblestone1"),
    ("Rock", "Rock", "Rock1"),
    ("Sandstone", "Sandstone", "Sandstone1"),
    ("Grass", "Grass", "Grass1"),
    ("LeafyGrass", "Leafy Grass", "Leafygrass1"),
    ("Snow", "Snow", "Snow1"),
    ("Mud", "Mud", "Mud1"),
    ("Ground", "Ground", "Ground1"),
    ("Asphalt", "Asphalt", "Asphalt1"),
    ("Salt", "Salt", "Salt1"),
    ("Glacier", "Glacier", "Glacier1"),
    ("ForceField", "Force Field", "Forcefield1"),
    ("Cardboard", "Cardboard", "Cardboard1"),
    ("Carpet", "Carpet", "Carpet1"),
    ("CeramicTiles", "Ceramic Tiles", "Ceramictiles1"),
    ("ClayRoofTiles", "Clay Roof Tiles", "Clayrooftiles1"),
    ("RoofShingles", "Roof Shingles", "Roofshingles1"),
    ("Leather", "Leather", "Leather1"),
    ("Plaster", "Plaster", "Plaster1"),
    ("Rubber", "Rubber", "Rubber1"),
)

MATERIAL_ITEMS = tuple(
    (name, label, f"Roblox {label} material")
    for name, label, _folder in MATERIAL_DEFINITIONS
)
MATERIAL_FOLDERS = {name: folder for name, _label, folder in MATERIAL_DEFINITIONS}
MATERIAL_NAMES = tuple(MATERIAL_FOLDERS)

# Roblox OBJ exports map one texture repetition across eight studs.
TEXTURE_TILE_STUDS = 8.0

# The texture export does not fully describe the renderer model.  These values
# only supply the optical properties which cannot be represented by the image
# maps themselves; the visible pattern always comes from the bundled files.
METALLIC_MATERIALS = frozenset({"Metal", "CorrodedMetal", "DiamondPlate", "Foil"})
TRANSMISSIVE_MATERIALS = frozenset({"Glass", "Ice", "Glacier"})
EMISSIVE_MATERIALS = {"Neon": 2.5, "ForceField": 1.3}
TEXTURE_DISABLED_MATERIALS = frozenset({"Plastic", "SmoothPlastic"})
FIXED_ROUGHNESS = {
    "Plastic": 0.8,
    "SmoothPlastic": 0.25,
}


def asset_root() -> Path:
    return Path(__file__).resolve().parent / "assets" / "roblox_materials"


def material_directory(material_name: str) -> Path:
    try:
        folder = MATERIAL_FOLDERS[material_name]
    except KeyError as error:
        raise ValueError(f"Unsupported Roblox material: {material_name}") from error
    return asset_root() / folder


def material_files(material_name: str) -> dict[str, Path | None]:
    """Return the bundled MTL and exported texture maps for one material."""

    directory = material_directory(material_name)
    textures = directory / "textures"

    def first(suffix: str) -> Path | None:
        matches = sorted(textures.glob(f"*_{suffix}.png")) if textures.is_dir() else []
        return matches[0] if matches else None

    mtls = sorted(directory.glob("*.mtl"))
    textures_enabled = material_name not in TEXTURE_DISABLED_MATERIALS
    return {
        "mtl": mtls[0] if mtls else None,
        # Plastic and SmoothPlastic are deliberately represented only by the
        # selected color and a fixed roughness. Their exported image maps must
        # not affect the Blender preview.
        "diffuse": first("diff") if textures_enabled else None,
        "normal": first("nmap") if textures_enabled else None,
        "specular": first("spec") if textures_enabled else None,
    }


def parse_mtl(path: Path | None) -> dict:
    """Read the small subset of Wavefront MTL used by Studio's export."""

    result = {"diffuse": (1.0, 1.0, 1.0), "specular": (0.04, 0.04, 0.04), "shininess": 0.0}
    if path is None or not path.is_file():
        return result
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = raw_line.strip().split()
        if not parts:
            continue
        try:
            if parts[0] == "Kd" and len(parts) >= 4:
                result["diffuse"] = tuple(float(value) for value in parts[1:4])
            elif parts[0] == "Ks" and len(parts) >= 4:
                result["specular"] = tuple(float(value) for value in parts[1:4])
            elif parts[0] == "Ns" and len(parts) >= 2:
                result["shininess"] = float(parts[1])
        except ValueError:
            continue
    return result
