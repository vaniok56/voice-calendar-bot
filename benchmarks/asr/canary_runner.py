import sys
import time

import soundfile as sf
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

from backend_common import run_cases


MODEL_NAME = sys.argv[1]
MODEL_ID = "nvidia/canary-1b-v2"
REVISION = "d455706339a6b32e1aa40f82c713a482a0c938e2"

torch.set_num_threads(5)
torch.set_num_interop_threads(1)
started = time.perf_counter()
processor = AutoProcessor.from_pretrained(MODEL_ID, revision=REVISION)
model = AutoModelForSpeechSeq2Seq.from_pretrained(
    MODEL_ID,
    revision=REVISION,
    dtype=torch.float32,
    attn_implementation="eager",
).eval()
load_seconds = time.perf_counter() - started


def transcribe(item, wav):
    audio, sample_rate = sf.read(wav, dtype="float32", always_2d=False)
    inputs = processor.apply_transcription_request(
        audio=audio,
        source_language=item["dominant_language"],
    ).to("cpu")
    with torch.inference_mode():
        token_ids = model.generate(**inputs, max_new_tokens=512)
    return processor.decode(token_ids, skip_special_tokens=True)[0], item["dominant_language"]


run_cases(MODEL_NAME, transcribe, load_seconds)
