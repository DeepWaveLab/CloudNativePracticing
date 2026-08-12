# 雲原生實作課程

把雲原生 landscape 上真正在動的專案,一個一個放上真實叢集實測,寫成可照抄的 runbook——這是一系列動手課,一個 sprint 一個主題,依序推進。每一章遵守同一套紀律:

- **真實驗證**:所有指令與輸出都來自實際跑過的驗證紀錄,沒有想像的示意。
- **具名地雷**:官方文件查不到或會誤導的問題,收錄成可跨章引用的地雷索引(目前累計 **158 顆**)。
- **成本紀律**:雲端資源用 spot、收工歸零、自動安全網管著,每個 sprint 的實際花費都記在章節裡。

## 課程地圖

<div class="grid cards" markdown>

-   ![KAI Scheduler](assets/logos/kai-scheduler-icon-color.svg){ width="32" }
    ![HAMi](assets/logos/hami-icon-color.svg){ width="32" }
    ![Kubernetes](assets/logos/kubernetes-icon-color.svg){ width="32" }

    **Sprint 1 · [GPU 排程三部曲](sprints/sprint1.md)** — ✅ 已完結(9 章 · 41 顆地雷)

    ---

    兩張 T4 怎麼分給更多人:KAI Scheduler 的佇列與 gang scheduling、HAMi 的 VRAM 切分與硬隔離、Kubernetes DRA 的下一代資源表達,全部在 AKS 真卡實測。

-   ![eBPF](assets/logos/ebpf-logo.svg#only-light){ width="76" }
    ![eBPF](assets/logos/ebpf-logo-dark.svg#only-dark){ width="76" }
    ![Falco](assets/logos/falco-icon-color.svg){ width="28" }
    ![Tetragon](assets/logos/tetragon-icon-color.svg){ width="28" }
    ![Cilium](assets/logos/cilium-icon-color.svg){ width="28" }

    **Sprint 2 · [eBPF 與執行期安全](sprints/sprint2.md)** — ✅ 已完結(11 章 · 76 顆地雷)

    ---

    同一批核心事件,四種取用方式。先用 bpftrace 手工追 syscall,把核心事件裡的 cgroup id 換算回 pod 名字;換上 Falco 讓規則常駐比對,並量出把誤報從 180 筆/分鐘壓到 0 要交出多少偵測力;再換 Tetragon,把過濾放進核心裡,實測 SIGKILL 到底是擋住了操作還是只是事後殺掉行程。最後三天走到網路層。

    每一天兩套工具同時開著對打——**最值得看的結論,往往出現在它們答案一樣錯的那一格。Day 0 從零講起,不預設你碰過 eBPF。**

-   ![WebAssembly](assets/logos/webassembly-icon-color.svg){ width="32" }
    ![wasmCloud](assets/logos/wasmcloud-icon-color.svg){ width="28" }
    ![WasmEdge](assets/logos/wasmedge-icon-color.svg){ width="28" }
    ![SpinKube](assets/logos/spinkube-icon-color.svg){ width="28" }

    **Sprint 3 · [WebAssembly](sprints/sprint3.md)** — ✅ 已完結(10 章 · 41 顆地雷)

    ---

    wasmCloud、WasmEdge、Spin/SpinKube——容器之外的另一種執行層,三條路線依對節點的侵入程度由低到高實測。Day 0 給三個問題與判斷方法出發,每條路線收尾那天當場下判定,Day 9 收成一張 44 格、每格帶出處的決策表。

    途中有兩個量測結果跟宣傳方向相反,其中一條實測結論:**能用的 SpinKube,一定拆不乾淨。**

-   **Sprint 4 · 資料、OS 與多叢集** — 📅 規劃中

    ---

    KubeFleet、Drasi、Confidential Containers——跨叢集治理與機密運算。

-   **Sprint 5 · 遊戲伺服器平台** — 📅 規劃中

    ---

    Agones、Quilkin、Nakama——K8s 上的 dedicated game server 全套。

</div>

從 [Sprint 1](sprints/sprint1.md)、[Sprint 2](sprints/sprint2.md) 或 [Sprint 3](sprints/sprint3.md) 的總覽開始,或直接進 [Sprint 1 Day 0](runbook/sprint1-day0-azure-aks-foundation.md) 動手。
