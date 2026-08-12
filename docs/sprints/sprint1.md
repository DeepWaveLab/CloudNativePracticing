# Sprint 1 · GPU 排程三部曲

兩張 GPU,一邊是線上推論、一邊是離線批次,誰先拿到卡、拿到之後能用多少、「一張卡」這個概念本身還能不能再切——這門課在 AKS 上把 **KAI Scheduler**、**HAMi**(含 WebUI)、**Kubernetes DRA** 三套機制各實測一輪,寫成可照抄的 runbook。每一章的指令與輸出都來自真實跑過的驗證紀錄,途中踩到的 **41 顆雷**以具名地雷收錄在各章。

<div style="text-align: center;" markdown>

[![KAI Scheduler](../assets/logos/kai-scheduler-icon-color.svg){ width="88" }](https://www.cncf.io/projects/kai-scheduler/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![HAMi](../assets/logos/hami-icon-color.svg){ width="88" }](https://project-hami.io/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="88" }](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)

*三位主角:KAI Scheduler(誰先拿到卡)、HAMi 與它的 WebUI(拿到之後能用多少)、Kubernetes DRA(重新定義「一張卡」)。*

</div>

## 九天的路線(Day 0–8)

<div class="grid cards" markdown>

-   **Day 0 · [環境建置與成本紀律](../runbook/sprint1-day0-azure-aks-foundation.md)**

    ---

    quota 的三個維度、AKS 與 T4 spot pool、收工歸零的循環——先把「隨時有卡、隨時不燒錢」變成事實。

-   ![KAI Scheduler](../assets/logos/kai-scheduler-icon-color.svg){ width="44" }

    **Day 1 · [KAI 安裝與 Queue 基礎](../runbook/sprint1-day1-kai-queue-basics.md)**

    ---

    佇列階層、保底配額與超額借用,第一顆走 KAI 排程的 GPU pod,以及兩套優先權的心智模型。

-   ![KAI Scheduler](../assets/logos/kai-scheduler-icon-color.svg){ width="44" }

    **Day 2 · [Gang Scheduling 與搶占](../runbook/sprint1-day2-gang-scheduling-preemption.md)**

    ---

    全上或全不上的實證、搶一張卡賠掉整組的代價,加碼親手觸發一場 spot 節點回收。

-   ![HAMi](../assets/logos/hami-icon-color.svg){ width="44" }

    **Day 3 · [HAMi 安裝與 VRAM 硬隔離](../runbook/sprint1-day3-hami-memory-isolation.md)**

    ---

    一張 T4 同時服務四個容器,超配的 OOM 只炸在自己容器裡——實體卡還空著 9.5 GB 的那一刻。

-   ![HAMi](../assets/logos/hami-icon-color.svg){ width="44" }

    **Day 4 · [HAMi 進階與 KAI 整合](../runbook/sprint1-day4-hami-kai-integration.md)**

    ---

    binpack 與 spread 的一行之差、兩套帳本互鎖的真相,以及官方整合真正的運作方式。

-   ![HAMi-WebUI](../assets/logos/hami-webui-logo.png#only-light){ width="150" }
    ![HAMi-WebUI](../assets/logos/hami-webui-logo-dark.png#only-dark){ width="150" }

    **Day 5 · [HAMi-WebUI 觀測介面](../runbook/sprint1-day5-hami-webui.md)**

    ---

    給切卡的帳本一張臉:配額鏈與用量鏈、畫面上的 0 有幾種來源,並附一次真實 spot 回收的觀測案例。

-   ![Kubernetes DRA](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 6 · [DRA 概念與模擬裝置](../runbook/sprint1-day6-dra-simulated-devices.md)**

    ---

    用模擬裝置走完四個 API 與 CEL 選擇器:挑規格、排型號、兩顆 pod 具名共用同一個裝置。

-   ![Kubernetes DRA](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 7 · [DRA 真卡實測](../runbook/sprint1-day7-dra-aks-real-gpu.md)**

    ---

    回答一個沒人寫過的問題:Tesla T4 走 DRA 到底能不能動——答案是能,證據攤在章節裡。

-   **Day 8 · [綜合:三者分工決策表](../runbook/sprint1-day8-decision-matrix.md)**

    ---

    八天的數字收斂成一張決策表:什麼場景用哪套、哪些格子誠實留白,以及帶回自家叢集的建議清單。

</div>
