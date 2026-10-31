"""Recover the text reportlab drew inside a generated PDF.

Shared by the receipt, quotation and invoice PDF tests. Lives in its own
module so the extraction logic is not copied per test file.
"""
import base64
import re
import zlib


def _a85_then_inflate(blob: bytes):
    return zlib.decompress(base64.a85decode(blob, adobe=True))


def _inflate(blob: bytes):
    for step in (_a85_then_inflate, zlib.decompress):
        try:
            return step(blob)
        except Exception:
            continue
    return None


def _page_content_refs(data: bytes):
    for page in re.finditer(rb"/Type\s*/Page[^s]", data):
        window = data[max(0, page.start() - 300):page.start()]
        refs = re.findall(rb"/Contents\s+(\d+)\s+0\s+R", window)
        if refs:
            yield int(refs[-1])


def _content_streams(data: bytes):
    """Yield the decoded text of the page content streams.

    The page's ``/Contents`` object is resolved by reference rather than by
    scanning for ``stream`` markers: once the PDF carries an image, the binary
    image data contains that word itself and a naive scan finds garbage.
    """
    for obj in _page_content_refs(data):
        match = re.search(
            rb"%d 0 obj(.*?)endobj" % obj, data, re.DOTALL
        )
        if not match:
            continue
        start = match.group(1).find(b"stream")
        if start == -1:
            continue
        start += len(b"stream")
        while match.group(1)[start:start + 1] in (b"\r", b"\n"):
            start += 1
        end = match.group(1).find(b"endstream", start)
        if end == -1:
            continue
        blob = match.group(1)[start:end].strip()
        decoded = _inflate(blob)
        if decoded is not None:
            yield decoded


def _literal_strings(raw: bytes):
    """Text operands from a content stream.

    reportlab emits ``(...)`` for Latin-1 text and ``<hex>`` as soon as a
    character needs the font's encoding, so both forms are collected.
    """
    out = []
    index = 0
    while index < len(raw):
        ch = raw[index:index + 1]
        if ch == b"<":
            close = raw.find(b">", index)
            if close == -1:
                index += 1
                continue
            try:
                out.append(bytes.fromhex(raw[index + 1:close].decode()).decode("latin-1"))
            except ValueError:
                pass
            index = close + 1
            continue
        if ch != b"(":
            index += 1
            continue
        depth = 1
        index += 1
        buf = bytearray()
        while index < len(raw) and depth:
            ch = raw[index:index + 1]
            if ch == b"\\":
                index += 1
                # PDF escapes are either a single char or a 1-3 digit octal
                # code, so "\243" is one character and not three. The index is
                # already positioned past the escape, so no trailing bump.
                octal = raw[index:index + 3]
                digits = bytes(d for d in octal if 48 <= d <= 55)
                if digits:
                    buf.append(int(digits, 8))
                    index += len(digits)
                else:
                    buf += raw[index:index + 1]
                    index += 1
                continue
            elif ch == b"(":
                depth += 1
                buf += ch
            elif ch == b")":
                depth -= 1
                if depth:
                    buf += ch
            else:
                buf += ch
            index += 1
        out.append(bytes(buf).decode("latin-1", "replace"))
    return out


def pdf_text(data: bytes) -> str:
    """Text drawn in the PDF, recovered from its content streams.

    reportlab writes each string as a literal ``(...)`` operand, so the text is
    recovered by decompressing the streams and reading those operands out.
    """
    chunks = []
    for raw in _content_streams(data):
        chunks.extend(_literal_strings(raw))
    return " ".join(chunks)
