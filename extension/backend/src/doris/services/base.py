"""Base HTTP client for BlueOS services."""

from typing import Any

import httpx


class BlueOSClient:
    """Base HTTP client for communicating with BlueOS services."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """Make a GET request."""
        response = await self.client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def post(
        self, path: str, data: dict | None = None, json: dict | None = None
    ) -> dict[str, Any]:
        """Make a POST request."""
        response = await self.client.post(path, data=data, json=json)
        response.raise_for_status()
        return response.json()

    async def put(self, path: str, json: dict | None = None) -> dict[str, Any]:
        """Make a PUT request."""
        response = await self.client.put(path, json=json)
        response.raise_for_status()
        return response.json()

    async def delete(self, path: str) -> dict[str, Any]:
        """Make a DELETE request."""
        response = await self.client.delete(path)
        response.raise_for_status()
        return response.json()

    async def health_check(self) -> bool:
        """Check if the service is healthy."""
        try:
            await self.client.get("/")
            return True
        except Exception:
            return False

