"""CPU embeddings from a pinned multilingual E5 ONNX export."""

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_FILE = "model_qint8_avx512_vnni.onnx"
FILES = {
    MODEL_FILE: (118346824, "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"),
    "tokenizer.json": (17082730, "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
}
EMBEDDING_SIZE = 384
MAX_TOKENS = 256


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_model(local: Path) -> None:
    local.mkdir(parents=True, exist_ok=True)
    for name, (size, digest) in FILES.items():
        path = local / name
        if path.is_file() and path.stat().st_size == size and sha256(path) == digest:
            continue
        url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/onnx/{name}?download=true"
        print(f"Downloading {MODEL}/{name}", flush=True)
        req = Request(url, headers={"User-Agent": "ESIS local RAG/1.0"})
        with NamedTemporaryFile(dir=local, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                with urlopen(req, timeout=90) as response:
                    remaining = size
                    while block := response.read(1024 * 1024):
                        remaining -= len(block)
                        if remaining < 0:
                            raise ValueError(f"{name} exceeds its published size")
                        stream.write(block)
                stream.close()
                if remaining or sha256(temporary) != digest:
                    raise ValueError(f"{name} did not match the pinned revision checksum")
                temporary.replace(path)
            finally:
                stream.close()
                temporary.unlink(missing_ok=True)


class LocalEmbeddings:
    def __init__(self, local: Path):
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(4, os.cpu_count() or 1)
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(local / MODEL_FILE), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(str(local / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self.names = {entry.name for entry in self.session.get_inputs()}

    def encode(self, texts: list[str], *, query: bool = False) -> np.ndarray:
        if not texts:
            return np.empty((0, EMBEDDING_SIZE), dtype=np.float32)
        prefix = "query: " if query else "passage: "
        encoded = self.tokenizer.encode_batch([prefix + text for text in texts])
        mask = np.array([entry.attention_mask for entry in encoded], dtype=np.int64)
        inputs = {
            "input_ids": np.array([entry.ids for entry in encoded], dtype=np.int64),
            "attention_mask": mask,
        }
        if "token_type_ids" in self.names:
            inputs["token_type_ids"] = np.array([entry.type_ids for entry in encoded], dtype=np.int64)
        hidden = self.session.run(["last_hidden_state"], inputs)[0]
        weights = mask[:, :, None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / weights.sum(axis=1)
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.maximum(norm, 1e-12)).astype(np.float32)
