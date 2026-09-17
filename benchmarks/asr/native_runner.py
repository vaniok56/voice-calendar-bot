import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


MANIFEST = Path("/work/manifest.json")
WAV = Path("/work/wav")
RESULTS = Path("/work/results")
LOGS = Path("/work/logs")
TIMEOUT_SECONDS = 10 * 60

WHISPER_MODELS = {
    "whisper-small": (
        "ggml-small-q5_1.bin",
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-small-q5_1.bin?download=true",
        "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
    ),
    "whisper-medium": (
        "ggml-medium-q5_0.bin",
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-medium-q5_0.bin?download=true",
        "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f",
    ),
    "whisper-turbo": (
        "ggml-large-v3-turbo-q5_0.bin",
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-large-v3-turbo-q5_0.bin?download=true",
        "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    ),
}


def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def prepare():
    WAV.mkdir(parents=True, exist_ok=True)
    for item in manifest():
        destination = WAV / item["wav_file"]
        if destination.exists():
            continue
        temporary = destination.with_suffix(".partial.wav")
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", f"/audio/{item['source_file']}", "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le", str(temporary),
        ], check=True, timeout=120)
        temporary.replace(destination)


def download(url: str, destination: Path, expected_hash: str):
    if destination.exists() and sha256(destination) == expected_hash:
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    subprocess.run([
        "curl", "-fL", "--retry", "5", "--retry-all-errors", "-C", "-",
        "-o", str(partial), url,
    ], check=True)
    if sha256(partial) != expected_hash:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Hash mismatch for {destination.name}")
    partial.replace(destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def completed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    completed = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            completed.add(json.loads(line)["id"])
        except (json.JSONDecodeError, KeyError):
            pass
    return completed


def peak_rss(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except OSError:
        pass
    return None


def multipart(wav: Path, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    body.extend((
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{wav.name}\"\r\n"
        "Content-Type: audio/wav\r\n\r\n"
    ).encode())
    body.extend(wav.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def request_transcript(url: str, wav: Path, fields: dict[str, str]) -> str:
    body, content_type = multipart(wav, fields)
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8").strip()


def wait_ready(url: str, process: subprocess.Popen, timeout: int = 600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Server exited with {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.5)
    raise TimeoutError("Server did not become ready")


def stop_server(process: subprocess.Popen):
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_model(model_name: str, kind: str):
    RESULTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    output = RESULTS / f"{model_name}.jsonl"
    remaining = [item for item in manifest() if item["id"] not in completed_ids(output)]
    if not remaining:
        return

    model_dir = Path("/models/native")
    model_dir.mkdir(parents=True, exist_ok=True)
    if kind == "parakeet":
        environment = os.environ | {"NEMO_SPEECH_MODEL_DIR": str(model_dir / "nemo")}
        subprocess.run(["nemo-speech", "pull", "parakeet-tdt"], check=True, env=environment)
        command = [
            "nemo-speech", "serve", "--asr-model", "parakeet-tdt", "--device", "cpu",
            "--threads", "1", "--host", "127.0.0.1", "--port", "8080", "--no-ui",
        ]
        ready_url = "http://127.0.0.1:8080/ready"
        inference_url = "http://127.0.0.1:8080/v1/audio/transcriptions"
        fields = {"response_format": "text", "language": "auto"}
    else:
        filename, url, digest = WHISPER_MODELS[kind]
        model_path = model_dir / filename
        download(url, model_path, digest)
        environment = os.environ
        command = [
            "whisper-server", "-m", str(model_path), "-t", "5", "-l", "auto", "-ng",
            "--host", "127.0.0.1", "--port", "8081",
        ]
        ready_url = "http://127.0.0.1:8081/"
        inference_url = "http://127.0.0.1:8081/inference"
        fields = {"response_format": "text"}

    server_log = (LOGS / f"{model_name}-server.log").open("a", encoding="utf-8")
    load_started = time.perf_counter()
    process = subprocess.Popen(command, stdout=server_log, stderr=subprocess.STDOUT, env=environment)
    try:
        wait_ready(ready_url, process)
        load_seconds = time.perf_counter() - load_started
        with output.open("a", encoding="utf-8") as file:
            for item in remaining:
                started = time.perf_counter()
                record = {"id": item["id"], "model": model_name, "load_seconds": load_seconds}
                try:
                    transcript = request_transcript(inference_url, WAV / item["wav_file"], fields)
                    record.update({"status": "ok", "transcript": transcript, "detected_language": None})
                except Exception as error:
                    record.update({"status": "error", "error": f"{type(error).__name__}: {error}"})
                record["elapsed_seconds"] = time.perf_counter() - started
                record["peak_rss_kib"] = peak_rss(process.pid)
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
                file.flush()
    finally:
        stop_server(process)
        server_log.close()


if sys.argv[1] == "prepare":
    prepare()
elif sys.argv[1] == "model":
    run_model(sys.argv[2], sys.argv[3])
else:
    raise SystemExit("Usage: native_runner.py prepare | model NAME KIND")
