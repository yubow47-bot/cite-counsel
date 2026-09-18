"""HF Space 上传脚本 —— 实验副本中已加硬门禁。

本文件位于 E:\\mcgill-实验版（实验副本）。该副本禁止部署到现有 HF Space。
只有显式设置 MCGILL_ALLOW_PROD_DEPLOY=1 时才会真正上传，否则直接退出，
不会触碰任何 HF 目标。
"""

import os
import sys

if os.environ.get("MCGILL_ALLOW_PROD_DEPLOY", "").strip() != "1":
    sys.exit(
        "BLOCKED: 实验副本禁止部署到 HF/production。\n"
        "此门禁由 E:/mcgill-实验版 的隔离要求设置（见 EXPERIMENT_README.md）。\n"
        "如确需部署到独立测试 Space，请自行显式导出 MCGILL_ALLOW_PROD_DEPLOY=1。"
    )

from huggingface_hub import upload_folder  # noqa: E402

upload_folder(
    folder_path=".",
    repo_id=os.environ["HF_SPACE_ID"],
    repo_type="space",
    token=os.environ["HF_TOKEN"],
    commit_message="Sync from GitHub main",
    ignore_patterns=[".git/*", ".github/*"],
)
