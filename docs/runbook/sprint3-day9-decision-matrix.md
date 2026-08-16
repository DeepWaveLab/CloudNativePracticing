# Day 9: 綜合——三條路線的決策表,每一格帶著它是哪天量到的

![WebAssembly 官方標誌](../assets/logos/webassembly-icon-color.svg){ align=right width="95" }

> Sprint 3 的最後一章。三條路線的判定都已經在各自收尾那天給了——wasmCloud 在 [Day 3](sprint3-day3-wasmcloud-distributed.md)、WasmEdge 在 [Day 4](sprint3-day4-wasmedge.md)、SpinKube 在 [Day 8](sprint3-day8-reversibility.md)。今天不揭曉任何新東西,只把它們收成一張可以直接拿去用的表,連同這個 sprint 的總帳。

!!! abstract "你在課程的哪裡"
    - **Day 0**:三個問題與判斷方法出發。
    - **Day 1–8**:三條路線實測,各自收尾那天下判定。
    - **今天**:把判定收成決策表、對總帳、收尾。

## 這張表怎麼讀

每一格後面掛著 `【類型|出處】`:

- **實測**=Day 1–8 在叢集上量到的;**查證**=2026-08-11 從一手來源取回(健康度、治理、上游 issue 現況);**推論**=從前兩者導出、未經驗證。`D<n>` 指第 n 天的章節。
- **類型標記不可以省。** 三條路線的實測厚度不對稱——wasmCloud 只有兩天,另外兩條各三天——混在一起,你就不知道哪一格站得比較穩。

## 表 A:三個入口問題

