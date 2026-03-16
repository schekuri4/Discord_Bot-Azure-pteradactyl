"""Minecraft version manifest and mod/plugin type detection."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import aiohttp

MOJANG_VERSION_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"

# Popular versions to show at the top of the picker
POPULAR_VERSIONS = [
    "1.21.4", "1.21.3", "1.21.1", "1.20.4", "1.20.1",
    "1.19.4", "1.18.2", "1.16.5", "1.12.2", "1.8.9",
]


async def fetch_release_versions(limit: int = 50) -> list[str]:
    """Fetch Minecraft release versions from Mojang's manifest."""
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(MOJANG_VERSION_MANIFEST, timeout=timeout) as resp:
                if resp.status != 200:
                    return list(POPULAR_VERSIONS)
                data = await resp.json()
                versions = [
                    v["id"]
                    for v in data.get("versions", [])
                    if v.get("type") == "release"
                ]
                return versions[:limit]
    except Exception:
        return list(POPULAR_VERSIONS)


# ── Mod type detection from JAR contents ─────────────────────────────

MOD_TYPE_FABRIC = "fabric"
MOD_TYPE_FORGE = "forge"
MOD_TYPE_BUKKIT = "bukkit"  # Spigot/Paper plugin
MOD_TYPE_BUNGEECORD = "bungeecord"
MOD_TYPE_UNKNOWN = "unknown"

# Server types that accept each mod type
COMPATIBLE_SERVER_TYPES = {
    MOD_TYPE_FABRIC: {"fabric"},
    MOD_TYPE_FORGE: {"forge"},
    MOD_TYPE_BUKKIT: {"paper", "spigot", "vanilla"},  # Paper/Spigot accept Bukkit plugins
    MOD_TYPE_BUNGEECORD: {"bungeecord"},
    MOD_TYPE_UNKNOWN: set(),  # no compatibility check
}


def detect_mod_type(file_data: bytes) -> tuple[str, dict[str, Any]]:
    """Inspect a JAR to determine mod/plugin type and extract metadata.

    Returns (mod_type, metadata_dict).
    """
    metadata: dict[str, Any] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(file_data)) as zf:
            names = zf.namelist()

            # Fabric mod
            if "fabric.mod.json" in names:
                try:
                    raw = zf.read("fabric.mod.json")
                    info = json.loads(raw)
                    metadata["name"] = info.get("name", "")
                    metadata["version"] = info.get("version", "")
                    metadata["mc_version"] = info.get("depends", {}).get("minecraft", "")
                except Exception:
                    pass
                return MOD_TYPE_FABRIC, metadata

            # Forge mod (1.13+)
            if "META-INF/mods.toml" in names:
                try:
                    raw = zf.read("META-INF/mods.toml").decode("utf-8", errors="replace")
                    # Simple parsing — look for modId and version
                    for line in raw.splitlines():
                        line = line.strip()
                        if line.startswith("modId") and "=" in line:
                            metadata["name"] = line.split("=", 1)[1].strip().strip('"')
                        if line.startswith("version") and "=" in line:
                            metadata["version"] = line.split("=", 1)[1].strip().strip('"')
                except Exception:
                    pass
                return MOD_TYPE_FORGE, metadata

            # Forge mod (legacy)
            if "mcmod.info" in names:
                try:
                    raw = zf.read("mcmod.info")
                    info_list = json.loads(raw)
                    if isinstance(info_list, list) and info_list:
                        info = info_list[0] if isinstance(info_list[0], dict) else {}
                        metadata["name"] = info.get("name", "")
                        metadata["version"] = info.get("version", "")
                        metadata["mc_version"] = info.get("mcversion", "")
                except Exception:
                    pass
                return MOD_TYPE_FORGE, metadata

            # Bukkit/Spigot/Paper plugin
            if "plugin.yml" in names:
                try:
                    raw = zf.read("plugin.yml").decode("utf-8", errors="replace")
                    for line in raw.splitlines():
                        if line.startswith("name:"):
                            metadata["name"] = line.split(":", 1)[1].strip()
                        if line.startswith("version:"):
                            metadata["version"] = line.split(":", 1)[1].strip().strip("'\"")
                        if line.startswith("api-version:"):
                            metadata["mc_version"] = line.split(":", 1)[1].strip()
                except Exception:
                    pass
                return MOD_TYPE_BUKKIT, metadata

            # BungeeCord plugin
            if "bungee.yml" in names or "plugin.yml" in names:
                return MOD_TYPE_BUNGEECORD, metadata

    except zipfile.BadZipFile:
        pass

    return MOD_TYPE_UNKNOWN, metadata


def check_compatibility(mod_type: str, server_type: str) -> tuple[bool, str]:
    """Check if a mod type is compatible with a server type.

    Returns (is_compatible, message).
    """
    if mod_type == MOD_TYPE_UNKNOWN:
        return True, "Could not determine mod type — no compatibility check performed."

    compatible_servers = COMPATIBLE_SERVER_TYPES.get(mod_type, set())
    server_lower = server_type.lower()

    if server_lower in compatible_servers:
        return True, f"✅ {mod_type.title()} mod is compatible with {server_type} servers."

    return False, (
        f"⚠️ **Incompatible:** This is a **{mod_type.title()}** mod/plugin, "
        f"but the server is running **{server_type.title()}**. "
        f"Expected server types: {', '.join(t.title() for t in compatible_servers)}."
    )


# ── Server type descriptions ─────────────────────────────────────────

SERVER_TYPE_INFO = {
    "vanilla": {
        "label": "Vanilla",
        "description": "Official Mojang server. No mod/plugin support.",
        "supports_mods": False,
        "supports_plugins": False,
    },
    "paper": {
        "label": "Paper",
        "description": "High-performance Bukkit/Spigot fork. Supports plugins.",
        "supports_mods": False,
        "supports_plugins": True,
    },
    "spigot": {
        "label": "Spigot",
        "description": "Modified Bukkit server. Supports plugins.",
        "supports_mods": False,
        "supports_plugins": True,
    },
    "forge": {
        "label": "Forge",
        "description": "Mod loader for Java Edition. Supports Forge mods.",
        "supports_mods": True,
        "supports_plugins": False,
    },
    "fabric": {
        "label": "Fabric",
        "description": "Lightweight mod loader. Supports Fabric mods.",
        "supports_mods": True,
        "supports_plugins": False,
    },
    "bungeecord": {
        "label": "BungeeCord",
        "description": "Proxy server for linking multiple MC servers.",
        "supports_mods": False,
        "supports_plugins": True,
    },
}
