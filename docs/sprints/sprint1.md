# Sprint 1 · GPU 排程三部曲

線上推論與離線批次共用同一批 GPU 時，要決定誰先拿到卡、每個容器能用多少 VRAM，以及怎麼依條件挑選裝置。這個 sprint 在 AKS 的 T4 spot 節點池上依序操作 **KAI Scheduler**（佇列與排程）、**HAMi**（VRAM 配額與隔離，搭配 WebUI 查看用量）與 **Kubernetes DRA**（描述與挑選裝置），最後一章整理三者的分工。

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

    申請 GPU 配額，建立 AKS 叢集與 T4 spot 節點池，跑出第一顆 CUDA pod 的 `nvidia-smi`；也建立收工時把 GPU 節點縮到零、開工時復原的流程。

-   ![KAI Scheduler](../assets/logos/kai-scheduler-icon-color.svg){ width="44" }

    **Day 1 · [KAI 安裝與 Queue 基礎](../runbook/sprint1-day1-kai-queue-basics.md)**

    ---

    安裝 KAI Scheduler 並建立兩層佇列，學保底配額、閒置時向其他佇列借用額度，以及佇列優先權與 pod 優先權各自影響什麼。

-   ![KAI Scheduler](../assets/logos/kai-scheduler-icon-color.svg){ width="44" }

    **Day 2 · [Gang Scheduling 與搶占](../runbook/sprint1-day2-gang-scheduling-preemption.md)**

    ---

    把需要 3 顆 pod 的訓練工作放進只有 2 張卡的叢集，比較預設排程器與 KAI gang scheduling 的處理方式；也觀察搶占與 spot 節點回收時整組 pod 的行為。

-   ![HAMi](../assets/logos/hami-icon-color.svg){ width="44" }

    **Day 3 · [HAMi 安裝與 VRAM 硬隔離](../runbook/sprint1-day3-hami-memory-isolation.md)**

    ---

    用 HAMi 取代 NVIDIA device plugin，讓一張 T4 同時給多個容器使用。設定 VRAM 配額後，容器內只看得到分配到的大小，超用時只有該容器 OOM。

-   ![HAMi](../assets/logos/hami-icon-color.svg){ width="44" }

    **Day 4 · [HAMi 進階與 KAI 整合](../runbook/sprint1-day4-hami-kai-integration.md)**

    ---

    比較 HAMi 的 binpack 與 spread 放置策略，再照官方文件用 HAMi-core 把 VRAM 隔離接到 KAI Scheduler，讓走 KAI 排程的 pod 也能共用一張卡。

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

    安裝 NVIDIA 的 DRA driver，用 ResourceClaim 把一張實體 T4 配給 pod，確認容器內看到的 GPU 與申請一致；也說明 DRA driver 為什麼不能和傳統 device plugin 放在同一個節點。

-   **Day 8 · [綜合：三者分工決策表](../runbook/sprint1-day8-decision-matrix.md)**

    ---

    不動手。整理 KAI、HAMi、DRA 各自適合的場景，附場景選型練習，以及導入自家叢集時的建議清單。

</div>
