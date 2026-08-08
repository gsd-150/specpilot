from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RfcRejectionCode(StrEnum):
    """Stable machine-readable reasons for rejecting an RFC XML document."""

    NOT_A_REGULAR_FILE = "not_a_regular_file"
    DOCUMENT_TOO_LARGE = "document_too_large"
    INVALID_ENCODING = "invalid_encoding"
    DOCTYPE = "doctype"
    ENTITY_DECLARATION = "entity_declaration"
    EXTERNAL_ENTITY = "external_entity"
    PROCESSING_INSTRUCTION = "processing_instruction"
    INVALID_XML = "invalid_xml"
    UNEXPECTED_ROOT = "unexpected_root"
    UNSUPPORTED_RFCXML_VERSION = "unsupported_rfcxml_version"


@dataclass(frozen=True, slots=True)
class RfcLimits:
    """Bounds applied before any parsing begins.

    ``max_bytes`` is generous because RFC XML is legitimately large — RFC 9110
    is over a megabyte — but it is still a bound, checked against the file on
    disk rather than against whatever a parser decides to allocate.
    """

    max_bytes: int = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RfcInspection:
    """What the boundary is willing to say about a document it accepted.

    Metadata only. No title, no section text, no reference target — a caller
    that wants content parses the tree itself, having been told the document is
    safe to parse.
    """

    document_sha256: str
    document_bytes: int
    root_tag: str


class UnsafeRfcError(Exception):
    """An RFC document rejected by the fail-closed inspection policy.

    Carries a code and nothing else. The offending construct is exactly the
    thing that must not be echoed into a log or an error message.
    """

    def __init__(self, code: RfcRejectionCode) -> None:
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"UnsafeRfcError({self.code.value})"
