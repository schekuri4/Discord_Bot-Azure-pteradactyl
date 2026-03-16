"""Pterodactyl Panel API clients — Client API and Application (Admin) API."""

from __future__ import annotations

import io
from typing import Any

import aiohttp


class PterodactylApiError(Exception):
    pass


# ── Client API (ptlc_ key) ──────────────────────────────────────────


class PterodactylClient:
    """Wraps the Pterodactyl *Client* API (user-level)."""

    def __init__(self, panel_url: str, api_key: str) -> None:
        self.panel_url = panel_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "Application/vnd.pterodactyl.v1+json",
            "Content-Type": "application/json",
        }

    # ── servers ──────────────────────────────────────────────────────

    async def list_servers(self) -> list[dict[str, Any]]:
        url = f"{self.panel_url}/api/client?per_page=100"
        servers: list[dict[str, Any]] = []
        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        raise PterodactylApiError(f"List failed ({resp.status}): {body[:300]}")

                    payload = await resp.json()
                    for item in payload.get("data", []):
                        attrs: dict[str, Any] = item.get("attributes", {})
                        name = str(attrs.get("name", "unknown"))
                        identifier = str(attrs.get("identifier", ""))
                        uuid = str(attrs.get("uuid", ""))
                        description = str(attrs.get("description", "") or "")
                        node = str(attrs.get("node", ""))
                        is_suspended = bool(attrs.get("is_suspended", False))

                        relationships = attrs.get("relationships", {})
                        allocs = relationships.get("allocations", {}).get("data", [])
                        ip = ""
                        port = ""
                        if allocs:
                            alloc_attrs = allocs[0].get("attributes", {})
                            ip = str(alloc_attrs.get("ip_alias") or alloc_attrs.get("ip", ""))
                            port = str(alloc_attrs.get("port", ""))

                        if identifier:
                            servers.append({
                                "name": name,
                                "identifier": identifier,
                                "uuid": uuid,
                                "description": description,
                                "node": node,
                                "ip": ip,
                                "port": port,
                                "suspended": is_suspended,
                            })

                    next_url = (
                        payload.get("meta", {})
                        .get("pagination", {})
                        .get("links", {})
                        .get("next")
                    )
                    url = next_url

        return servers

    async def get_server_resources(self, identifier: str) -> dict[str, Any]:
        url = f"{self.panel_url}/api/client/servers/{identifier}/resources"
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status >= 400:
                    return {"current_state": "unknown"}
                payload = await resp.json()
                return payload.get("attributes", {})

    async def send_power_signal(self, identifier: str, signal: str) -> None:
        url = f"{self.panel_url}/api/client/servers/{identifier}/power"
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json={"signal": signal}, timeout=timeout) as resp:
                if resp.status in (200, 202, 204):
                    return
                body = await resp.text()
                if resp.status in (401, 403):
                    raise PterodactylApiError("Pterodactyl rejected power control. Check API key permissions.")
                raise PterodactylApiError(f"Power signal failed ({resp.status}): {body[:300]}")

    async def send_command(self, identifier: str, command: str) -> None:
        url = f"{self.panel_url}/api/client/servers/{identifier}/command"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json={"command": command}, timeout=timeout) as resp:
                if resp.status in (200, 202, 204):
                    return
                body = await resp.text()
                raise PterodactylApiError(f"Console command failed ({resp.status}): {body[:300]}")

    # ── file management ──────────────────────────────────────────────

    async def list_files(self, identifier: str, directory: str = "/") -> list[dict[str, Any]]:
        url = f"{self.panel_url}/api/client/servers/{identifier}/files/list"
        params = {"directory": directory}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), params=params, timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"List files failed ({resp.status}): {body[:300]}")
                payload = await resp.json()
                return [item.get("attributes", {}) for item in payload.get("data", [])]

    async def get_upload_url(self, identifier: str) -> str:
        url = f"{self.panel_url}/api/client/servers/{identifier}/files/upload"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"Upload URL failed ({resp.status}): {body[:300]}")
                payload = await resp.json()
                return payload["attributes"]["url"]

    async def upload_file(self, identifier: str, directory: str, filename: str, data: bytes) -> None:
        upload_url = await self.get_upload_url(identifier)
        upload_url += f"&directory={directory}"
        timeout = aiohttp.ClientTimeout(total=120)
        form = aiohttp.FormData()
        form.add_field("files", io.BytesIO(data), filename=filename, content_type="application/octet-stream")
        async with aiohttp.ClientSession() as session:
            async with session.post(upload_url, data=form, timeout=timeout) as resp:
                if resp.status not in (200, 204):
                    body = await resp.text()
                    raise PterodactylApiError(f"Upload failed ({resp.status}): {body[:300]}")

    async def delete_files(self, identifier: str, directory: str, files: list[str]) -> None:
        url = f"{self.panel_url}/api/client/servers/{identifier}/files/delete"
        payload = {"root": directory, "files": files}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json=payload, timeout=timeout) as resp:
                if resp.status not in (200, 204):
                    body = await resp.text()
                    raise PterodactylApiError(f"Delete files failed ({resp.status}): {body[:300]}")

    async def rename_file(self, identifier: str, directory: str, old_name: str, new_name: str) -> None:
        url = f"{self.panel_url}/api/client/servers/{identifier}/files/rename"
        payload = {"root": directory, "files": [{"from": old_name, "to": new_name}]}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=self._headers(), json=payload, timeout=timeout) as resp:
                if resp.status not in (200, 204):
                    body = await resp.text()
                    raise PterodactylApiError(f"Rename failed ({resp.status}): {body[:300]}")

    async def get_file_contents(self, identifier: str, filepath: str) -> str:
        url = f"{self.panel_url}/api/client/servers/{identifier}/files/contents"
        params = {"file": filepath}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), params=params, timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"Read file failed ({resp.status}): {body[:300]}")
                return await resp.text()

    # ── backups ──────────────────────────────────────────────────────

    async def list_backups(self, identifier: str) -> list[dict[str, Any]]:
        url = f"{self.panel_url}/api/client/servers/{identifier}/backups"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"List backups failed ({resp.status}): {body[:300]}")
                payload = await resp.json()
                return [item.get("attributes", {}) for item in payload.get("data", [])]

    async def create_backup(self, identifier: str, name: str | None = None) -> dict[str, Any]:
        url = f"{self.panel_url}/api/client/servers/{identifier}/backups"
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json=body, timeout=timeout) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise PterodactylApiError(f"Create backup failed ({resp.status}): {text[:300]}")
                payload = await resp.json()
                return payload.get("attributes", {})

    async def delete_backup(self, identifier: str, backup_uuid: str) -> None:
        url = f"{self.panel_url}/api/client/servers/{identifier}/backups/{backup_uuid}"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status not in (200, 204):
                    body = await resp.text()
                    raise PterodactylApiError(f"Delete backup failed ({resp.status}): {body[:300]}")

    async def restore_backup(self, identifier: str, backup_uuid: str) -> None:
        url = f"{self.panel_url}/api/client/servers/{identifier}/backups/{backup_uuid}/restore"
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json={"truncate": False}, timeout=timeout) as resp:
                if resp.status not in (200, 204):
                    body = await resp.text()
                    raise PterodactylApiError(f"Restore backup failed ({resp.status}): {body[:300]}")

    async def get_backup_download_url(self, identifier: str, backup_uuid: str) -> str:
        url = f"{self.panel_url}/api/client/servers/{identifier}/backups/{backup_uuid}/download"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"Backup download URL failed ({resp.status}): {body[:300]}")
                payload = await resp.json()
                return payload["attributes"]["url"]


