"""Reusable HTTPS clients for NCBI Entrez and optional Europe PMC access."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import socket
import ssl
import time
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpResponse:
    content: bytes
    status: int
    content_type: str
    url: str


class MetadataClient:
    def __init__(
        self,
        *,
        email: str | None = None,
        tool: str | None = None,
        api_key: str | None = None,
        timeout: float = 60,
        attempts: int = 4,
        transport: Callable[[Request, float], HttpResponse] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.email = email if email is not None else os.getenv("NCBI_EMAIL", "")
        self.tool = tool if tool is not None else os.getenv("NCBI_TOOL", "sra-bioproject")
        self.api_key = api_key if api_key is not None else os.getenv("NCBI_API_KEY", "")
        self.timeout = timeout
        self.attempts = attempts
        self.transport = transport or self._urlopen
        self.sleep = sleep
        self.monotonic = monotonic
        self._last_request = 0.0

    def _urlopen(self, request: Request, timeout: float) -> HttpResponse:
        with urlopen(request, timeout=timeout) as response:
            return HttpResponse(
                response.read(), response.status,
                response.headers.get_content_type(), response.geturl(),
            )

    def _throttle(self) -> None:
        interval = 0.1 if self.api_key else 0.34
        delay = interval - (self.monotonic() - self._last_request)
        if self._last_request and delay > 0:
            self.sleep(delay)

    def get(self, base_url: str, params: Mapping[str, object]) -> HttpResponse:
        query = {key: str(value) for key, value in params.items() if value not in (None, "")}
        if "eutils.ncbi.nlm.nih.gov" in base_url:
            query.update({"tool": self.tool})
            if self.email:
                query["email"] = self.email
            if self.api_key:
                query["api_key"] = self.api_key
        url = f"{base_url}?{urlencode(query)}"
        request = Request(url, headers={"User-Agent": f"{self.tool}/metadata"})
        for attempt in range(1, self.attempts + 1):
            self._throttle()
            try:
                response = self.transport(request, self.timeout)
                self._last_request = self.monotonic()
                return response
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == self.attempts:
                    raise
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(8, 2 ** (attempt - 1))
            except (URLError, TimeoutError, socket.timeout, ssl.SSLError):
                if attempt == self.attempts:
                    raise
                delay = min(8, 2 ** (attempt - 1))
            self.sleep(delay)
        raise RuntimeError("metadata request retry loop exhausted")

    def entrez(self, endpoint: str, **params: object) -> HttpResponse:
        return self.get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/{endpoint}", params)

    def europe_pmc(self, accession: str) -> HttpResponse:
        return self.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            {"query": f'"{accession}"', "format": "json", "pageSize": 1000},
        )

    @staticmethod
    def public_url(url: str) -> str:
        if "api_key=" not in url:
            return url
        prefix, remainder = url.split("api_key=", 1)
        return prefix + "api_key=REDACTED" + ("&" + remainder.split("&", 1)[1] if "&" in remainder else "")

    @staticmethod
    def json(response: HttpResponse) -> object:
        return json.loads(response.content.decode("utf-8"))