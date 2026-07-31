"""Retrieve BioProject records and explicit Entrez relationships."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import xml.etree.ElementTree as ET

from .client import MetadataClient
from .models import RawResponseRecord

LINKS = {
    "biosample": "bioproject_biosample_all",
    "sra": "bioproject_sra_all",
    "pubmed": "bioproject_pubmed",
    "pmc": "bioproject_pmc",
    "assembly": "bioproject_assembly_all",
    "parent": "bioproject_bioproject_d2u",
    "child": "bioproject_bioproject_u2d",
}

LINK_DATABASES = {
    LINKS["biosample"]: "biosample", LINKS["sra"]: "sra",
    LINKS["pubmed"]: "pubmed", LINKS["pmc"]: "pmc",
    LINKS["assembly"]: "assembly", LINKS["parent"]: "bioproject",
    LINKS["child"]: "bioproject", "bioproject_dbvar": "dbvar",
    "bioproject_gds": "gds", "bioproject_genome": "genome",
    "bioproject_nuccore": "nuccore", "bioproject_protein": "protein",
    "bioproject_taxonomy": "taxonomy",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _raw(filename: str, response, database: str, operation: str, *, query: str = "", linkname: str = "") -> RawResponseRecord:
    return RawResponseRecord(filename, response.content, database, operation, response.content_type, query, linkname, response.status, _now())


def _ids(content: bytes, linkname: str) -> list[str]:
    root = ET.fromstring(content)
    return sorted({(element.text or "").strip() for group in root.findall(".//LinkSetDb") if group.findtext("./LinkName") == linkname for element in group.findall("./Link/Id") if (element.text or "").strip()}, key=int)


def _empty_xml(root: str) -> bytes:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<{root} />\n'.encode()


def _discover_links(client: MetadataClient, uid: str) -> bytes:
    combined = ET.Element("eLinkResult")
    link_set = ET.SubElement(combined, "LinkSet")
    ET.SubElement(link_set, "DbFrom").text = "bioproject"
    id_list = ET.SubElement(link_set, "IdList")
    ET.SubElement(id_list, "Id").text = uid
    for linkname, database in LINK_DATABASES.items():
        response = client.entrez(
            "elink.fcgi", dbfrom="bioproject", db=database, id=uid,
            linkname=linkname, cmd="neighbor",
        )
        root = ET.fromstring(response.content)
        for group in root.findall(".//LinkSetDb"):
            link_set.append(group)
    return ET.tostring(combined, encoding="utf-8", xml_declaration=True)


def retrieve(client: MetadataClient, accession: str, include_literature_search: bool = False, require_sra: bool = True) -> tuple[list[RawResponseRecord], list[str]]:
    accession = accession.strip().upper()
    search = client.entrez("esearch.fcgi", db="bioproject", term=f"{accession}[Project Accession]", retmode="json")
    payload = json.loads(search.content.decode("utf-8"))
    uids = payload.get("esearchresult", {}).get("idlist", [])
    if len(uids) != 1:
        raise RuntimeError(f"BioProject accession {accession} resolved to {len(uids)} records")
    uid = uids[0]
    project = client.entrez("efetch.fcgi", db="bioproject", id=uid, retmode="xml")
    links_content = _discover_links(client, uid)
    links = type(project)(links_content, 200, "application/xml", "entrez:elink")
    records = [
        _raw("bioproject.xml", project, "bioproject", "efetch", query=accession),
        _raw("entrez_links.xml", links, "bioproject", "elink", query=uid, linkname="explicit verified links"),
    ]
    warnings = []
    targets = (
        ("biosamples.xml", "biosample", LINKS["biosample"], "BioSampleSet", False),
        ("sra_experiments.xml", "sra", LINKS["sra"], "EXPERIMENT_PACKAGE_SET", require_sra),
        ("pubmed.xml", "pubmed", LINKS["pubmed"], "PubmedArticleSet", False),
        ("pmc.xml", "pmc", LINKS["pmc"], "pmc-articleset", False),
        ("assemblies.xml", "assembly", LINKS["assembly"], "DocumentSummarySet", False),
    )
    for filename, database, linkname, empty_root, required in targets:
        ids = _ids(links.content, linkname)
        if not ids:
            if required:
                raise RuntimeError(f"Required {database} links are missing for {accession}")
            records.append(RawResponseRecord(filename, _empty_xml(empty_root), database, "efetch", "application/xml", linkname=linkname, retrieved_at=_now()))
            continue
        try:
            response = client.entrez("efetch.fcgi", db=database, id=",".join(ids), retmode="xml")
            records.append(_raw(filename, response, database, "efetch", query=",".join(ids), linkname=linkname))
        except Exception as exc:
            if required:
                raise RuntimeError(f"Required {database} retrieval failed") from exc
            warnings.append(f"Optional {database} retrieval failed: {exc}")
            records.append(RawResponseRecord(filename, _empty_xml(empty_root), database, "efetch", "application/xml", linkname=linkname, retrieved_at=_now()))
    sra_ids = _ids(links.content, LINKS["sra"])
    if sra_ids:
        runinfo = client.entrez("efetch.fcgi", db="sra", id=",".join(sra_ids), rettype="runinfo", retmode="text")
        records.append(_raw("sra_runinfo.csv", runinfo, "sra", "efetch", query=",".join(sra_ids)))
    else:
        records.append(RawResponseRecord("sra_runinfo.csv", b"\n", "sra", "efetch", "text/csv", linkname=LINKS["sra"], retrieved_at=_now()))
    if include_literature_search:
        try:
            response = client.europe_pmc(accession)
            records.append(_raw("europe_pmc.json", response, "europe_pmc", "search", query=accession))
        except Exception as exc:
            warnings.append(f"Optional Europe PMC search failed: {exc}")
    return records, warnings