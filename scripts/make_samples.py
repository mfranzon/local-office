# /// script
# dependencies = ["pillow"]
# ///
"""Generates deterministic test inputs in samples/: an invoice scan, a bar chart, and Italian speech."""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "samples"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"


def font(size):
    return ImageFont.truetype(FONT, size)


def invoice():
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    d.text((80, 80), "FATTURA N. 2026/0417", font=font(48), fill="black")
    d.text((80, 160), "Data: 15/10/2026", font=font(30), fill="black")
    d.text((80, 210), "Fornitore: Officine Rossi S.r.l. - P.IVA 01234567890", font=font(30), fill="black")
    d.text((80, 260), "Cliente: Studio Bianchi S.r.l., Trieste", font=font(30), fill="black")
    rows = [("Descrizione", "Qta", "Prezzo", "Totale"),
            ("Consulenza tecnica", "10", "85,00", "850,00"),
            ("Licenza software annuale", "1", "1.200,00", "1.200,00"),
            ("Supporto on-site", "2", "300,00", "600,00")]
    y = 380
    for i, row in enumerate(rows):
        for x, cell in zip((80, 700, 850, 1030), row):
            d.text((x, y), cell, font=font(30 if i else 32), fill="black")
        d.line((70, y + 50, 1170, y + 50), fill="gray", width=2)
        y += 70
    d.text((700, y + 40), "Imponibile: 2.650,00 EUR", font=font(32), fill="black")
    d.text((700, y + 90), "IVA 22%: 583,00 EUR", font=font(32), fill="black")
    d.text((700, y + 140), "Totale: 3.233,00 EUR", font=font(36), fill="black")
    img.rotate(0.7, fillcolor="white", expand=False).save(OUT / "invoice.png")


def chart():
    data = {"Q1": 120, "Q2": 185, "Q3": 160, "Q4": 240}
    img = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(img)
    d.text((250, 20), "Vendite 2025 (migliaia EUR)", font=font(30), fill="black")
    d.line((80, 520, 860, 520), fill="black", width=2)
    for i, (q, v) in enumerate(data.items()):
        x = 140 + i * 180
        d.rectangle((x, 520 - v * 1.6, x + 100, 520), fill="#3b6fb6")
        d.text((x + 30, 530), q, font=font(26), fill="black")
        d.text((x + 25, 520 - v * 1.6 - 36), str(v), font=font(26), fill="black")
    img.save(OUT / "chart.png")


def speech():
    text = ("Buongiorno a tutti. La riunione di oggi riguarda il budget del quarto trimestre. "
            "Marco preparerà il report entro venerdì, e Giulia contatterà il fornitore per la nuova licenza.")
    aiff = OUT / "speech.aiff"
    subprocess.run(["say", "-v", "Alice", "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(OUT / "speech_it.wav")], check=True)
    aiff.unlink()
    (OUT / "speech_it.txt").write_text(text)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    invoice(); chart(); speech()
    print("samples:", sorted(p.name for p in OUT.iterdir()))
