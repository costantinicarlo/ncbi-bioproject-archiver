import json
import xml.etree.ElementTree as ET

from sra_bioproject.metadata.client import HttpResponse
from sra_bioproject.metadata.entrez import LINK_DATABASES, retrieve


class FakeClient:
    def __init__(self) -> None:
        self.links = []

    def entrez(self, endpoint: str, **params: object) -> HttpResponse:
        if endpoint == "esearch.fcgi":
            content = json.dumps({"esearchresult": {"idlist": ["1"]}}).encode()
        elif endpoint == "elink.fcgi":
            self.links.append(str(params["linkname"]))
            content = b"<eLinkResult><LinkSet /></eLinkResult>"
        elif params.get("db") == "bioproject":
            content = b'<RecordSet><Project><ProjectID><ArchiveID accession="PRJNA1" /></ProjectID></Project></RecordSet>'
        else:
            content = b"<empty />"
        return HttpResponse(content, 200, "application/xml", "https://example.test")

    def europe_pmc(self, accession: str) -> HttpResponse:
        return HttpResponse(b'{}', 200, "application/json", "https://example.test")


def test_retrieval_requests_each_verified_link_explicitly() -> None:
    client = FakeClient()
    records, warnings = retrieve(client, "PRJNA1", require_sra=False)
    links = next(record for record in records if record.filename == "entrez_links.xml")
    assert set(client.links) == set(LINK_DATABASES)
    assert ET.fromstring(links.content).tag == "eLinkResult"
    assert warnings == []