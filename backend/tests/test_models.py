import pytest

from app import models


@pytest.mark.parametrize("ram,assistant,embed", [
    (64, "Qwen3.5 9B", "Qwen3 Embedding 0.6B"),
    (24, "Qwen3.5 9B", "Qwen3 Embedding 0.6B"),
    (16, "Qwen3.5 4B", "Qwen3 Embedding 0.6B"),
    (8, "Qwen3.5 2B", "EmbeddingGemma 300M"),
    (0, "Qwen3.5 4B", "Qwen3 Embedding 0.6B"),   # RAM unknown: assume 16 GB
])
def test_recommended_model_fits_the_mac(ram, assistant, embed):
    assert models.recommended("assistant", ram).name == assistant
    assert models.recommended("embed", ram).name == embed
    assert {models.recommended(r, ram).role for r in ("ocr", "agent")} == {"ocr", "agent"}
