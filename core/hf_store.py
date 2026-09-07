"""Minimal HF Dataset persistence helper for spend tracking & feedback.

Stores two files in the same HF Dataset repo:
  data.jsonl      — spend tracker records (default)
  feedback.jsonl  — user feedback records (gate #4)

Uses huggingface_hub HfApi to read/write JSON records.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_DATASET_REPO = os.getenv("HF_SPEND_DATASET", "").strip()
_HF_TOKEN = os.getenv("HF_TOKEN", "").strip()


def _ensure_configured() -> bool:
    """Return True if HF Dataset persistence env vars are set."""
    return bool(_DATASET_REPO and _HF_TOKEN)


def read_dataset(filename: str = "data.jsonl", repo: str | None = None, token: str | None = None) -> list[dict]:
    """Read all JSON records from the HF Dataset file.

    Args:
        filename: file in the dataset repo (default "data.jsonl" for spend).
    Returns the deserialized list of records, or [] on any failure.
    Never raises.
    """
    return _read_dataset(filename, repo or _DATASET_REPO, token or _HF_TOKEN)


def _read_dataset(filename: str, repo: str, token: str) -> list[dict]:
    if not repo or not token:
        logger.warning("HF_SPEND_DATASET or HF_TOKEN not set — cannot read dataset")
        return []
    try:
        from huggingface_hub import HfApi
        api = HfApi(endpoint="https://huggingface.co")
        raw = api.hf_hub_download(
            repo_id=repo,
            filename=filename,
            repo_type="dataset",
            token=token,
        )
        records = []
        with open(raw, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records
    except Exception:
        logger.warning("Failed to read HF Dataset %s", repo, exc_info=True)
        return []


def append_record(record: dict, filename: str = "data.jsonl", repo: str | None = None, token: str | None = None) -> bool:
    """Append one JSON record to the HF Dataset file.

    Args:
        filename: file in the dataset repo (default "data.jsonl" for spend).
    Returns True on success, False on failure (never raises).
    """
    return _append_record(record, filename, repo or _DATASET_REPO, token or _HF_TOKEN)


def _append_record(record: dict, filename: str, repo: str, token: str) -> bool:
    if not repo or not token:
        logger.warning("HF_SPEND_DATASET or HF_TOKEN not set — cannot write dataset")
        return False
    try:
        from huggingface_hub import HfApi
        from huggingface_hub.utils import EntryNotFoundError
        api = HfApi(endpoint="https://huggingface.co")
        line = json.dumps(record, ensure_ascii=False) + "\n"

        # download-append-reupload (known race on concurrent writes)
        try:
            local_path = api.hf_hub_download(
                repo_id=repo, filename=filename,
                repo_type="dataset", token=token,
            )
            with open(local_path, "a", encoding="utf-8") as f:
                f.write(line)
            api.upload_file(
                path_or_fileobj=local_path,
                path_in_repo=filename,
                repo_id=repo,
                repo_type="dataset",
                token=token,
            )
        except EntryNotFoundError:
            # File doesn't exist yet — create it fresh (single record).
            # Any OTHER exception (network/auth/rate-limit) propagates to
            # the outer try/except, which logs a warning and returns False
            # — never fall through to the create-new-file path on a
            # transient error, which would silently destroy prior history.
            api.upload_file(
                path_or_fileobj=line.encode(),
                path_in_repo=filename,
                repo_id=repo,
                repo_type="dataset",
                token=token,
            )
        return True
    except Exception:
        logger.warning("Failed to append to HF Dataset %s/%s", repo, filename, exc_info=True)
        return False
