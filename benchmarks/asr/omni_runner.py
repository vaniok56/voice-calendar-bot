import sys
import time

import torch
from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline

from backend_common import run_cases


MODEL_NAME = sys.argv[1]
MODEL_CARD = sys.argv[2]

torch.set_num_threads(5)
torch.set_num_interop_threads(1)
started = time.perf_counter()
pipeline = ASRInferencePipeline(model_card=MODEL_CARD, device="cpu", dtype=torch.float32)
load_seconds = time.perf_counter() - started


def transcribe(_item, wav):
    transcript = pipeline.transcribe([str(wav)], lang=None, batch_size=1)[0]
    return transcript, None


run_cases(MODEL_NAME, transcribe, load_seconds)
