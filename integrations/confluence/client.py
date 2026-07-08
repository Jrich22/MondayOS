"""
Confluence REST clients.

Two implementations satisfy the same tiny interface:

* :class:`HttpConfluenceClient` — talks to Confluence Cloud over the v1 REST
  API using only the standard library (no third-party HTTP dependency).
* :class:`FakeConfluenceClient` — an in-memory stand-in used by tests and by
  local demos (``MONDAYOS_CONFLUENCE_FAKE=1``). It never touches the network
  and can optionally persist its state to disk so it survives across CLI
  invocations.

Only page create / update / fetch are implemented — the operations the
document publisher needs. Nothing here reads MondayOS state.
"""
from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from integrations.confluence.config import ConfluenceConfig


class ConfluenceError(Exception):
    """Raised when a Confluence API call fails or is misconfigured."""


def normalize_site(base_url: str) -> str:
    """Return the Atlassian site root, without a trailing ``/wiki`` or slash.

    On Confluence Cloud both the REST API and the web UI live under
    ``<site>/wiki``. Users may set ``CONFLUENCE_BASE_URL`` as either the bare
    site (``https://x.atlassian.net``) or with the wiki suffix already attached
    (``https://x.atlassian.net/wiki``). We normalise to the bare site so the
    ``/wiki`` segment is added exactly once when building request and page
    URLs — otherwise a base URL that includes ``/wiki`` produces a doubled
    ``/wiki/wiki`` path, which returns no page data (blank id, bogus URL).
    """
    site = base_url.strip().rstrip("/")
    while site.lower().endswith("/wiki"):
        site = site[: -len("/wiki")].rstrip("/")
    return site


@dataclass
class PageResult:
    """The outcome of a create/update/fetch, normalised across clients."""

    id: str
    title: str
    url: str
    version: int


class ConfluenceClient(ABC):
    """Minimal interface the publisher depends on."""

    @abstractmethod
    def create_page(
        self, space_key: str, title: str, body_storage: str, parent_id: str = ""
    ) -> PageResult:
        """Create a new page and return its identifiers."""

    @abstractmethod
    def update_page(
        self, page_id: str, title: str, body_storage: str, parent_id: str = ""
    ) -> PageResult:
        """Replace the body/title of an existing page."""

    @abstractmethod
    def get_page(self, page_id: str) -> PageResult | None:
        """Fetch a page, or None if it does not exist."""


# ---------------------------------------------------------------------------
# Real client (Confluence Cloud REST v1, stdlib only)
# ---------------------------------------------------------------------------

