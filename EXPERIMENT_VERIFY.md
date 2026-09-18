# 实验副本验证记录（实际执行输出）

副本路径: `E:\mcgill-实验版`    源: `D:\mcgill`    验证时间: 2026-09-18

## 1. HEAD 一致性（要求 1：从已提交 HEAD 创建）

```
$ git clone --no-hardlinks D:/mcgill "E:/mcgill-实验版"
Cloning into 'E:/mcgill-实验版'...
done.

源 D:\mcgill   : f8a04856f52fc3a979c40a834a27deb0025eaecc  f8a0485 docs: describe verified project capabilities
副本 E:\...    : f8a04856f52fc3a979c40a834a27deb0025eaecc  f8a0485 docs: describe verified project capabilities
副本 tracked 文件数: 159 == 源 tracked 文件数 159
副本工作区 (克隆后): 干净，无未跟踪文件
```

未随克隆进入副本的源工作区内容：`.venv/`、`.venv_new/`、`__pycache__/`、`.pytest_cache/`、
`.gradio/`、`.zcode/`、`syllabi/`、`_deploy_verify.py`、`.env`、`config/settings.py`。

## 2. 4 份未提交设计文档（要求 2）

按清单单独复制，md5 与源逐一致：

```
OK   CHATBOX_ENGINEERING_BLUEPRINT.md  8b0626c34eb313ac7e4c6438798578b3
OK   CHATBOX_EXTENSION.md              ba90403f2faa5a56ad8b8f24b45eafe1
OK   EXPANSION_MASTER_BLUEPRINT.md     9a6e5b040f6c737d917797ef7287fdab
OK   PHASE1_IMPLEMENTATION_PROMPT.md   09f8c3c8aba4c72de2d2dac7938b80f3
```

副本中这 4 份仍为未跟踪状态（与源一致）：

```
$ git status --short
?? docs/CHATBOX_ENGINEERING_BLUEPRINT.md
?? docs/CHATBOX_EXTENSION.md
?? docs/EXPANSION_MASTER_BLUEPRINT.md
?? docs/PHASE1_IMPLEMENTATION_PROMPT.md
```

源侧 `docs/PHASE2_FOOTNOTE_REFERENCES.md`、`syllabi/`、`_deploy_verify.py` 未复制（不在清单内）。

## 3. 密钥/配置未复制（要求 3）

```
$ ls -a | grep -E '^\.env|settings.py'      → 克隆后无 .env、无 config/settings.py
$ grep -rInE 'sk-[A-Za-z0-9]{16,}|hf_[A-Za-z0-9]{20,}|discord(app)?\.com/api/webhooks/.*' .  → 无输出（未发现任何密钥）
$ git check-ignore -v .env
.gitignore:5:.env	.env                    → 新建的 .env 处于忽略状态，不会误提交
```

副本中新建的 `.env` 为全新文件（非源拷贝），敏感项全空：
`HF_TOKEN=""  HF_SPEND_DATASET=""  HF_SPACE_ID=""  DISCORD_FEEDBACK_WEBHOOK=""  CANLII_API_KEY=""`

## 4. 禁止自动部署到 HF / 生产（要求 4）

### 4.1 Actions 工作流已移出

```
$ find .github -type f
.github/scripts/sync_upload.py
.github/workflows.disabled/keep-alive.yml.disabled
.github/workflows.disabled/sync-to-hf.yml.disabled

$ find .github -name "*.yml" -o -name "*.yaml" | wc -l
0
```

`sync-to-hf.yml`（push 到 main 即上传 HF Space）与 `keep-alive.yml`（每 15 分钟 ping
生产 HF 健康 URL）均已移出 `workflows/` 并加 `.disabled` 后缀，GitHub Actions 不会发现。

### 4.2 远端推送地址已禁用

```
$ git remote -v
prod	https://github.com/yubow47-bot/mcgill-citation-tool.git (fetch)
prod	no-push://blocked-experiment-copy (push)
source	D:/mcgill (fetch)
source	no-push://blocked-experiment-copy (push)
```

实测拦截（连生产远端与源仓库都推不出去）：

```
$ git push prod main
git: 'remote-no-push' is not a git command. See 'git --help'.
fatal: remote helper 'no-push' aborted session

$ git push source main
git: 'remote-no-push' is not a git command. See 'git --help'.
fatal: remote helper 'no-push' aborted session
```

### 4.3 pre-push 钩子（第二道拦截，直接测试）

```
$ sh .git/hooks/pre-push prod "https://github.com/yubow47-bot/mcgill-citation-tool.git"
  BLOCKED: 实验副本禁止推送/部署到生产或云远端。
  远端: prod  (https://github.com/yubow47-bot/mcgill-citation-tool.git)
  exit=1

$ sh .git/hooks/pre-push prod "https://huggingface.co/spaces/xxx/yyy"
  BLOCKED ... exit=1

$ sh .git/hooks/pre-push source "D:/mcgill"
  exit=0    # 本地路径远端放行

$ MCGILL_ALLOW_PROD_DEPLOY=1 sh .git/hooks/pre-push prod "https://github.com/x/y"
  pre-push: MCGILL_ALLOW_PROD_DEPLOY=1，放行。  exit=0   # 显式放行
```

### 4.4 HF 上传脚本硬门禁

`.github/scripts/sync_upload.py` 在 `import huggingface_hub` 之前先校验
`MCGILL_ALLOW_PROD_DEPLOY == "1"`，否则 `sys.exit` 并打印阻断原因。

### 4.5 运行时实测：写入路径完全失效（真实执行，非推断）

用源仓库解释器运行副本代码，把副本 `.env` 注入环境后调用：

```
注入后关键项:
  HF_TOKEN = ''   HF_SPEND_DATASET = ''   HF_SPACE_ID = ''
  DISCORD_FEEDBACK_WEBHOOK = ''   CANLII_API_KEY = ''   MCGILL_EXPERIMENT = '1'

WARNING:core.hf_store:HF_SPEND_DATASET or HF_TOKEN not set — cannot write dataset
WARNING:core.hf_store:HF_SPEND_DATASET or HF_TOKEN not set — cannot read dataset

[HF] _ensure_configured() = False      ← 不触达 HF
[HF] append_record()      = False      ← 写入未发生
[HF] read_dataset()       = []         ← 读取未发生
[Discord] notify()        = False      ← 通知未发送

[网络] 本轮 append_record 产生的出站连接数 = 0 []
```

最后一项是对 `socket.connect` 打桩统计得出的**真实出站连接数 = 0**，
证明 HF/Discord 路径没有发出任何网络请求。

## 5. 副本现状

```
副本 HEAD: f8a0485 + 1 个本地提交 fab96d9（仅隔离措施，不影响源仓库）
           fab96d9 chore(experiment): 隔离实验副本，禁用向 HF/生产的自动部署
           f8a0485 docs: describe verified project capabilities   ← 与源 HEAD 相同
tracked:   160（159 + EXPERIMENT_README.md / 工作流重命名均在同一提交内）
体积:      工作区 ~1.9M，.git 7.7M
副本无虚拟环境，需要时自行 python -m venv .venv
```

源仓库 `D:\mcgill` 未被修改（仅读取）。
