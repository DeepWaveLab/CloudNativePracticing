# Sprint 1 · GPU 排程三部曲

線上推論正在使用 GPU，離線批次也需要同一批卡。只看節點還有沒有空閒 GPU，無法回答每個團隊應保留多少，也無法限制共用一張卡的程式各用多少 VRAM。這個 sprint 在 AKS 的 T4 spot 節點上操作 KAI Scheduler、HAMi 與 Kubernetes DRA，逐步區分排程、記憶體隔離與裝置選擇。

<div style="text-align: center;" markdown>

[![KAI Scheduler](../assets/logos/kai-scheduler-icon-color.svg){ width="88" }](https://www.cncf.io/projects/kai-scheduler/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![HAMi](../assets/logos/hami-icon-color.svg){ width="88" }](https://project-hami.io/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="88" }](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)

*KAI Scheduler 管理 GPU 排程，HAMi 限制容器可用的 VRAM，HAMi-WebUI 顯示配額與用量，Kubernetes DRA 用新的 API 描述與挑選裝置。*

</div>

## 課程路線（Day 0–8）

<div class="grid cards" markdown>

-   **Day 0 · [環境建置](../runbook/sprint1-day0-azure-aks-foundation.md)**

    ---

    申請 GPU 配額，建立 AKS 叢集與 T4 spot 節點池，跑出第一顆 CUDA pod 的 `nvidia-smi`；也建立用完後把 GPU 節點縮到零、再次使用時復原的流程。

-   ![KAI Scheduler](../assets/logos/kai-scheduler-icon-color.svg){ width="44" }

    **Day 1 · [KAI 安裝與 Queue 基礎](../runbook/sprint1-day1-kai-queue-basics.md)**

    ---

    安裝 KAI Scheduler 並建立兩層佇列，學保底配額、閒置時向其他佇列借用額度，以及佇列優先權與 pod 優先權各自影響什麼。

-   ![KAI Scheduler](../assets/logos/kai-scheduler-icon-color.svg){ width="44" }

    **Day 2 · [Gang Scheduling 與搶占](../runbook/sprint1-day2-gang-scheduling-preemption.md)**

    ---

    把需要 3 顆 pod 的訓練工作放進只有 2 張卡的叢集，比較預設排程器與 KAI gang scheduling 的處理方式；也觀察搶占與 spot 節點回收時整組 pod 的行為。

-   ![HAMi](../assets/logos/hami-icon-color.svg){ width="44" }

    **Day 3 · [HAMi 安裝與 VRAM 用量限制](../runbook/sprint1-day3-hami-memory-isolation.md)**

    ---

    用 HAMi 取代 NVIDIA device plugin，讓一張 T4 同時給多個容器使用。設定 VRAM 配額後，觀察容器內回報的大小，並以超額配置與另一個容器的資料校驗檢查影響範圍。

-   ![HAMi](../assets/logos/hami-icon-color.svg){ width="44" }

    **Day 4 · [HAMi 與 KAI 整合](../runbook/sprint1-day4-hami-kai-integration.md)**

    ---

    照官方文件用 HAMi-core 把 VRAM 隔離接到 KAI Scheduler，讓走 KAI 排程的 pod 也能共用一張卡，並找出兩套 GPU 方案裝在同一座叢集時為什麼會互相卡住。

-   ![HAMi-WebUI](../assets/logos/hami-webui-logo.png#only-light){ width="150" }
    ![HAMi-WebUI](../assets/logos/hami-webui-logo-dark.png#only-dark){ width="150" }

    **Day 5 · [HAMi-WebUI 觀測介面](../runbook/sprint1-day5-hami-webui.md)**

    ---

    安裝 HAMi-WebUI 與它需要的 Prometheus Operator，在網頁上看每張卡的配額、用量與分配給哪顆 pod，並學會分辨畫面上的 0 是沒人使用還是資料沒有送到。

-   ![Kubernetes DRA](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 6 · [DRA 概念與模擬裝置](../runbook/sprint1-day6-dra-simulated-devices.md)**

    ---

    用官方的模擬 driver 學 DRA 的四個 API 物件與 CEL 選擇器：依條件挑選裝置、排除特定型號，以及讓兩顆 pod 共用同一個裝置。

-   ![Kubernetes DRA](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 7 · [DRA 配置實體 GPU](../runbook/sprint1-day7-dra-aks-real-gpu.md)**

    ---

    安裝 NVIDIA 的 DRA driver，用 ResourceClaim 把一張實體 T4 配給 pod，確認容器內看到的 GPU 與申請一致；也說明如何避免 DRA driver 與傳統 device plugin 重複分配同一張卡。

-   **Day 8 · [Sprint 1 總結](../runbook/sprint1-day8-decision-matrix.md)**

    ---

    不動手。回頭看三套工具各管哪一層，拿到一個 GPU 需求時怎麼選，以及導入既有叢集前要先查什麼。

</div>
