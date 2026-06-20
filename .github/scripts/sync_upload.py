import os
from huggingface_hub import upload_folder

upload_folder(
    folder_path=".",
    repo_id=os.environ["HF_SPACE_ID"],
    repo_type="space",
    token=os.environ["HF_TOKEN"],
    commit_message="Sync from GitHub main",
    ignore_patterns=[".git/*", ".github/*"],
)
