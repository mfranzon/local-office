"""Full HTTP smoke test with a synthetic invoice; leaves a labeled sample document."""
import argparse
import io
import json
from pathlib import Path
import time

import httpx
from openpyxl import load_workbook
from PIL import Image, ImageDraw, ImageFont


def events(response):
    response.raise_for_status()
    for line in response.iter_lines():
        if line.startswith("data: "):
            event = json.loads(line[6:])
            print(json.dumps(event, ensure_ascii=True), flush=True)
            if event["type"] == "error":
                raise RuntimeError(event["message"])
            yield event


def run(base, existing_doc=None):
    image = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30)
    for i, text in enumerate(["SAMPLE INVOICE LO-47815", "Supplier: Acme Stationery", "Customer: Example Office",
                              "Invoice date: 3 November 2026", "Payment due: 18 December 2026",
                              "Printer paper: 12 units at INR 250.00", "Total: INR 3000.00"]):
        draw.text((50, 50+i*80), text, font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    with httpx.Client(base_url=base, timeout=300) as client:
        setup = client.get("/api/setup").json()
        assert not setup["missing"], setup
        if existing_doc:
            doc_id = existing_doc
            current = client.get(f"/api/documents/{doc_id}").json()
            if current["wiki_status"] == "error":
                client.post("/api/wiki/rebuild").raise_for_status()
        else:
            upload = client.post("/api/documents", files={"files": ("Sample invoice - NPU check.png", buffer.getvalue(), "image/png")})
            upload.raise_for_status()
            doc_id = upload.json()["created"][0]
        for _ in range(150):
            doc = client.get(f"/api/documents/{doc_id}").json()
            print("Document", doc["status"], "wiki", doc["wiki_status"], flush=True)
            if doc["status"] == "error":
                raise RuntimeError(doc["error"])
            if doc["status"] == "ready":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Document ingestion timed out")
        assert "LO-47815" in doc["pages"][0]["text"]
        assert "3000" in doc["pages"][0]["text"]
        with client.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "What is the total amount on this invoice?"}],
                                                     "use_docs": True, "doc_ids": [doc_id]}) as response:
            reply = list(events(response))
        answer = "".join(e.get("text", "") for e in reply if e["type"] == "token")
        assert "3000" in answer.replace(",", ""), answer
        assert any(e["type"] == "sources" and e["sources"] for e in reply)
        assert "[1]" in answer, answer
        with client.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "/invoice-table Create an Excel table of the uploaded invoices."}],
                                                     "use_docs": True}) as response:
            skill = list(events(response))
        files = [e for e in skill if e["type"] == "file" and e["name"].lower().endswith(".xlsx")]
        assert files, "The invoice skill did not produce an XLSX workbook"
        for file in files:
            downloaded = client.get(file["url"])
            downloaded.raise_for_status()
            assert len(downloaded.content) > 100
            rows = list(load_workbook(io.BytesIO(downloaded.content), data_only=True).active.values)
            assert "LO-47815" in str(rows) and "3000" in str(rows), rows
        for _ in range(90):
            doc = client.get(f"/api/documents/{doc_id}").json()
            if doc["wiki_status"] in ("done", "error"):
                break
            time.sleep(2)
        assert doc["wiki_status"] == "done", doc
        wiki = client.get("/api/wiki").json()
        assert wiki["entities"], wiki
        print(json.dumps({"passed": True, "document_id": doc_id, "answer": answer,
                          "exports": files, "wiki_entities": len(wiki["entities"])}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--doc-id")
    options = parser.parse_args()
    run(options.base, options.doc_id)
