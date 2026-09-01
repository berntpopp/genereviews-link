"""Identity of the dense embedding model the corpus was built with.

This lives OUTSIDE `genereview_link.corpus`: the serving image ships no ingest pipeline,
and the fleet OCI content policy denies every path with a `corpus` component, so the
server cannot read these constants from `corpus.tokenizer`.
"""

from __future__ import annotations

BGE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
BGE_MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
BGE_MODEL_FILE = "model.safetensors"
BGE_MODEL_FILE_SHA256 = "3c9f31665447c8911517620762200d2245a2518d6e7208acc78cd9db317e21ad"
BGE_MODEL_FILES = {
    "1_Pooling/config.json": "d1caf60c96f5fba2157c0c26b76d80818fad6cf0b8eb5e73ec372ff9818eba5c",
    "README.md": "ddb964361a55c6e5dfca6361615854b260c9c960205d04c7520151aaa1d75837",
    "config.json": "094f8e891b932f2000c92cfc663bac4c62069f5d8af5b5278c4306aef3084750",
    "config_sentence_transformers.json": "940d5f50db195fa6e5e6a4f122c095f77880de259d74b14a65779ed48bdd7c56",
    "model.safetensors": BGE_MODEL_FILE_SHA256,
    "modules.json": "84e40c8e006c9b1d6c122e02cba9b02458120b5fb0c87b746c41e0207cf642cf",
    "sentence_bert_config.json": "84e39fda68ccbff05bfa723ae9c0e70e23e2ec373b76e0f8c6e71af72a693cbf",
    "special_tokens_map.json": "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
    "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    "tokenizer_config.json": "9261e7d79b44c8195c1cada2b453e55b00aeb81e907a6664974b4d7776172ab3",
    "vocab.txt": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
}
BGE_DIM = 384  # output embedding dimension for bge-small-en-v1.5

#: The exact two files the SERVING runtime needs, and nothing else.
#:
#: The serving image cannot carry PyTorch: `torch`'s wheel alone is 526 MB and the fleet
#: OCI content policy caps any single file at 64 MiB. It runs the same weights through
#: ONNX Runtime instead (30 MB largest file), whose vectors reproduce the
#: sentence-transformers vectors the corpus was embedded with to float32 rounding --
#: min cosine 0.999999999796, max per-dimension delta 1.75e-07 across the parity suite in
#: `tests/unit/test_onnx_embedding_parity.py`.
#:
#: These are the trust root for the model bundle: the artifact is proven against them
#: before it is opened, exactly as the corpus artifact is proven against its digest.
BGE_ONNX_FILE = "model.onnx"
BGE_ONNX_FILE_SHA256 = "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35"
BGE_TOKENIZER_FILE = "tokenizer.json"
BGE_TOKENIZER_FILE_SHA256 = BGE_MODEL_FILES["tokenizer.json"]

#: `{member: sha256}` for the published model bundle, at the pinned revision.
BGE_RUNTIME_FILES = {
    BGE_ONNX_FILE: BGE_ONNX_FILE_SHA256,
    BGE_TOKENIZER_FILE: BGE_TOKENIZER_FILE_SHA256,
}

#: bge-small-en-v1.5 is CLS-pooled then L2-normalised (`1_Pooling/config.json` sets
#: `pooling_mode_cls_token: true`, `modules.json` appends `2_Normalize`). The pipeline is
#: pinned HERE, in reviewed code, and is deliberately NOT read from the artifact: a
#: downloaded file must never be able to change how its own output is computed.
BGE_POOLING = "cls"
BGE_MAX_SEQ_LENGTH = 512

__all__ = [
    "BGE_DIM",
    "BGE_MAX_SEQ_LENGTH",
    "BGE_MODEL_FILE",
    "BGE_MODEL_FILE_SHA256",
    "BGE_MODEL_NAME",
    "BGE_MODEL_REVISION",
    "BGE_ONNX_FILE",
    "BGE_ONNX_FILE_SHA256",
    "BGE_POOLING",
    "BGE_RUNTIME_FILES",
    "BGE_TOKENIZER_FILE",
    "BGE_TOKENIZER_FILE_SHA256",
]
