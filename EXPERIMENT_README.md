# 实验副本说明 —— E:\mcgill-实验版

本目录是 D:\mcgill（mcgill-citation-tool）的**实验副本**，用于在不影响生产的前提下
试验 CHATBOX / EXPANSION 相关设计。创建方式与隔离措施如下。

## 一、创建方式

从**已提交的 HEAD**克隆，不是文件夹复制：

```
源仓库:   D:\mcgill
HEAD:     f8a0485  "docs: describe verified project capabilities"
命令:     git clone --no-hardlinks D:/mcgill "E:/mcgill-实验版"
```

- 克隆后 HEAD 与源仓库完全一致（f8a04856f52fc3a979c40a834a27deb0025eaecc）。
- 受版本控制的文件 **159 个**，与源仓库 `git ls-files` 数量一致。
- 未复制工作区里的未提交内容：`.venv`、`.venv_new`、`__pycache__`、`.pytest_cache`、
  `.gradio`、`.zcode`、`syllabi/`、`_deploy_verify.py` 等一概未带入。

## 二、单独复制的 4 份未提交设计文档

以下 4 份在源仓库中处于未跟踪（untracked）状态，已单独复制到本副本 `docs/`，
在本副本中**同样保持未跟踪**（不提交），以对应其原始状态：

| 文件 | 源状态 |
|---|---|
| docs/CHATBOX_ENGINEERING_BLUEPRINT.md | 未跟踪 |
| docs/CHATBOX_EXTENSION.md | 未跟踪 |
| docs/EXPANSION_MASTER_BLUEPRINT.md | 未跟踪 |
| docs/PHASE1_IMPLEMENTATION_PROMPT.md | 未跟踪 |

**未**复制的其他未跟踪内容，因此本副本不存在：
- `docs/PHASE2_FOOTNOTE_REFERENCES.md`（不在指定清单内）
- `syllabi/`、`_deploy_verify.py`

## 三、未复制的内容（按要求排除）

| 项 | 说明 |
|---|---|
| `.env` | 源仓库 `.env` 含 HF_TOKEN / DEEPSEEK_API_KEY / CANLII_API_KEY 等生产密钥，**未复制** |
| `config/settings.py` | 源仓库中虽被 `.gitignore` 忽略，也**未复制**（克隆不含） |
| 生产密钥 | 全部未带入，见下节验证 |
| 反馈通知配置 | 源侧反馈通知靠环境变量 `DISCORD_FEEDBACK_WEBHOOK`，**未复制、未设置** |

本副本新建了一个**全新的** `.env`（不是源文件的拷贝）：所有密钥与 HF/Discord 目标项
一律留空，仅保留占位说明与本地实验用限额。留空即自动降级为 no-op。

## 四、禁止自动部署到当前 HF / 生产目标

已实施 5 层隔离，任一层单独生效即可阻断：

1. **GitHub Actions 部署工作流已移出**
   `.github/workflows/sync-to-hf.yml`（push 到 main 即同步上传 HF Space）与
   `.github/workflows/keep-alive.yml`（每 15 分钟 ping 生产 HF 健康 URL）
   已移到 `.github/workflows.disabled/`，文件名加 `.disabled` 后缀，
   GitHub Actions 不会发现也不会执行。目录内保留了原文与禁用原因。

2. **HF 上传脚本加硬门禁**
   `.github/scripts/sync_upload.py` 开头即校验 `MCGILL_ALLOW_PROD_DEPLOY`，
   不等于 `1` 时 `sys.exit`，在导入 `huggingface_hub` 之前就退出。

3. **远端重接：推送地址被禁用**
   - `source`（原 origin，指向本地 D:\mcgill）→ push URL 置为 `no-push://blocked-experiment-copy`
   - `prod`（https://github.com/yubow47-bot/mcgill-citation-tool.git）→ 仅 fetch，push URL 同样置为 `no-push://...`
   即：本副本默认既不能推回源仓库，也不能推回生产 GitHub 仓库。

4. **pre-push 钩子二次拦截**（`.git/hooks/pre-push`）
   命中 huggingface / github.com / gitlab / vercel / railway / no-push 等地址一律拒绝，
   并打印提示。唯一放行方式是显式 `MCGILL_ALLOW_PROD_DEPLOY=1 git push ...`。

5. **运行时写入路径天然失效**
   本副本 `.env` 中 `HF_TOKEN`、`HF_SPEND_DATASET`、`HF_SPACE_ID`、
   `DISCORD_FEEDBACK_WEBHOOK` 全为空：
   - `core/hf_store.py` 的 `_ensure_configured()` 返回 False → 花费/反馈都不写 HF Dataset
   - `core/discord_notify.py` 的 `notify()` 直接返回 False → 不发任何 Discord 通知
   - `/api/feedback` 的本地 JSONL 仍会写 `data/feedback.jsonl`（仅本地文件）

## 五、验证记录

见 `EXPERIMENT_VERIFY.md`（含实际命令与输出）。

## 六、运行本副本前须知

- 本副本**无虚拟环境**，需要时自行创建：
  `python -m venv .venv` 然后按 `requirements.txt` 安装。
- 需要真实调用 LLM 时，在**本副本** `.env` 中填入你自己的测试 key，
  不要把生产 key 放进这里。
- 若要临时放行某个部署动作，必须先确认目标不是生产 Space；
  用 `MCGILL_ALLOW_PROD_DEPLOY=1` 显式放行，用完请恢复。
