"""Authoritative metadata and manifest schema versions."""

SNAPSHOT_SCHEMA_VERSION = "1.0"
PROJECT_SCHEMA_VERSION = "1.0"
TSV_SCHEMA_VERSION = "1.0"
MANIFEST_SCHEMA_VERSION = "1.0"

SAMPLE_COLUMNS = (
    "bioproject", "biosample", "sample_name", "title", "organism", "taxid",
    "package", "collection_date", "geo_loc_name", "isolation_source", "sex",
    "tissue", "isolate", "strain",
)
SAMPLE_ATTRIBUTE_COLUMNS = (
    "bioproject", "biosample", "attribute_name", "attribute_value",
    "attribute_harmonized_name",
)
RUN_COLUMNS = (
    "bioproject", "run_accession", "experiment_accession", "experiment_alias",
    "biosample", "sample_alias", "library_strategy", "library_source",
    "library_selection", "library_layout", "instrument_model", "total_bases",
    "total_spots", "sra_size_bytes", "md5", "url",
)
PUBLICATION_COLUMNS = (
    "bioproject", "pmid", "pmcid", "doi", "title", "journal",
    "publication_date", "authors", "association_source",
    "association_confidence", "evidence",
)
RELATIONSHIP_COLUMNS = (
    "source_accession", "relationship", "target_database", "target_accession",
    "target_uid", "evidence_source",
)
LINKED_RESOURCE_COLUMNS = (
    "bioproject", "target_database", "target_accession", "target_uid",
    "relationship", "evidence_source",
)