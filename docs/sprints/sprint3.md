# Sprint 3 · WebAssembly

WebAssembly（wasm）是容器之外另一種在 Kubernetes 上執行程式的方式。這個 sprint 先說明 wasm 是什麼、Kubernetes 怎麼透過 RuntimeClass 與 containerd shim 執行它，再在 AKS 上操作三條路線：**wasmCloud**、**WasmEdge + runwasi**、**Spin/SpinKube**，了解各自的部署方式與對節點的改動。過程中也會量測 WasmEdge 與一般容器的冷啟動與記憶體用量，並練習移除 SpinKube、檢查節點上的殘留，最後一章整理成選型用的決策表。

<div style="text-align: center;" markdown>

[![WebAssembly](../assets/logos/webassembly-icon-color.svg){ width="88" }](https://webassembly.org/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![wasmCloud](../assets/logos/wasmcloud-icon-color.svg){ width="80" }](https://wasmcloud.com/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![WasmEdge](../assets/logos/wasmedge-icon-color.svg){ width="76" }](https://wasmedge.org/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![SpinKube](../assets/logos/spinkube-icon-color.svg){ width="80" }](https://www.spinkube.dev/)

*三條路線對節點的改動由小到大：wasmCloud 不改動節點，WasmEdge 要手動安裝 shim，SpinKube 由 operator 修改節點設定。*

</div>

三條路線依對節點的改動由小到大排列。Day 1 會先存下原始節點的設定，之後每條路線都拿它比對，看出安裝時改了什麼、移除後還留下什麼。

Day 0 會提出三個選型問題：工作負載需不需要用一般的 Kubernetes 物件管理？導入的動機是冷啟動速度還是部署密度？選用的工具明年是否還在維護？每條路線結束時都用這三個問題判斷，Day 9 整理成決策表。

## 課程路線（Day 0–9）

<div class="grid cards" markdown>

-   ![WebAssembly](../assets/logos/webassembly-icon-color.svg){ width="44" }

    **Day 0 · [wasm 是什麼](../runbook/sprint3-day0-wasm-concepts.md)**

    ---

    不需要叢集。介紹 wasm 這種編譯目標、它預設沒有任何系統能力的沙箱模型，以及 Kubernetes 執行 wasm 的機制；並提供判斷開源專案是否還在維護的檢查清單。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 1 · [RuntimeClass 與節點執行路徑](../runbook/sprint3-day1-three-generations.md)**

    ---

    建立叢集，把 RuntimeClass 從 Pod spec 一路追到節點上的 shim 行程，逐層說明每一段的設定；也確認 Krustlet 與 AKS WASI node pool 這兩條已退役路線的現況，並存下之後比對用的節點設定。

-   ![wasmCloud](../assets/logos/wasmcloud-icon-color.svg){ width="44" }

    **Day 2 · [wasmCloud 與元件模型](../runbook/sprint3-day2-wasmcloud.md)**

    ---

    安裝 wasmCloud 並執行第一個元件，了解它為什麼不經過 kubelet 與 CRI；比對節點設定確認這條路線沒有改動節點，並說明這種做法的代價。

-   ![wasmCloud](../assets/logos/wasmcloud-icon-color.svg){ width="44" }

    **Day 3 · [wasmCloud 分散式模型](../runbook/sprint3-day3-wasmcloud-distributed.md)**

    ---

    加入第二台節點，學 wasmCloud 的分散式模型：同一個映像怎麼只靠能力設定改變行為，以及 workload 會被排到哪台節點；也說明怎麼確認某項功能在目前版本裡沒有對應機制。

-   ![WasmEdge](../assets/logos/wasmedge-icon-color.svg){ width="44" }

    **Day 4 · [WasmEdge 執行期](../runbook/sprint3-day4-wasmedge.md)**

    ---

    手動安裝 WasmEdge shim、修改 containerd 設定並建立 RuntimeClass，每做一步就和原始節點比對；也說明同一支 wasm 程式為什麼不一定能在兩個執行期之間通用。

-   ![WebAssembly](../assets/logos/webassembly-icon-color.svg){ width="44" }

    **Day 5 · [冷啟動、記憶體與映像大小](../runbook/sprint3-day5-cost-measurement.md)**

    ---

    把同一份原始碼編成三個目標，在同一顆節點上比較冷啟動、記憶體與映像大小，並學會用對照組與信賴區間判斷量到的差異是否成立。

-   ![SpinKube](../assets/logos/spinkube-icon-color.svg){ width="44" }

    **Day 6 · [SpinKube（上）：shim 佈建](../runbook/sprint3-day6-spinkube-shim.md)**

    ---

    安裝 cert-manager 與 runtime-class-manager，由它自動把 Spin 的 shim 裝上節點；和 Day 4 的手動流程對照，看自動化做了哪些事，以及它在 AKS 上判斷錯 containerd 設定路徑時怎麼發現與修正。

-   ![SpinKube](../assets/logos/spinkube-icon-color.svg){ width="44" }

    **Day 7 · [SpinKube（下）：operator](../runbook/sprint3-day7-spinkube-operator.md)**

    ---

    安裝 spin-operator 與 `SpinApp` CRD，再用一份一般的 Deployment 執行同一支 Spin 應用，說明 operator 與執行機制是兩件分開的事。

-   ![SpinKube](../assets/logos/spinkube-icon-color.svg){ width="44" }

    **Day 8 · [SpinKube 移除與節點殘留](../runbook/sprint3-day8-reversibility.md)**

    ---

    由上而下逐層移除 SpinKube，每移除一層就和 Day 1 的節點設定比對；對照設定有沒有手動改過的兩種情境，列出需要手動清理的殘留。

-   ![WebAssembly](../assets/logos/webassembly-icon-color.svg){ width="44" }

    **Day 9 · [綜合：三條路線的決策表](../runbook/sprint3-day9-decision-matrix.md)**

    ---

    不動手。用 Day 0 的三個問題比較三條路線，判斷各自適合哪種工作負載，以及導入前還要確認哪些限制。

</div>

## 學完之後

- 能說明 Kubernetes 執行 wasm 的完整路徑（RuntimeClass → handler → containerd shim），並在節點上找到每一層對應的設定。
- 能依工作負載型態、導入動機與專案維護狀態，判斷三條路線裡哪一條適合自家平台，或目前都不適合。
- 導入會改動節點的元件之前，知道要先存下節點設定、移除後再比對殘留。

從 [Day 0](../runbook/sprint3-day0-wasm-concepts.md) 開始。

---

!!! quote ""
    WebAssembly 標誌為 WebAssembly 專案之官方資產（CC0 1.0）；wasmCloud、WasmEdge（wasm-edge-runtime）、SpinKube、Kubernetes 標誌為 CNCF（Linux Foundation）官方資產。此處皆作社群教學用途。
