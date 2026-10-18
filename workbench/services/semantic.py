"""Local CPU semantic retrieval. No text is sent to an embedding API."""
import os
import threading
from pathlib import Path

MODEL_NAME = 'BAAI/bge-small-zh-v1.5'
MODEL_CACHE = Path(__file__).resolve().parents[2] / 'data' / 'library-models'
_lock = threading.RLock()
_model = None


def load(*, download=False):
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding
            _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(MODEL_CACHE),
                threads=2, local_files_only=not download)
        return _model


def encode(texts):
    with _lock:
        model = load()
        return [vector.tolist() for vector in model.embed(texts, batch_size=16)]


def encode_query(text):
    with _lock:
        return next(load().query_embed(text[:2000])).tolist()


def encode_chunks(text):
    chunks = [text[pos:pos+360] for pos in range(0, len(text), 300) if text[pos:pos+360].strip()]
    vectors = encode(chunks)
    return [{'text': chunk, 'vector': vector} for chunk, vector in zip(chunks, vectors)]


def cosine(a, b):
    if len(a) != len(b) or not a:
        return -1.0
    from math import sqrt
    divisor = sqrt(sum(x*x for x in a) * sum(y*y for y in b))
    return sum(x*y for x, y in zip(a,b))/divisor if divisor else -1.0
