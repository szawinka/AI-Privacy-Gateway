# Károly Tamás szakdolgozati prototípusa (2026).
"""Bounded, local TXT and simple DOCX extraction."""

from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import PurePosixPath
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED, ZIP_STORED
import zlib

from gateway.text_processing import InvalidSelection


MAX_UPLOAD = 1024 * 1024
MAX_EXPANDED = 8 * 1024 * 1024
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
BLOCKED = {
    "vanish", "webHidden", "ins", "del", "moveFrom", "moveTo", "sdt", "customXml",
    "drawing", "pict", "object", "fldSimple", "fldChar", "instrText", "txbxContent",
    "altChunk", "footnoteReference", "endnoteReference", "commentReference",
    "headerReference", "footerReference", "sym",
}


class DocumentRejected(InvalidSelection):
    pass


def validate_text(text: str) -> str:
    if any(
        unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\r\t" and not (i == 0 and c == "\ufeff")
        for i, c in enumerate(text)
    ):
        raise DocumentRejected("A szöveg rejtett vezérlőjelet tartalmaz. Egyszerűsítsd a bemenetet.")
    return text


def _xml(data):
    if len(data) > MAX_UPLOAD:
        raise DocumentRejected("A dokumentum XML-része túl nagy.")
    text = data.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise DocumentRejected("A dokumentum nem támogatott XML-szerkezetet tartalmaz.")
    root = ET.fromstring(text)
    if sum(1 for _ in root.iter()) > 50_000:
        raise DocumentRejected("A dokumentum szerkezete túl összetett.")
    return root


def _paragraph(element):
    parts = []
    for child in element:
        if child.tag == W + "pPr":
            continue
        if child.tag != W + "r":
            raise DocumentRejected("A DOCX összetett tartalmát egyszerűsíteni kell.")
        for run in child:
            if run.tag == W + "t":
                if len(run):
                    raise DocumentRejected("A DOCX összetett szövegelemet tartalmaz.")
                parts.append(run.text or "")
            elif run.tag == W + "tab":
                parts.append("\t")
            elif run.tag in (W + "br", W + "cr"):
                parts.append("\n")
            elif run.tag != W + "rPr":
                raise DocumentRejected("A DOCX nem támogatott szövegelemet tartalmaz.")
    return "".join(parts)


def _table(element):
    rows = []
    for row in element:
        if row.tag in (W + "tblPr", W + "tblGrid"):
            continue
        if row.tag != W + "tr":
            raise DocumentRejected("A táblázat szerkezete nem támogatott.")
        cells = []
        for cell in row:
            if cell.tag == W + "trPr":
                continue
            if cell.tag != W + "tc":
                raise DocumentRejected("A táblázat szerkezete nem támogatott.")
            paragraphs = []
            for item in cell:
                if item.tag == W + "p":
                    paragraphs.append(_paragraph(item))
                elif item.tag != W + "tcPr":
                    raise DocumentRejected("Beágyazott táblázat vagy összetett cella nem támogatott.")
            cells.append("\n".join(paragraphs))
        rows.append("\t".join(cells))
    return "\n".join(rows)


