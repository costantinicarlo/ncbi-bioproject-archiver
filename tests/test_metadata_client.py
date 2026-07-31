from email.message import Message
from urllib.error import HTTPError

from sra_bioproject.metadata.client import HttpResponse, MetadataClient


def test_client_adds_identity_and_redacts_api_key() -> None:
    requests = []

    def transport(request, timeout):
        requests.append(request)
        return HttpResponse(b"{}", 200, "application/json", request.full_url)

    client = MetadataClient(
        email="researcher@example.org", tool="test-tool", api_key="secret",
        transport=transport, monotonic=lambda: 1.0,
    )
    response = client.entrez("esearch.fcgi", db="bioproject", term="PRJNA1")
    assert "email=researcher%40example.org" in response.url
    assert "tool=test-tool" in response.url
    assert "secret" not in client.public_url(response.url)


def test_client_retries_429_and_server_errors() -> None:
    attempts = []
    delays = []

    def transport(request, timeout):
        attempts.append(request)
        if len(attempts) < 3:
            headers = Message()
            return_code = 429 if len(attempts) == 1 else 503
            raise HTTPError(request.full_url, return_code, "retry", headers, None)
        return HttpResponse(b"ok", 200, "text/plain", request.full_url)

    client = MetadataClient(
        transport=transport, sleep=delays.append, monotonic=lambda: 1.0,
    )
    assert client.entrez("einfo.fcgi", db="bioproject").content == b"ok"
    assert len(attempts) == 3
    assert delays == [1, 2]