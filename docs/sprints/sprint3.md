# Sprint 3 · WebAssembly

容器之外的另一種執行層。這門課在 AKS 上把 wasm 進 Kubernetes 的三條現行路線——**wasmCloud**、**WasmEdge + runwasi**、**Spin/SpinKube**——各實測一輪,寫成可照抄的 runbook。每一章的指令與輸出都來自真實跑過的驗證紀錄,途中踩到的 **41 顆雷**以具名地雷收錄在各章。

<div style="text-align: center;" markdown>

[![WebAssembly](../assets/logos/webassembly-icon-color.svg){ width="88" }](https://webassembly.org/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![wasmCloud](../assets/logos/wasmcloud-icon-color.svg){ width="80" }](https://wasmcloud.com/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![WasmEdge](../assets/logos/wasmedge-icon-color.svg){ width="76" }](https://wasmedge.org/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![SpinKube](../assets/logos/spinkube-icon-color.svg){ width="80" }](https://www.spinkube.dev/)

*三條路線:wasmCloud(完全不碰節點)、WasmEdge(手動裝 shim)、SpinKube(operator 替你改節點)。*

</div>

三條路線刻意照**對節點的侵入程度由低到高**排:最乾淨的先做,最後才動 containerd,前面的量測才不會被後面的改動污染,而最後一天也才有乾淨的基準可以驗「拆得掉嗎」。

**Day 0 給三個問題與判斷方法出發**:工作負載要不要長得像 Kubernetes 原生?動機是不是冷啟動或密度?你選的工具明年還在嗎?每條路線收尾那天當場下判定,Day 9 收成決策表——途中有兩個量測結果跟宣傳方向相反。

## 課程路線(Day 0–9)

<div class="grid cards" markdown>

-   ![WebAssembly](../assets/logos/webassembly-icon-color.svg){ width="44" }

    **Day 0 · [wasm 是什麼](../runbook/sprint3-day0-wasm-concepts.md)**

    ---

    不是語言,是編譯目標;預設零能力的沙箱。三個問題與「怎麼判斷工具還活著」的五條檢查清單,都從這裡出發。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 1 · [RuntimeClass 七層實測](../runbook/sprint3-day1-three-generations.md)**

    ---

    把 RuntimeClass 從 Pod spec 追到節點上的 shim 行程,七層逐層指出來;順便對已退役的 WASI node pool 下指令,看它今天回什麼。

-   ![wasmCloud](../assets/logos/wasmcloud-icon-color.svg){ width="44" }

    **Day 2 · [wasmCloud 與元件模型](../runbook/sprint3-day2-wasmcloud.md)**

    ---

    一條不碰節點的路線:五份 diff 零行輸出為證。代價換到哪裡去了,三筆帳一起看。

-   ![wasmCloud](../assets/logos/wasmcloud-icon-color.svg){ width="44" }

    **Day 3 · [分散式模型與驗收改寫](../runbook/sprint3-day3-wasmcloud-distributed.md)**

    ---

    原訂驗收在 2.6.1 上表達不出來——判定「做不到」需要三層證據。改驗它實際支援的事,兩半都過。

-   ![WasmEdge](../assets/logos/wasmedge-icon-color.svg){ width="44" }

    **Day 4 · [WasmEdge 執行期](../runbook/sprint3-day4-wasmedge.md)**

    ---

    手動裝 shim,精確量出「第幾步開始節點不一樣」。而「同一支 wasm 程式」在兩個執行期之間不存在——差在第 5 個位元組。

-   ![WebAssembly](../assets/logos/webassembly-icon-color.svg){ width="44" }

    **Day 5 · [成本實測](../runbook/sprint3-day5-cost-measurement.md)**

    ---

    同一份原始碼編三個目標。冷啟動的差異量不出來,記憶體的差異方向跟宣傳相反——兩個帶信賴區間的否定結論。

-   ![SpinKube](../assets/logos/spinkube-icon-color.svg){ width="44" }

    **Day 6 · [SpinKube(上):shim 佈建](../runbook/sprint3-day6-spinkube-shim.md)**

    ---

    operator 替你改節點——供應鏈、紀錄、範圍六件事都做得比手動好,唯一做壞的是它用猜的那一步。

-   ![SpinKube](../assets/logos/spinkube-icon-color.svg){ width="44" }

    **Day 7 · [SpinKube(下):operator](../runbook/sprint3-day7-spinkube-operator.md)**

    ---

    停更 13 個月的 operator 裝起來零摩擦,而它其實不是執行機制——一份三個欄位的 Deployment 證明了這件事。

-   ![SpinKube](../assets/logos/spinkube-icon-color.svg){ width="44" }

    **Day 8 · [拆得掉嗎:可逆性驗收](../runbook/sprint3-day8-reversibility.md)**

    ---

    兩個情境的解除安裝對照,結論收在一句話:在 AKS 上,能用的 SpinKube 一定拆不乾淨。

-   ![WebAssembly](../assets/logos/webassembly-icon-color.svg){ width="44" }

    **Day 9 · [綜合:三條路線的決策表](../runbook/sprint3-day9-decision-matrix.md)**

    ---

    三條路線在各自收尾那天下過的判定,收成 44 格決策表——每一格標明實測、查證還是推論。含整個 sprint 的總帳。

</div>

## 這個 sprint 的量測紀律

- **節點基準先存後比**:Day 1 存下出廠節點的四份快照,之後每一天的「有沒有動到節點」都拿它逐字 diff——Day 2 五份零行、Day 8 拆完歸零,證據都是 `exit=0`。
- **量不出來就說量不出來**:冷啟動用 null control 加 bootstrap 信賴區間,兩批判定不一致就不排名。
- **驗收表達不出來就改寫,不硬湊**:Day 3、Day 4 各改寫一次,原本要驗什麼、為什麼不行、改驗什麼,三段寫清楚。

從 [Day 0](../runbook/sprint3-day0-wasm-concepts.md) 開始。

---

!!! quote ""
    WebAssembly 標誌為 WebAssembly 專案之官方資產(CC0 1.0);wasmCloud、WasmEdge(wasm-edge-runtime)、SpinKube、Kubernetes 標誌為 CNCF(Linux Foundation)官方資產。此處皆作社群教學用途。
