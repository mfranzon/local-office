"""Verify that the invoice skill reads sources and creates a real, correct workbook."""
import io
import json
import httpx
from openpyxl import load_workbook

with httpx.Client(base_url="http://127.0.0.1:8765", timeout=300) as client:
    output = []
    with client.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content":
        "/invoice-table Create an Excel table of the uploaded invoices."}], "use_docs": True}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            print(json.dumps(event, ensure_ascii=True), flush=True)
            assert event["type"] != "error", event
            output.append(event)
    files = [e for e in output if e["type"] == "file"]
    assert files, "No workbook was created"
    for file in files:
        data = client.get(file["url"])
        data.raise_for_status()
        if file["name"].endswith(".xlsx"):
            rows = list(load_workbook(io.BytesIO(data.content), data_only=True).active.values)
            print(json.dumps({"workbook_rows": rows}, ensure_ascii=True), flush=True)
            assert "LO-47815" in str(rows), rows
            assert "3000" in str(rows), rows
    print("PASS: invoice skill created a source-grounded workbook", flush=True)
