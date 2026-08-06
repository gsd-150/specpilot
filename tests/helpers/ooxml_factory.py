from __future__ import annotations

import zipfile
from html import escape
from pathlib import Path

CONTENT_TYPES = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    b'  <Default Extension="rels" ContentType="application/vnd.openxmlformats-'
    b'package.relationships+xml"/>\n'
    b'  <Default Extension="xml" ContentType="application/xml"/>\n'
    b'  <Override PartName="/word/document.xml" ContentType="application/vnd.'
    b'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
    b"</Types>\n"
)

DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>safe fixture</w:t></w:r></w:p></w:body>
</w:document>
"""


def build_relationship_docx(
    tmp_path: Path,
    *,
    target: str,
    target_mode: str | None = None,
    relationship_part: str = "word/_rels/document.xml.rels",
    relationship_id: str = "rId9",
    relationship_type: str = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    ),
    name: str = "source.docx",
) -> Path:
    mode_attribute = (
        ""
        if target_mode is None
        else f' TargetMode="{escape(target_mode, quote=True)}"'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships">\n'
        f'  <Relationship Id="{escape(relationship_id, quote=True)}" '
        f'Type="{escape(relationship_type, quote=True)}" '
        f'Target="{escape(target, quote=True)}"{mode_attribute}/>\n'
        "</Relationships>\n"
    ).encode()
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", DOCUMENT_XML)
        archive.writestr(relationship_part, relationships)
    return path


def append_windows_member(
    docx: Path,
    member_name: str,
    *,
    external_attr: int,
) -> None:
    member = zipfile.ZipInfo(member_name)
    member.create_system = 0
    member.external_attr = external_attr
    with zipfile.ZipFile(docx, "a") as archive:
        archive.writestr(member, b"")


def build_docx(
    tmp_path: Path,
    mutation: str = "safe",
    *,
    name: str = "source.docx",
) -> Path:
    content_types = CONTENT_TYPES
    members: list[tuple[str | zipfile.ZipInfo, bytes]] = [
        ("word/document.xml", DOCUMENT_XML),
    ]

    if mutation == "macro_content_type":
        content_types = content_types.replace(
            b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            b"application/vnd.ms-word.document.macroEnabled.main+xml",
        )
    elif mutation == "vba_project":
        members.append(("word/vbaProject.bin", b"synthetic-vba"))
    elif mutation == "embedded_executable":
        members.append(("word/embeddings/payload.exe", b"MZsynthetic"))
    elif mutation == "ole_object":
        members.append(("word/embeddings/oleObject1.bin", b"synthetic-ole"))
    elif mutation == "nested_package":
        members.append(("word/embeddings/nested.docx", b"PK\x03\x04synthetic"))
    elif mutation in {"external_relationship", "ole_relationship"}:
        if mutation == "external_relationship":
            relationship_type = (
                b"http://schemas.openxmlformats.org/officeDocument/2006/"
                b"relationships/hyperlink"
            )
            target = b"https://secret.example.invalid/private?q=token"
            target_mode = b' TargetMode="External"'
        else:
            relationship_type = (
                b"http://schemas.openxmlformats.org/officeDocument/2006/"
                b"relationships/oleObject"
            )
            target = b"../media/image.png"
            target_mode = b""
        members.append(
            (
                "word/_rels/document.xml.rels",
                (
                    b'<?xml version="1.0" encoding="UTF-8"?>\n'
                    b'<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                    b'2006/relationships">\n'
                    b'  <Relationship Id="rId9" Type="'
                    + relationship_type
                    + b'" Target="'
                    + target
                    + b'"'
                    + target_mode
                    + b"/>\n"
                    b"</Relationships>\n"
                ),
            )
        )
    elif mutation == "doctype":
        content_types = b"""<?xml version="1.0"?>
<!DOCTYPE Types [<!ENTITY secret "SHOULD-NOT-LEAK">]>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml" ContentType="&secret;"/>
</Types>
"""
    elif mutation == "traversal":
        members.append(("../escape.bin", b"unsafe"))
    elif mutation == "symlink":
        member = zipfile.ZipInfo("word/media/link.png")
        member.create_system = 3
        member.external_attr = 0o120777 << 16
        members.append((member, b"../../outside"))
    elif mutation != "safe":
        raise ValueError(f"unknown mutation: {mutation}")

    path = tmp_path / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        for member_name, payload in members:
            archive.writestr(member_name, payload)
    return path
