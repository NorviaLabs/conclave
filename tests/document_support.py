"""Builders for real PDF and Word documents, written byte by byte.

The document-reading tests build every document they read, so no binary
fixtures are committed and each test shows what its document contains.
Nothing here imports the new interfaces; `pypdf` is imported only to encrypt.
"""

import io
import zipfile
from xml.sax.saxutils import escape

# -- PDF -------------------------------------------------------------------------------


def _pdf_string(text: str) -> str:
    return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _page_stream(lines):
    if lines is None:
        # A scanned page: something drawn, no text layer.
        return "0.8 g 60 300 470 450 re f"
    parts = ["BT", "/F1 11 Tf", "15 TL", "60 780 Td"]
    for index, line in enumerate(lines):
        parts.append(("" if index == 0 else "T* ") + _pdf_string(line) + " Tj")
    parts.append("ET")
    return "\n".join(parts)


def pdf_bytes(pages, *, password=None, owner_only=False) -> bytes:
    """A PDF with one page per item: a list of lines, or None for a page with
    no text layer. `password` needs one to open it; `owner_only` encrypts it
    with an empty user password, as a print-locked PDF is."""

    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for lines in pages:
        stream = _page_stream(lines).encode("latin-1")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")
        content = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content} 0 R "
                       f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{kid} 0 R' for kid in kids)}] /Count {len(kids)} >>"
    out, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + (body if isinstance(body, bytes) else body.encode("latin-1")) + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    data = bytes(out)
    if password is None and not owner_only:
        return data
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=password or "", owner_password="owner-secret", algorithm="AES-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


REPORT = [
    ["Q3 Support Review", "Prepared for the billing team, 14 October 2026.", "1. Summary",
     "Ticket volume rose 12% over Q2, driven by refund requests after",
     "the September price change. First response time held at 3h 10m."],
    ["2. Refunds", "Refund requests: 1,284 (Q2: 902). Median time to resolve: 2.1 days.",
     "Most common cause: customers charged twice when changing plans mid-cycle."],
    ["3. Recommendations", "- Deduplicate plan-change charges before invoicing.",
     "- Add a self-service refund form for charges under $50."],
]
REPORT_TEXT = """[Page 1]
Q3 Support Review
Prepared for the billing team, 14 October 2026.
1. Summary
Ticket volume rose 12% over Q2, driven by refund requests after
the September price change. First response time held at 3h 10m.
[Page 2]
2. Refunds
Refund requests: 1,284 (Q2: 902). Median time to resolve: 2.1 days.
Most common cause: customers charged twice when changing plans mid-cycle.
[Page 3]
3. Recommendations
- Deduplicate plan-change charges before invoicing.
- Add a self-service refund form for charges under $50."""

# -- Word -------------------------------------------------------------------------------

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _style(identifier, name, *, based_on=None, numbered=False):
    base = f'<w:basedOn w:val="{based_on}"/>' if based_on else ""
    number = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>' if numbered else ""
    return f'<w:style w:type="paragraph" w:styleId="{identifier}"><w:name w:val="{name}"/>{base}{number}</w:style>'


DEFAULT_STYLES = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">'
                  + _style("Normal", "Normal") + _style("Title", "Title", based_on="Normal")
                  + "".join(_style(f"Heading{level}", f"heading {level}", based_on="Normal") for level in range(1, 7))
                  + _style("ListBullet", "List Bullet", based_on="Normal", numbered=True)
                  + _style("Custom", "Case study heading", based_on="Heading2")
                  + "</w:styles>")


def p(text="", style=None, *, numbered=False, runs=None):
    """A paragraph: plain text, or `runs` given as raw run XML."""
    properties = ""
    if style or numbered:
        properties = ("<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "")
                      + ('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else "") + "</w:pPr>")
    body = runs if runs is not None else (f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>' if text else "")
    return f"<w:p>{properties}{body}</w:p>"


def table(rows):
    cells = "".join("<w:tr>" + "".join(f"<w:tc>{p(value)}</w:tc>" for value in row) + "</w:tr>" for row in rows)
    return f"<w:tbl>{cells}</w:tbl>"


POLICY_BODY = "".join([
    p("Refund policy", "Title"),
    p("This policy applies to all paid plans from 1 November 2026."),
    p("Who can ask for a refund", "Heading1"),
    p("Any account owner, within 30 days of the charge.", "ListBullet"),
    p("Billing contacts, for charges they approved.", "ListBullet"),
    p("Amounts", "Heading2"),
    table([("Charge", "Refund", "Approval"), ("Under $50", "Automatic", "None"), ("$50 and over", "On request", "Billing lead")]),
    p("Exceptions", "Heading1"),
    p("Annual plans are refunded pro rata after the first 30 days."),
])
POLICY_TEXT = """# Refund policy
This policy applies to all paid plans from 1 November 2026.
## Who can ask for a refund
- Any account owner, within 30 days of the charge.
- Billing contacts, for charges they approved.
### Amounts
Charge | Refund | Approval
Under $50 | Automatic | None
$50 and over | On request | Billing lead
## Exceptions
Annual plans are refunded pro rata after the first 30 days."""

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                 '</Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>')


def document_xml(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'


def docx_bytes(body=POLICY_BODY, *, styles=DEFAULT_STYLES, document=None, leave_out=()) -> bytes:
    """A .docx: `body` is the XML inside <w:body>; `document` replaces the whole part."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        parts = {"[Content_Types].xml": CONTENT_TYPES, "_rels/.rels": RELS, "word/styles.xml": styles,
                 "word/document.xml": document if document is not None else document_xml(body)}
        for name, value in parts.items():
            if name not in leave_out:
                archive.writestr(name, value)
    return buffer.getvalue()


def encrypted_docx_bytes() -> bytes:
    """What Word writes for a password-protected .docx: an OLE container holding
    an EncryptedPackage stream, not a zip."""
    return bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 504 + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 512


def legacy_doc_bytes() -> bytes:
    """A Word 97-2003 file: an OLE container with a WordDocument stream."""
    return bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 504 + "WordDocument".encode("utf-16-le") + b"\x00" * 512


BILLION_LAUGHS = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
                  + "".join(f'<!ENTITY lol{n} "{("&lol" + (str(n - 1) if n > 1 else "") + ";") * 10}">' for n in range(1, 10))
                  + f']><w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&lol9;</w:t></w:r></w:p></w:body></w:document>')