def extract_document(filename: str, data: bytes) -> str:
    if not data or len(data) > MAX_UPLOAD:
        raise DocumentRejected("A fájl üres vagy nagyobb 1 MiB-nál.")
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    try:
        if suffix == ".txt":
            return validate_text(data.decode("utf-8-sig"))
        if suffix != ".docx":
            raise DocumentRejected("TXT vagy egyszerű DOCX fájlt válassz.")
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if len(entries) > 256 or len(names) != len(set(names)) or sum(i.file_size for i in entries) > MAX_EXPANDED:
                raise DocumentRejected("A DOCX csomag mérete vagy szerkezete nem támogatott.")
            for item in entries:
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in item.filename or item.flag_bits & 1:
                    raise DocumentRejected("A DOCX csomag szerkezete nem támogatott.")
                if item.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
                    raise DocumentRejected("A DOCX tömörítési módja nem támogatott.")
                name = item.filename.lower()
                if any(part in name for part in (
                    "header", "footer", "footnotes", "endnotes", "comments", "vbaproject",
                    "embeddings/", "media/", "activex/",
                )):
                    raise DocumentRejected("A DOCX kiegészítő tartalmat tartalmaz. Használj egyszerű szöveges másolatot.")
                if not item.is_dir() and not name.endswith((".xml", ".rels")) and name != "docprops/thumbnail.jpeg":
                    raise DocumentRejected("A DOCX nem támogatott csomagrészt tartalmaz.")
                if name.endswith((".xml", ".rels")):
                    root = _xml(archive.read(item))
                    if any(e.tag.startswith(W) and e.tag[len(W):] in BLOCKED for e in root.iter()):
                        raise DocumentRejected("A DOCX rejtett, követett vagy összetett tartalmat tartalmaz.")
                    if name.endswith(".rels") and any(
                        e.get("TargetMode", "").lower() == "external" for e in root.iter(REL + "Relationship")
                    ):
                        raise DocumentRejected("A DOCX külső kapcsolatot tartalmaz.")
            types = _xml(archive.read("[Content_Types].xml"))
            main_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            if not any(e.get("PartName") == "/word/document.xml" and e.get("ContentType") == main_type for e in types):
                raise DocumentRejected("A fájl nem támogatott DOCX-dokumentum.")
            root = _xml(archive.read("word/document.xml"))
            if root.tag != W + "document":
                raise DocumentRejected("A DOCX törzsszerkezete nem támogatott.")
            for parent in root.iter():
                if parent.tag != W + "t" and parent.text and parent.text.strip():
                    raise DocumentRejected("A DOCX ismeretlen szövegelemet tartalmaz.")
                if any(child.tag == W + "t" and parent.tag != W + "r" for child in parent):
                    raise DocumentRejected("A DOCX összetett szövegelemet tartalmaz.")
            body = root.find(W + "body")
            if body is None:
                raise DocumentRejected("A DOCX törzsszövege hiányzik.")
            paragraphs = []
            for element in body:
                if element.tag == W + "p":
                    paragraphs.append(_paragraph(element))
                elif element.tag == W + "tbl":
                    paragraphs.append(_table(element))
                elif element.tag != W + "sectPr":
                    raise DocumentRejected("A DOCX összetett tartalmát egyszerűsíteni kell.")
            return validate_text("\n".join(paragraphs))
    except DocumentRejected:
        raise
    except (UnicodeError, ET.ParseError, BadZipFile, KeyError, RuntimeError, ValueError, NotImplementedError, zlib.error):
        raise DocumentRejected("A fájl nem olvasható támogatott UTF-8 TXT vagy egyszerű DOCX formában.") from None


async def read_upload(request):
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data") or "\r" in content_type or "\n" in content_type:
        raise DocumentRejected("Érvénytelen feltöltés.")
    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > MAX_UPLOAD + 16_384:
            raise DocumentRejected("A feltöltés nagyobb a megengedett méretnél.")
        body.extend(part)
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    parts = list(message.iter_parts())
    if message.defects or not message.is_multipart() or not 1 <= len(parts) <= 4:
        raise DocumentRejected("Érvénytelen feltöltés.")
    fields, filename, data = {}, "", None
    for part in parts:
        name = part.get_param("name", header="content-disposition")
        if part.defects or part.is_multipart() or name in fields or part.get_content_disposition() != "form-data":
            raise DocumentRejected("Érvénytelen feltöltés.")
        payload = part.get_payload(decode=True)
        if name == "document" and data is None:
            filename, data = part.get_filename() or "", payload
        elif name in ("request", "task"):
            fields[name] = payload.decode("utf-8")
        else:
            raise DocumentRejected("Érvénytelen feltöltés.")
    if data is None:
        raise DocumentRejected("Válassz feltöltendő fájlt.")
    return fields, filename, data
