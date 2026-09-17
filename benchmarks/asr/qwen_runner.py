import sys
import time

import torch
from huggingface_hub import snapshot_download
from qwen_asr import Qwen3ASRModel

from backend_common import run_cases


MODEL_ID = sys.argv[2]
MODEL_NAME = sys.argv[1]
REVISIONS = {
    "Qwen/Qwen3-ASR-0.6B": "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
    "Qwen/Qwen3-ASR-1.7B": "7278e1e70fe206f11671096ffdd38061171dd6e5",
}

torch.set_num_threads(5)
torch.set_num_interop_threads(1)
started = time.perf_counter()
model_dir = snapshot_download(MODEL_ID, revision=REVISIONS[MODEL_ID])
model = Qwen3ASRModel.from_pretrained(
    model_dir,
    dtype=torch.float32,
    attn_implementation="eager",
    max_inference_batch_size=1,
    max_new_tokens=512,
)
load_seconds = time.perf_counter() - started


def transcribe(_item, wav):
    result = model.transcribe(audio=str(wav), language=None)[0]
    return result.text, result.language


run_cases(MODEL_NAME, transcribe, load_seconds)