# ── Application / Admin API (ptla_ key) ─────────────────────────────


class PterodactylAdmin:
    """Wraps the Pterodactyl *Application* API (admin-level)."""

    def __init__(self, panel_url: str, admin_key: str) -> None:
        self.panel_url = panel_url.rstrip("/")
        self.admin_key = admin_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.admin_key}",
            "Accept": "Application/vnd.pterodactyl.v1+json",
            "Content-Type": "application/json",
        }

    # ── nests & eggs ─────────────────────────────────────────────────

    async def list_nests(self) -> list[dict[str, Any]]:
        url = f"{self.panel_url}/api/application/nests?include=eggs"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"List nests failed ({resp.status}): {body[:300]}")
                payload = await resp.json()
                return [item.get("attributes", {}) for item in payload.get("data", [])]

    async def get_egg(self, nest_id: int, egg_id: int) -> dict[str, Any]:
        url = f"{self.panel_url}/api/application/nests/{nest_id}/eggs/{egg_id}?include=variables"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"Get egg failed ({resp.status}): {body[:300]}")
                payload = await resp.json()
                return payload.get("attributes", {})

    # ── nodes & allocations ──────────────────────────────────────────

    async def list_nodes(self) -> list[dict[str, Any]]:
        url = f"{self.panel_url}/api/application/nodes?include=allocations"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"List nodes failed ({resp.status}): {body[:300]}")
                payload = await resp.json()
                return [item.get("attributes", {}) for item in payload.get("data", [])]

    async def find_free_allocation(self, node_id: int | None = None) -> tuple[int, int]:
        """Return (node_id, allocation_id) for the first unassigned allocation."""
        nodes = await self.list_nodes()
        for node in nodes:
            if node_id and node.get("id") != node_id:
                continue
            allocs = (
                node.get("relationships", {})
                .get("allocations", {})
                .get("data", [])
            )
            for alloc in allocs:
                attrs = alloc.get("attributes", {})
                if not attrs.get("assigned"):
                    return node["id"], attrs["id"]
        raise PterodactylApiError("No free allocations available on any node.")

    # ── users ────────────────────────────────────────────────────────

    async def list_users(self) -> list[dict[str, Any]]:
        url = f"{self.panel_url}/api/application/users"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"List users failed ({resp.status}): {body[:300]}")
                payload = await resp.json()
                return [item.get("attributes", {}) for item in payload.get("data", [])]

    async def get_first_admin_user_id(self) -> int:
        users = await self.list_users()
        for user in users:
            if user.get("root_admin"):
                return user["id"]
        if users:
            return users[0]["id"]
        raise PterodactylApiError("No users found on the panel.")

    # ── server management ────────────────────────────────────────────

    async def create_server(
        self,
        name: str,
        user_id: int,
        egg_id: int,
        docker_image: str,
        startup: str,
        environment: dict[str, str],
        allocation_id: int,
        memory: int = 2048,
        disk: int = 10240,
        cpu: int = 100,
        databases: int = 1,
        backups: int = 3,
        description: str = "",
    ) -> dict[str, Any]:
        url = f"{self.panel_url}/api/application/servers"
        payload = {
            "name": name,
            "description": description,
            "user": user_id,
            "egg": egg_id,
            "docker_image": docker_image,
            "startup": startup,
            "environment": environment,
            "limits": {
                "memory": memory,
                "swap": 0,
                "disk": disk,
                "io": 500,
                "cpu": cpu,
            },
            "feature_limits": {
                "databases": databases,
                "allocations": 1,
                "backups": backups,
            },
            "allocation": {
                "default": allocation_id,
            },
        }
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json=payload, timeout=timeout) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise PterodactylApiError(f"Create server failed ({resp.status}): {body[:500]}")
                result = await resp.json()
                return result.get("attributes", {})

    async def delete_server(self, server_id: int, force: bool = False) -> None:
        url = f"{self.panel_url}/api/application/servers/{server_id}"
        if force:
            url += "/force"
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self._headers(), timeout=timeout) as resp:
                if resp.status not in (200, 204):
                    body = await resp.text()
                    raise PterodactylApiError(f"Delete server failed ({resp.status}): {body[:300]}")

    async def list_servers(self) -> list[dict[str, Any]]:
        url = f"{self.panel_url}/api/application/servers?per_page=100"
        servers: list[dict[str, Any]] = []
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        raise PterodactylApiError(f"Admin list failed ({resp.status}): {body[:300]}")
                    payload = await resp.json()
                    for item in payload.get("data", []):
                        servers.append(item.get("attributes", {}))
                    url = (
                        payload.get("meta", {})
                        .get("pagination", {})
                        .get("links", {})
                        .get("next")
                    )
        return servers
