"""Minimal, dependency-free PDF writer for demo invoices.

Each text item returns its bounding box as page fractions, so the demo extraction sidecar
can carry exact evidence (page + bbox + source text) like a real model would.
"""

from __future__ import annotations

from dataclasses import dataclass, field

W, H = 595.0, 842.0  # A4 points


@dataclass
class Page:
    ops: list[str] = field(default_factory=list)

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float = 10,
        bold: bool = False,
        right: bool = False,
        gray: float = 0.0,
    ) -> tuple[float, float, float, float]:
        """y is from the top. Returns bbox (x0, y0, x1, y1) as fractions of the page."""
        width = len(s) * size * (0.56 if bold else 0.52)
        if right:
            x -= width
        font = "F2" if bold else "F1"
        esc = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.ops.append(f"BT {gray:.2f} g /{font} {size} Tf {x:.1f} {H - y:.1f} Td ({esc}) Tj ET")
        return (
            round(x / W, 4),
            round((y - size) / H, 4),
            round((x + width) / W, 4),
            round((y + 3) / H, 4),
        )

    def line(self, x0: float, y: float, x1: float, gray: float = 0.75, w: float = 0.6) -> None:
        self.ops.append(f"{gray:.2f} G {w} w {x0:.1f} {H - y:.1f} m {x1:.1f} {H - y:.1f} l S")

    def rect(self, x: float, y: float, w: float, h: float, gray: float = 0.95) -> None:
        self.ops.append(f"{gray:.2f} g {x:.1f} {H - y - h:.1f} {w:.1f} {h:.1f} re f")


def build(pages: list[Page], salt: str = "") -> bytes:
    """Serialise pages. `salt` lets a 're-scan' of the same invoice produce different bytes."""
    objs: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    for i, p in enumerate(pages):
        stream = ("\n".join(p.ops) + (f"\n% {salt}" if salt else "")).encode("latin-1", "replace")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W:.0f} {H:.0f}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {6 + 2 * i} 0 R >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
