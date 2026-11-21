import argparse
import os
from pathlib import Path

from ilkelma.languages import packs

ROOT = Path(__file__).resolve().parent / "models"
os.environ["HF_HOME"] = str(ROOT / ".hf")
os.environ["HF_HUB_CACHE"] = str(ROOT / ".hf" / "hub")
os.environ["HF_HUB_DISABLE_XET"] = "1"

from huggingface_hub import hf_hub_download, snapshot_download


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download local speech models.")
    parser.add_argument("--parakeet", action="store_true", help="Download only the optional Parakeet model.")
    if parser.parse_args().parakeet:
        import onnx_asr
        from ilkelma.engine.stt import PARAKEET_MODEL

        onnx_asr.load_model(PARAKEET_MODEL, ROOT / "parakeet-tdt-0.6b-v3", quantization="int8",
                            providers=["CPUExecutionProvider"])
        raise SystemExit(0)
    for repo, pack in {pack["model"]: pack for pack in packs().values()}.items():
        folder = ROOT / repo.rsplit("/", 1)[-1]
        snapshot_download(repo, local_dir=folder,
                          allow_patterns=["*.json", "*.bin", "*.txt", "README.md"])
        if pack.get("tokenizer") and not (folder / "tokenizer.json").is_file():
            hf_hub_download(pack["tokenizer"], "tokenizer.json", local_dir=folder)
