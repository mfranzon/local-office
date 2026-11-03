"""Exercise installed models with real NPU inference, without changing app data."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import models, npu
from app.llm import image_part


async def run():
    try:
        for role in sys.argv[1:] or ["assistant", "ocr", "embed", "vision"]:
            spec = models.recommended(role)
            print(f"Testing {role}: {spec.name}; downloaded={spec.downloaded}", flush=True)
            if role == "embed":
                vectors = await npu.embed(spec, ["Invoice for printer paper", "Delivery of notebooks"])
                assert len(vectors) == 2 and all(len(v) == 512 for v in vectors)
                print("PASS: two 512-dimensional NPU embeddings", flush=True)
                continue
            content = "Reply with OK only."
            if role in ("ocr", "vision"):
                content = [image_part(models._test_image("Codice 47815")),
                           {"type": "text", "text": spec.prompt or "Read the number in this image."}]
            if role == "vision":
                content = [image_part(models._test_vision_image(), "image/png"),
                           {"type": "text", "text": "What color is the rectangle? Reply in English."}]
            response = await npu.complete(spec, [{"role": "user", "content": content}], max_tokens=256)
            print(response, flush=True)
            expected = "47815" if role == "ocr" else "blue" if role == "vision" else "OK"
            assert expected.lower() in response["content"].lower()
    finally:
        npu.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
