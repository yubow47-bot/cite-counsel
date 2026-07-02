"""Unit tests for core/hf_store.py — _append_record data-loss fix.

Run: pytest tests/test_hf_store.py -v
"""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from huggingface_hub.utils import EntryNotFoundError

from core.hf_store import _append_record

# ── Constants ──

_REPO = "test/repo"
_TOKEN = "hf_test_token"
_RECORD = {"key": "value"}

# The expected JSON-line form of the record
_EXPECTED_LINE = json.dumps(_RECORD, ensure_ascii=False) + "\n"


# ═════════════════════════════════════════════════════════════════════════════
#  Test 1: file genuinely missing → EntryNotFoundError → create new file
# ═════════════════════════════════════════════════════════════════════════════

@patch("huggingface_hub.HfApi")
def test_missing_file_creates_new(MockHfApi):
    """hf_hub_download raises EntryNotFoundError -> upload_file creates new file."""
    mock_api = MagicMock()
    MockHfApi.return_value = mock_api

    # hf_hub_download raises "file not found"
    mock_api.hf_hub_download.side_effect = EntryNotFoundError("File not found")

    result = _append_record(_RECORD, "data.jsonl", _REPO, _TOKEN)

    assert result is True, "Should return True after creating new file"

    # upload_file should have been called ONCE with the new record bytes
    mock_api.upload_file.assert_called_once_with(
        path_or_fileobj=_EXPECTED_LINE.encode(),
        path_in_repo="data.jsonl",
        repo_id=_REPO,
        repo_type="dataset",
        token=_TOKEN,
    )
    # hf_hub_download was called (and failed)
    mock_api.hf_hub_download.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
#  Test 2: transient error on download -> NOT EntryNotFoundError -> no overwrite
# ═════════════════════════════════════════════════════════════════════════════

@patch("huggingface_hub.HfApi")
def test_transient_download_error_no_overwrite(MockHfApi):
    """hf_hub_download raises a generic exception -> upload_file is NOT called."""
    mock_api = MagicMock()
    MockHfApi.return_value = mock_api

    # hf_hub_download raises a network-level error (NOT EntryNotFoundError)
    mock_api.hf_hub_download.side_effect = ConnectionError("Network error")

    result = _append_record(_RECORD, "data.jsonl", _REPO, _TOKEN)

    assert result is False, "Should return False on transient error"

    # upload_file must NOT be called -- no overwrite of existing data
    mock_api.upload_file.assert_not_called()
    mock_api.hf_hub_download.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
#  Test 3: reupload fails after successful download -> no overwrite
# ═════════════════════════════════════════════════════════════════════════════

@patch("huggingface_hub.HfApi")
def test_reupload_failure_no_overwrite(MockHfApi):
    """hf_hub_download succeeds, then upload_file (reupload) raises -> no second upload."""
    mock_api = MagicMock()
    MockHfApi.return_value = mock_api

    # Create a real temp file so open(local_path, "a") succeeds
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write('{"existing": "data"}\n')
        local_path = f.name

    try:
        mock_api.hf_hub_download.return_value = local_path

        # upload_file raises on the reupload call
        mock_api.upload_file.side_effect = RuntimeError("Upload failed")

        result = _append_record(_RECORD, "data.jsonl", _REPO, _TOKEN)

        assert result is False, "Should return False when reupload fails"

        # upload_file was called exactly once (the failed reupload),
        # NOT a second time with just the new record
        mock_api.upload_file.assert_called_once()

        # The single call must have been the reupload (path_or_fileobj is the
        # local file, not the encoded line) -- verify it was NOT the destructive
        # create-new-file path, which would pass line.encode()
        call_kwargs = mock_api.upload_file.call_args[1]
        assert call_kwargs.get("path_or_fileobj") == local_path, (
            "upload_file must have been called with the local file path (reupload), "
            "not with line.encode() (create-new-file path)"
        )
    finally:
        # Clean up the temp file
        try:
            os.unlink(local_path)
        except OSError:
            pass
