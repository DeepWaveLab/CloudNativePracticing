# Sprint 4 · 服務網格與機密運算

外部使用者已經能連到服務，接著你要保護服務間的連線；若連節點管理者都不應看到工作負載的資料，光加密網路又不夠。前半段用 Envoy Gateway、Istio ambient 與 Cilium 比較入口、服務間身分與網路政策；後半段從 Kata 的 VM 隔離走到機密運算，說明遠端證明與金鑰釋放各自解決哪個問題。

<div style="text-align: center;" markdown>

[![Envoy](../assets/logos/envoy-icon-color.svg){ width="72" }](https://gateway.envoyproxy.io/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Istio](../assets/logos/istio-icon-color.svg){ width="76" }](https://istio.io/latest/docs/ambient/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Cilium](../assets/logos/cilium-icon-color.svg){ width="76" }](https://cilium.io/)

*Part 1 的三套工具：Envoy Gateway 處理 north-south 入口流量，Istio ambient 與 Cilium 是兩種 east-west mesh。*

</div>

Part 1 在一座自行安裝 Cilium 當 CNI 的 AKS 叢集（BYOCNI）上進行，會用到 Sprint 2 介紹過的 Cilium。Part 2 需要支援 AMD SEV-SNP 的機密運算機型（Azure 的 `_cc_v5` 系列），不是每個區域都有：Day 6 沿用 Part 1 的叢集，Day 7 起要在有供貨的區域另建一座叢集，章節會說明怎麼查詢。

## Part 1 · 服務網格（Day 0–4）

<div class="grid cards" markdown>

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 0 · [服務網格與入口閘道的三層地圖](../runbook/sprint4-day0-mesh-concepts.md)**

    ---

    不需要安裝。分清楚 Pod 網路、內部服務通訊與外部入口的責任，再看 sidecar、ztunnel、waypoint 與 eBPF 各在哪裡處理流量。

-   ![Envoy](../assets/logos/envoy-icon-color.svg){ width="44" }

    **Day 1 · [Envoy Gateway 與 HTTP/3](../runbook/sprint4-day1-envoy-gateway.md)**

    ---

    以 BYOCNI 叢集與已就緒的入口控制器為前提，使用 Cilium 設定與 Gateway API 的 GatewayClass、Gateway、HTTPRoute 建立路由，再開啟 HTTP/3，並在瀏覽器 DevTools 確認連線協定是 h3。

-   ![Istio](../assets/logos/istio-icon-color.svg){ width="44" }

    **Day 2 · [Istio ambient：服務間 mTLS 與連線限制](../runbook/sprint4-day2-istio-ambient.md)**

    ---

    用不需要 sidecar 的 Istio ambient 模式，讓服務間流量經 ztunnel 自動加上 mTLS 與 SPIFFE 身分；再以 waypoint 的連線池限制觀察拒絕訊號，區分 503 與實際過載證據。

-   ![Cilium](../assets/logos/cilium-icon-color.svg){ width="44" }

    **Day 3 · [Cilium mesh：WireGuard 加密與 L7](../runbook/sprint4-day3-cilium-mesh.md)**

    ---

    移除 Istio，改用 Cilium 的 mesh：用 WireGuard 做傳輸加密並抓包確認，套用一條 L7 policy，並分清楚傳輸加密與工作負載身分的差別，本課的 SPIRE 身分後端未就緒，尚未完成相互驗證。

-   ![Istio](../assets/logos/istio-icon-color.svg){ width="36" }
    ![Cilium](../assets/logos/cilium-icon-color.svg){ width="36" }

    **Day 4 · [Istio 與 Cilium 比較](../runbook/sprint4-day4-istio-vs-cilium.md)**

    ---

    分別比較已驗證的加密、工作負載身分與 HTTP 政策，再解讀資源及延遲數據。兩組測試的功能範圍不同，數字不能直接當成產品排名。

</div>

## Part 2 · 機密運算（Day 5–9）

<div class="grid cards" markdown>

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 5 · [機密運算的威脅模型](../runbook/sprint4-day5-confidential-concepts.md)**

    ---

    不需要安裝。說明一般 Kata 如何增加 VM 隔離邊界，機密容器又如何保護不受信任宿主環境中的私有記憶體；兩者的威脅模型不同；並介紹 Kata 與 Confidential Containers（CoCo）的關係。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 6 · [Kata Pod Sandboxing](../runbook/sprint4-day6-kata-sandboxing.md)**

    ---

    在 Part 1 的叢集加入 Kata 節點池，讓 pod 跑在有獨立 kernel 的輕量 VM 裡；找出 RuntimeClass handler 對應的節點設定，並確認 Kata 節點在自行安裝的 Cilium 下網路正常。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 7 · [kata-cc：SEV-SNP 機密硬體](../runbook/sprint4-day7-katacc.md)**

    ---

    使用符合 kata-cc 要求的機型、runtime 與安全政策啟動 Pod。分別核對區域、版本與節點池設定，再區分平台資訊與遠端證明。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 8 · [遠端證明：MAA、SKR 與金鑰釋放](../runbook/sprint4-day8-attestation.md)**

    ---

    串接 MAA 遠端證明、SKR sidecar 與 Key Vault Premium，把金鑰釋出條件綁定安全政策雜湊；改環境變數並重新產生政策後，保持原金鑰條件，觀察新版本遭到拒絕。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 9 · [需求與驗證範圍](../runbook/sprint4-day9-decision-matrix.md)**

    ---

    回顧移除機密節點池後的狀態，依需求比較 AKS kata-cc 與上游 CoCo，並區分網路觀測、VM 隔離與資料機密性的驗證範圍。

</div>

## 兩個 Part 的關係

Part 1 保護傳輸過程，應用處理資料時仍需要明文。Part 2 將宿主環境視為不受信任，結合硬體保護、安全政策與遠端證明，限制秘密可交付的執行環境。兩個 Part 合起來，說明基礎設施能替應用承擔哪些安全責任，以及哪些仍要由應用自己處理。

從 [Day 0](../runbook/sprint4-day0-mesh-concepts.md) 開始。

---

!!! quote ""
    Envoy、Istio、Cilium、Kubernetes 標誌為 CNCF（Linux Foundation）官方資產，此處皆作社群教學用途。