class HttpConfluenceClient(ConfluenceClient):
    """Confluence Cloud client backed by ``urllib`` and HTTP Basic auth."""

    def __init__(self, base_url: str, email: str, api_token: str, timeout: float = 30.0) -> None:
        # Site root without '/wiki'; the '/wiki' prefix is added exactly once
        # when building request paths and page URLs (see normalize_site).
        self._site = normalize_site(base_url)
        self._email = email
        self._token = api_token
        self._timeout = timeout

    @classmethod
    def from_config(cls, config: "ConfluenceConfig") -> "HttpConfluenceClient":
        return cls(config.base_url, config.email, config.api_token)

    def create_page(
        self, space_key: str, title: str, body_storage: str, parent_id: str = ""
    ) -> PageResult:
        payload: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": body_storage, "representation": "storage"}},
        }
        if parent_id:
            payload["ancestors"] = [{"id": str(parent_id)}]
        data = self._request("POST", "/wiki/rest/api/content", payload)
        return self._to_result(data)

    def update_page(
        self, page_id: str, title: str, body_storage: str, parent_id: str = ""
    ) -> PageResult:
        current = self.get_page(page_id)
        if current is None:
            raise ConfluenceError(f"Page {page_id!r} not found; cannot update.")
        next_version = current.version + 1
        payload: dict[str, Any] = {
            "id": str(page_id),
            "type": "page",
            "title": title or current.title,
            "version": {"number": next_version},
            "body": {"storage": {"value": body_storage, "representation": "storage"}},
        }
        if parent_id:
            payload["ancestors"] = [{"id": str(parent_id)}]
        data = self._request("PUT", f"/wiki/rest/api/content/{page_id}", payload)
        return self._to_result(data)

    def get_page(self, page_id: str) -> PageResult | None:
        try:
            data = self._request(
                "GET", f"/wiki/rest/api/content/{page_id}?expand=version", None
            )
        except ConfluenceError as exc:
            if "404" in str(exc):
                return None
            raise
        return self._to_result(data)

    # -- internals ------------------------------------------------------

    def _to_result(self, data: dict[str, Any]) -> PageResult:
        links = data.get("_links", {}) or {}
        page_id = str(data.get("id") or "")
        url = self._page_url(links.get("webui", ""), links.get("base", ""), page_id)
        version = int((data.get("version") or {}).get("number", 1))
        return PageResult(
            id=page_id,
            title=data.get("title", ""),
            url=url,
            version=version,
        )

    def _page_url(self, webui: str, link_base: str, page_id: str) -> str:
        """Build a web UI URL with exactly one ``/wiki`` segment.

        ``webui`` from the API is a site-relative path like
        ``/spaces/ENG/pages/123/Title`` (no ``/wiki`` prefix); ``link_base``,
        when present, is the ``<site>/wiki`` root. We rebuild from the
        normalised site so neither a ``/wiki``-suffixed base URL nor a
        ``/wiki``-prefixed ``webui`` can produce a doubled ``/wiki/wiki``.
        """
        base = normalize_site(link_base) if link_base else self._site
        if webui:
            suffix = webui if webui.startswith("/") else f"/{webui}"
            if suffix.lower().startswith("/wiki/") or suffix.lower() == "/wiki":
                suffix = suffix[len("/wiki"):] or "/"
            return f"{base}/wiki{suffix}"
        if page_id:
            return f"{base}/wiki/pages/viewpage.action?pageId={page_id}"
        return f"{base}/wiki/spaces"

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        url = f"{self._site}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        token = base64.b64encode(f"{self._email}:{self._token}".encode()).decode("ascii")
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Basic {token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = _read_error(exc)
            if exc.code == 401:
                raise ConfluenceError(
                    "Confluence auth failed (401) — check CONFLUENCE_EMAIL / "
                    "CONFLUENCE_API_TOKEN."
                ) from exc
            raise ConfluenceError(f"Confluence API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ConfluenceError(f"Confluence unreachable: {exc.reason}") from exc


def _read_error(exc: Any) -> str:
    try:
        return exc.read().decode("utf-8")[:300]
    except Exception:
        return str(exc)


# ---------------------------------------------------------------------------
# Fake client (offline; used by tests and demos)
# ---------------------------------------------------------------------------

class FakeConfluenceClient(ConfluenceClient):
    """
    In-memory Confluence stand-in. No network, fully deterministic.

    Pass ``store_path`` to persist pages to a JSON file so state survives
    across separate process invocations (e.g. two CLI calls in a demo);
    without it the client is purely in-memory.
    """

    def __init__(
        self,
        base_url: str = "https://example.atlassian.net",
        store_path: Path | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._store_path = Path(store_path) if store_path else None
        self._pages: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._load()

    def create_page(
        self, space_key: str, title: str, body_storage: str, parent_id: str = ""
    ) -> PageResult:
        self._counter += 1
        page_id = f"PAGE-{self._counter}"
        self._pages[page_id] = {
            "id": page_id, "title": title, "body": body_storage,
            "space": space_key, "parent": parent_id, "version": 1,
        }
        self._save()
        return self._result(page_id)

    def update_page(
        self, page_id: str, title: str, body_storage: str, parent_id: str = ""
    ) -> PageResult:
        existing = self._pages.get(page_id)
        version = (existing["version"] + 1) if existing else 1
        self._pages[page_id] = {
            "id": page_id,
            "title": title or (existing or {}).get("title", ""),
            "body": body_storage,
            "space": (existing or {}).get("space", ""),
            "parent": parent_id or (existing or {}).get("parent", ""),
            "version": version,
        }
        self._save()
        return self._result(page_id)

    def get_page(self, page_id: str) -> PageResult | None:
        if page_id not in self._pages:
            return None
        return self._result(page_id)

    # -- helpers --------------------------------------------------------

    def _result(self, page_id: str) -> PageResult:
        p = self._pages[page_id]
        return PageResult(
            id=page_id, title=p["title"],
            url=f"{self._base}/wiki/spaces/{p.get('space', 'DEMO')}/pages/{page_id}",
            version=p["version"],
        )

    def _load(self) -> None:
        if self._store_path and self._store_path.exists():
            try:
                data = json.loads(self._store_path.read_text(encoding="utf-8"))
                self._pages = data.get("pages", {})
                self._counter = data.get("counter", len(self._pages))
            except Exception:
                self._pages, self._counter = {}, 0

    def _save(self) -> None:
        if not self._store_path:
            return
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._store_path.write_text(
                json.dumps({"pages": self._pages, "counter": self._counter}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass  # a demo persistence failure must not break publishing


def build_client(config: "ConfluenceConfig", project_root: Path) -> ConfluenceClient:
    """
    Construct the appropriate client for the given configuration.

    Routes to the offline fake when ``MONDAYOS_CONFLUENCE_FAKE`` is set;
    otherwise builds the real HTTP client. Callers should verify
    ``config.credential_check()`` before invoking this for a real publish.
    """
    if config.use_fake:
        store = Path(project_root) / "logs" / "publish" / "fake-confluence.json"
        return FakeConfluenceClient(store_path=store)
    return HttpConfluenceClient.from_config(config)
