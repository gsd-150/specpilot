"""The RFC XML verification boundary.

The corpus moved from OOXML to RFC, so the archive and part-graph risks are
gone. XML parsing brings its own, and this module refuses those constructs
outright rather than relying on a parser having been configured correctly.

The refusals below are checked twice on purpose: once against the raw prologue
and once against the parsed tree. A boundary whose safety depends entirely on
one library's default settings is one dependency upgrade away from being open.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from xml.etree.ElementTree import Element  # noqa: S405 - parsed via defusedxml

from defusedxml.common import (  # type: ignore[import-untyped]
    DTDForbidden,
    EntitiesForbidden,
    ExternalReferenceForbidden,
)
from defusedxml.ElementTree import (  # type: ignore[import-untyped]
    fromstring as defused_fromstring,
)

from specpilot.contracts.rfc import (
    RfcInspection,
    RfcLimits,
    RfcRejectionCode,
    UnsafeRfcError,
)

_EXPECTED_ROOT = "rfc"

# Matched against the prologue only — the region before the root element, where
# a DOCTYPE and its internal subset are the only things that can legally appear.
_DOCTYPE = re.compile(r"<!DOCTYPE", re.IGNORECASE)
_ENTITY_DECLARATION = re.compile(r"<!ENTITY", re.IGNORECASE)
_EXTERNAL_ID = re.compile(r"\b(?:SYSTEM|PUBLIC)\b")
_PROCESSING_INSTRUCTION = re.compile(r"<\?(?!xml\s)[^>]*\?>")


def _read_regular_file(path: Path, limits: RfcLimits) -> bytes:
    """Open without following a symlink and read within the byte cap."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise UnsafeRfcError(RfcRejectionCode.NOT_A_REGULAR_FILE) from error
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise UnsafeRfcError(RfcRejectionCode.NOT_A_REGULAR_FILE)
        if status.st_size > limits.max_bytes:
            raise UnsafeRfcError(RfcRejectionCode.DOCUMENT_TOO_LARGE)
        # Read one byte past the cap so a file that grew since fstat is caught
        # rather than silently truncated into an "accepted" document.
        data = os.read(descriptor, limits.max_bytes + 1)
        while len(data) < status.st_size:
            chunk = os.read(descriptor, limits.max_bytes + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > limits.max_bytes:
            raise UnsafeRfcError(RfcRejectionCode.DOCUMENT_TOO_LARGE)
        return data
    finally:
        os.close(descriptor)


def _refuse_hostile_prologue(text: str) -> None:
    """Reject dangerous constructs before a parser ever sees the document."""
    root_start = text.find(f"<{_EXPECTED_ROOT}")
    prologue = text if root_start < 0 else text[:root_start]

    if _ENTITY_DECLARATION.search(prologue):
        # An external identifier inside an entity declaration is the more
        # specific finding, so report that rather than the general one.
        if _EXTERNAL_ID.search(prologue):
            raise UnsafeRfcError(RfcRejectionCode.EXTERNAL_ENTITY)
        raise UnsafeRfcError(RfcRejectionCode.ENTITY_DECLARATION)
    if _DOCTYPE.search(prologue):
        raise UnsafeRfcError(RfcRejectionCode.DOCTYPE)
    if _PROCESSING_INSTRUCTION.search(prologue):
        raise UnsafeRfcError(RfcRejectionCode.PROCESSING_INSTRUCTION)


def inspect_rfc_xml(path: Path, limits: RfcLimits) -> RfcInspection:
    """Verify one already-fetched RFC XML document and report metadata only.

    Fetching is deliberately not part of this boundary. It verifies bytes that
    are already on disk, which keeps the network out of the code path that
    decides whether a document is safe.
    """
    data = _read_regular_file(path, limits)

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise UnsafeRfcError(RfcRejectionCode.INVALID_ENCODING) from error

    _refuse_hostile_prologue(text)

    try:
        # forbid_dtd is not defusedxml's default. Without it a bare DOCTYPE
        # carrying no entities parses happily, which would leave that one shape
        # resting on the prologue scan alone — and a boundary that claims two
        # layers has to have two everywhere, not almost everywhere.
        root: Element = defused_fromstring(
            text,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    # Classify on the parser's own exception types rather than on their names.
    # A renamed class should break the build, not silently fall through to the
    # generic branch and mislabel a refusal.
    except ExternalReferenceForbidden as error:
        raise UnsafeRfcError(RfcRejectionCode.EXTERNAL_ENTITY) from error
    except EntitiesForbidden as error:
        raise UnsafeRfcError(RfcRejectionCode.ENTITY_DECLARATION) from error
    except DTDForbidden as error:
        raise UnsafeRfcError(RfcRejectionCode.DOCTYPE) from error
    except Exception as error:
        raise UnsafeRfcError(RfcRejectionCode.INVALID_XML) from error

    if root.tag != _EXPECTED_ROOT:
        raise UnsafeRfcError(RfcRejectionCode.UNEXPECTED_ROOT)

    return RfcInspection(
        document_sha256=hashlib.sha256(data).hexdigest(),
        document_bytes=len(data),
        root_tag=root.tag,
    )
