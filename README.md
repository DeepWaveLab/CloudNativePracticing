# 雲原生 GPU 排程實作課程

**📖 課程網站:https://deepwavelab.github.io/CloudNativePracticing/**

## 這是什麼

一份「做出來的」雲原生 GPU 排程教材:在 Azure AKS 上實測 KAI Scheduler、HAMi、DRA 三套 GPU 排程/切分機制,所有指令都在真實環境跑過、所有輸出都是實測,寫成可照抄的 runbook。

## 本機預覽

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-docs.txt
.venv/bin/mkdocs serve          # http://127.0.0.1:8000
```

發佈到 GitHub Pages:`mkdocs gh-deploy`(push 不等於上線,要手動跑這個指令才會真的部署)。

## 圖像出處

站內 KAI Scheduler、HAMi 與 Kubernetes 標誌取自 [CNCF 官方 artwork](https://github.com/cncf/artwork)(Linux Foundation);HAMi-WebUI 圖示取自其官方 repo 內建資產。均作社群教學用途,版權歸原基金會所有。

## 目錄結構

```
├── mkdocs.yml            # 網站設定(nav / theme / extensions)
├── requirements-docs.txt # mkdocs-material 釘版
├── course.yaml           # 課程狀態(style_approved、地雷索引)
└── docs/
    ├── index.md          # 首頁
    ├── runbook/          # 課程章節(sprint1-day*)
    ├── solutions/        # 排錯手冊
    └── assets/           # 圖片與素材
```
