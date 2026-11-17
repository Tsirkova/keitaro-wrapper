import httpx

from app.config import settings


class KeitaroError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class KeitaroClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        url = (base_url or settings.keitaro_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.keitaro_api_key
        self.http = httpx.Client(
            base_url=f"{url}/admin_api/v1/",
            headers={"Api-Key": self.api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )

    def close(self):
        self.http.close()

    def request(self, method: str, path: str, body: dict | None = None):
        if not self.api_key:
            raise KeitaroError(503, "KEITARO_API_KEY is not configured")
        try:
            response = self.http.request(method, path.lstrip("/"), json=body)
        except httpx.RequestError as exc:
            raise KeitaroError(502, f"Keitaro connection failed: {exc.__class__.__name__}") from exc
        if response.is_error:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:300]
            raise KeitaroError(response.status_code, f"Keitaro API: {detail}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise KeitaroError(502, "Keitaro returned invalid JSON") from exc

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, body: dict):
        return self.request("POST", path, body)

    def put(self, path: str, body: dict):
        return self.request("PUT", path, body)

    def delete(self, path: str):
        return self.request("DELETE", path)


def get_keitaro():
    client = KeitaroClient()
    try:
        yield client
    finally:
        client.close()


def campaign_path(campaign_id: int) -> str:
    return f"campaigns/{campaign_id}"


def stream_path(stream_id: int) -> str:
    return f"streams/{stream_id}"


def safe_alias(name: str) -> str:
    import re
    from uuid import uuid4

    stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return f"{stem}-{uuid4().hex[:8]}"