[Day 0 給的三題](sprint3-day0-wasm-concepts.md#three-questions),答完通常只剩一條或零條:

| 問題 | 答「是」 | 答「否」 |
|---|---|---|
| **一、wasm 工作負載要不要用一般 Kubernetes 物件管理?** | 只剩 **SpinKube**。原生 Deployment + `runtimeClassName` 直接跑,不需要 operator【實測|D7 驗收 (B):21 秒到 HTTP 200;對照組刪掉那一行得 `RunContainerError`】 | **wasmCloud** 進入候選。元件是 CRD 不是 Pod,`kubectl get pod` 的數量不隨元件增減【實測|D2】 |
| **二、主要動機是冷啟動或部署密度嗎?** | **走 containerd 的兩條不該選**;wasmCloud 這格沒有資料,不能給答案【實測|D5】【缺口|D5 wasmCloud 那一側】 | 繼續往下 |
| **三、能不能接受「節點池標籤讓換 VM 後自動重佈建」尚未被驗證?** | rcm 路線可評估 | rcm 路線降級為「先自己驗過再說」【實測|D7 手貼標籤不會自動重佈建】【查證|上游 issue #222 是文字,不是量測】 |

第一題的範圍限定:驗到的是 Deployment、Service、`kubectl logs`、Pod 層資源限制;**HPA、PDB、NetworkPolicy 沒有實測**,「它們也能用」是從「它是一顆普通 Pod」導出的推論。

## 表 B:三條路線橫向對照

| 面向 | wasmCloud(D2–D3) | WasmEdge + runwasi(D4–D5) | SpinKube(D6–D8) |
|---|---|---|---|
| 治理階段 | CNCF **Incubating**【查證】 | Sandbox;runwasi 是 containerd 子專案【查證】 | Sandbox【查證】 |
| 近 13 週 commits | 384【查證】 | WasmEdge 291;**runwasi 69,最近 8 週全 0**【查證】 | shim 16/rcm 65/operator 4,**三者最近 7–8 週全 0**【查證】 |
| bus factor | **最差**:74/100 同一人【查證】 | 最均勻:35/100,12 位作者【查證】 | operator 33%,另 33% 是機器人【查證】 |
| WASI 0.3 | v2.5.0 起**預設開啟**【查證】 | release notes 無此字樣,0.2 完成度仍掛 open issue【查證】 | Spin 端 opt-in;shim 側無此字樣【查證】 |
| 節點侵入 | **0**【實測|D2 五份 diff 零行,D3 兩顆節點皆同】 | shim 109.2 MiB + 設定區塊,手動寫入【實測|D4】 | shim 83.9 MiB + 6 行設定 + `/opt/rcm/` + 兩個標籤【實測|D6/D8】 |
| 在 AKS 開箱可用 | **是**【實測|D2】 | 否,要加 `use_local_image_pull`【實測|D4】 | 否,要手改 plugin domain【實測|D6,D7/D8 各重現一次】 |
| 換 VM 之後 | 自己回來【實測|D3 驗收 (B)】 | 全沒了,重跑腳本【實測|D5】 | 全沒了,重貼標籤【實測|D5/D7】 |
| 主動解除安裝 | `helm uninstall`,節點不動——**未實測**【推論|由 D2/D3 零侵入導出】 | 沒有【D4 收尾:全手動,無自動化】 | 有,但四個條件缺一不可,**能用的狀態下必失效**【實測|D8】 |
| 拆完的殘留 | 未拆(CRD + namespace + pod 留著,本課未實際拆除)【缺口|未實測】 | `RuntimeClass wasmedge` 名字在、實作不在【實測|D5】 | etcd 0、節點 0,**加五步手動收尾之後**【實測|D8】 |

## 表 C:什麼情境選哪一條

| 情境 | 建議 | 出處 |
|---|---|---|
| 既有 K8s 平台上讓 wasm 與容器並存,用同一套工作負載模型管 | **SpinKube,但只用 RuntimeClass + shim 兩層**;rcm 與 operator 視為可選便利層 | 【實測|D7 不經 operator 跑得起來;D8 拆 operator 時節點七份快照全同】 |
| 組跨雲/跨邊緣的 wasm 元件應用,接受自成一套執行模型 | **wasmCloud** | 【查證|2026-08-11】【實測|D3 跨節點排程與共享提供者】 |
| 只是要理解 RuntimeClass → handler → shim 這條鏈 | **WasmEdge 手動路線**,當學習路徑 | 【實測|D4 全手動流程,D1 七層對照】 |
| 既有程式碼是 core module,短期不搬 component | **WasmEdge**,接受標準層距離會拉開 | 【實測|D4】【推論|遷移成本未驗】 |
| 只需要 shim 那一層 | **不裝 rcm、不裝 operator**。執行層是 containerd(Graduated)+ shim,那兩個是便利層 | 【實測|D7/D8】 |
| 要讓 rcm 幫忙管節點 | 一律帶 `--set rcm.nodeInstallerJob.ttl=<秒>` | 【查證|2026-08-11】**未實測** |
| 平台是 AKS,打算跟著官方文件走 | **不要跟著那份文件走**——它教你裝已封存的 KWasm | 【查證|2026-08-11】該路徑未實測 |

## 表 D:什麼情境三條都不該選

| 情境 | 理由 | 出處 |
|---|---|---|
| 目標是降低冷啟動延遲 | wasmedge 與 runc 的差異落在雜訊邊緣、不能排名;兩者都比地板慢約 150 ms,那是容器管線本身。涵蓋走 containerd 的兩條;wasmCloud 沒有可比數字 | 【實測|D5,n=50×2,兩批 CI 判定不一致】 |
| 目標是同一批節點跑更多工作負載 | 節點層記憶體 wasm 較重:shim 常駐 38.8 MiB、每容器 +9.3 MiB/4 行程,對 runc 的 0.1/+4.5/1 | 【實測|D5】【缺口|wasmCloud 每元件記憶體沒拿到】 |
| 沒有人能負責節點層的 runbook 與收尾 | 兩條落在節點上的路線在 AKS 都不是開箱可用,而解除安裝的收尾沒有任何上游或雲廠商文件寫過 | 【實測|D4/D6/D8】 |
| 多節點、有既存流量的正式環境佈建與拆除 | 多節點行為、拆除對執行中 pod 的影響,全部沒驗 | 【缺口|D7/D8 誠實的差距】 |
| 依賴 Docker Desktop 當本機開發環境 | Wasm 工作負載已標 deprecated | 【查證|2026-08-11】 |

## 表 E:外推邊界——引用上面任何一格都要帶著

- 環境:AKS japaneast、Kubernetes 1.35.6、containerd 2.3.3、**單節點** `Standard_D2as_v5`。
- payload 是印一行字的程式;容器對照組是 scratch 靜態 musl,**容器側的最佳情況**。
- 冷啟動量在 `ctr` 層,端到端 pod 啟動時間沒有量;**映像大小整欄沒有數據**(Day 5 唯一未達成的一項)。
- **「三條路線的效能排名」不能寫**——只有兩條有數字,而那兩條量不出差異。
- 跨雲、跨規格、多節點、真實工作負載一律沒有資料;健康度數字查證於 2026-08-11,時點觀察不是趨勢。

## Sprint 3 總帳

| 項目 | 數字 |
|---|---|
| 章節 | 10 章(Day 0–9) |
| 地雷 | **41 顆**(#118–#158),全部帶錨點 |
| 驗收改寫 | 2 次(Day 3、Day 4——原要驗的能力在當時版本上不存在,改驗實際支援的事);另有 1 次驗收設計變更(Day 5 的發現讓 Day 8 加上「同一 VM 生命週期內完成」的規則) |
| 叢集開機時間 | 約 3 小時 08 分 |
| 雲端成本 | **約 NT$22–24** |

成本這格有一個值得攤開的細節:逐日帳相加是 NT$15.55 加 Day 5 的估計值,**但有約 21 小時的閒置公用 IP(約 NT$3.4)沒有進任何一天的帳**——叢集存在 25.6 小時,只有 4.6 小時在「當天」的帳期內。[Day 1 的地雷 4](sprint3-day1-three-generations.md#mine-4) 說「停機不等於零成本」,這筆漏帳就是它的實例:**按小時滴答的成本,會從按天記的帳本縫隙裡漏掉**。叢集一天不刪,公用 IP 就每小時繼續累計 NT$0.16。

## 誠實的差距

- 決策表 44 格裡,帶事實宣稱的 39 格中**實測 19、查證 15、推論 2、缺口 3**——理想上每一格都該是實測,但治理階段與 commit 節奏這類事實在叢集上量不出來,所以守的線是「每格標明類型、三者不混」。
- 組表的紀律:**宣稱範圍不得大於證據範圍**——例如「三條路線都不開箱可用」就是超出證據的寫法,wasmCloud 實測是可用的。
- wasmCloud 的「主動解除安裝乾淨」始終是推論,直到有人真的拆一次。
- 這張表的半衰期由第三題決定:**健康度那幾欄過一季就該重測**,方法在 [Day 0 的檢查清單](sprint3-day0-wasm-concepts.md#health-method)。

## 驗收 checkpoint

| 驗證 | 判準 |
|---|---|
| 每一格可追溯 | 44 格,0 格無出處;39 格帶事實宣稱者全部標明實測/查證/推論 |
| 全 sprint 紀錄的一致性 | 44 格出處逐筆與各日章節核對,數字與出處吻合 |
| 總帳可重算 | 成本、時間、地雷數皆從各日章節逐筆相加,含一筆帳期縫隙的說明 |

## 帶得走的東西

- **決策表的價值不在結論,在出處欄。** 結論半年就過期;「每一格是怎麼知道的」讓你自己能重新量一次。
- **實測、查證、推論分開標,是對讀者最基本的誠實。** 三種證據的保存期限不同:實測綁環境、查證綁日期、推論綁前提。
- **帳要對齊成本的節奏,不是你的作息。** 按小時計費的資源配上按天記的帳,縫隙裡的錢不會自動出現在帳上。
- **一個 sprint 的驗收被改寫兩次,不是規劃失敗。** 兩次都是「原訂要驗的東西在當前版本上不存在」——發現這件事本身就是實測的產出,硬湊一個通過才是失敗。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[Kubernetes RuntimeClass 文件](https://kubernetes.io/docs/concepts/containers/runtime-class/)** —— 整個 sprint 的機制原點。走完整個 sprint 再回來讀它,`handler`、`scheduling`、`overhead` 三個欄位各自對應到哪一天的哪顆雷,會自己浮出來。
- **[containerd 的 CRI 設定文件](https://github.com/containerd/containerd/blob/main/docs/cri/config.md)** —— 節點側的一手規格。plugin 路徑、`use_local_image_pull`、runtime 區塊,三條雷的正解都在裡面。
- **[WASI v0.3.0 release](https://github.com/WebAssembly/WASI/releases/tag/v0.3.0)** —— 標準層現況的起點。決策表的健康度欄過期之後,重驗就從標準層開始。

## 下一步

Sprint 3 到此完結:三條路線、41 顆地雷、兩次驗收改寫。Day 0 的那句主題,現在可以補完:

**Kubernetes 從來沒有規定工作負載必須是 Linux 容器——但沒有規定,不代表生態已經準備好讓你反悔。**

下一個 sprint 的方向在[課程總覽](../index.md)。

---

!!! quote ""
    WebAssembly 標誌為 WebAssembly 專案之官方資產(CC0 1.0),此處作社群教學用途。
