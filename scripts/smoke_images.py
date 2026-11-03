import io
import json
import httpx
from PIL import Image, ImageDraw, ImageFont

with httpx.Client(base_url="http://127.0.0.1:8765", timeout=300) as client:
    for mode in ("ocr", "vision"):
        image = Image.new("RGB", (512, 512), "white")
        draw = ImageDraw.Draw(image)
        if mode == "ocr":
            draw.text((30, 180), "Code 47815", fill="black", font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 52))
            prompt, expected = "Read all text", "47815"
        else:
            draw.rectangle((50, 100, 460, 400), fill="blue")
            prompt, expected = "What color is the rectangle? Reply in English.", "blue"
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        answer = ""
        with client.stream("POST", "/api/vision", files={"image": ("test.png", buffer.getvalue(), "image/png")},
                           data={"mode": mode, "messages": json.dumps([{"role": "user", "content": prompt}])}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    assert event["type"] != "error", event
                    if event["type"] == "token":
                        answer += event["text"]
        print(json.dumps({"mode": mode, "answer": answer}), flush=True)
        assert expected in answer.lower(), answer
    print("PASS: OCR and vision image actions", flush=True)
