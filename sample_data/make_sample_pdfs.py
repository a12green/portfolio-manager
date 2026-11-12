"""Generate the fictional statement PDFs in sample_data/statements/.

Needs reportlab (see requirements-dev.txt):

    pip install -r requirements-dev.txt
    python sample_data/make_sample_pdfs.py

The figures and names are made up. The layout is simplified to the lines the
statement parsers look for, so they are not copies of any real statement.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
OUT = HERE / "statements"


def _write_pdf(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=A4)
    y = A4[1] - 60
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 18
    pdf.showPage()
    pdf.save()


def fidelity_statement() -> None:
    # Owner-B individual account, month end 30 Sep 2026.
    # Stored in an ACCOUNT_B folder, which the importer uses to pick the account.
    _write_pdf(
        OUT / "fidelity" / "ACCOUNT_B" / "Statement9302026.pdf",
        [
            "Fidelity Brokerage Services - Monthly Statement (sample, fictional)",
            "Statement period: 1 September 2026 to 30 September 2026",
            "Account: HOLDER B - INDIVIDUAL",
            "",
            "Account Summary",
            "Total Holdings",
            "$48,215.60",
            "Total Core Account (5% of account holdings)",
            "$48,215.60",
            "$2,410.78",
            "Total Stocks (95% of account holdings)",
            "$45,804.82",
            "$45,804.82",
            "Your Account Value:",
            "$48,215.60",
        ],
    )


def aussuper_statement() -> None:
    # Annual statement for the year to 30 June 2026. The filename token
    # 2026JUNST is what the importer uses for the snapshot date.
    _write_pdf(
        OUT / "aussuper" / "AusSuper2026JUNST.pdf",
        [
            "Super Fund - Annual super statement (sample, fictional)",
            "for 1 July 2025 to 30 June 2026",
            "",
            "Your super balance at 30 June 2025",
            "$6,581.45",
            "Your super balance at 30 June 2026",
            "$7,128.15",
            "",
            "Your investment summary",
            "Total",
            "-1,213.90",
            "7,128.15",
            "100.00",
        ],
    )


if __name__ == "__main__":
    fidelity_statement()
    aussuper_statement()
    for pdf in sorted(OUT.rglob("*.pdf")):
        print("wrote", pdf.relative_to(HERE))
