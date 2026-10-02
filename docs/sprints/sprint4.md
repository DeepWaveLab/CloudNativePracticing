# Sprint 4 · 服務網格與機密運算

這個 sprint 處理應用層的兩類安全需求：服務之間的通訊安全，以及工作負載記憶體的保護。**Part 1 服務網格（Day 0–4）**：用 Envoy Gateway 處理叢集入口流量並開啟 HTTP/3，再比較 Istio ambient 的 mTLS、工作負載身分與斷路，以及 Cilium 的 WireGuard 加密與 L7 政策。**Part 2 機密運算（Day 5–9）**：用 Kata 隔離 pod，在 AMD SEV-SNP 硬體上執行 kata-cc，並用遠端證明讓金鑰只發給通過驗證的 pod。

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

    不需要安裝。分清楚 CNI、east-west mesh、north-south gateway 三層各負責什麼，說明 sidecar 模式為什麼逐漸被取代，以及 eBPF 處理 L3/L4 之後 mesh 還負責哪些事。

-   ![Envoy](../assets/logos/envoy-icon-color.svg){ width="44" }

    **Day 1 · [Envoy Gateway 與 HTTP/3](../runbook/sprint4-day1-envoy-gateway.md)**

    ---

    建立 BYOCNI 叢集並安裝 Cilium 與 Envoy Gateway，用 Gateway API 的 GatewayClass、Gateway、HTTPRoute 設定入口路由，再開啟 HTTP/3，並在瀏覽器 DevTools 確認連線協定是 h3。

-   ![Istio](../assets/logos/istio-icon-color.svg){ width="44" }

    **Day 2 · [Istio ambient：服務間 mTLS 與斷路](../runbook/sprint4-day2-istio-ambient.md)**

    ---

    用不需要 sidecar 的 Istio ambient 模式，讓服務間流量經 ztunnel 自動加上 mTLS 與 SPIFFE 身分；再在 waypoint 設定斷路，觀察超過門檻的請求被拒絕。

-   ![Cilium](../assets/logos/cilium-icon-color.svg){ width="44" }

    **Day 3 · [Cilium mesh：WireGuard 加密與 L7](../runbook/sprint4-day3-cilium-mesh.md)**

    ---

    移除 Istio，改用 Cilium 的 mesh：用 WireGuard 做傳輸加密並抓包確認，套用一條 L7 policy，並分清楚傳輸加密與工作負載身分的差別，後者在 Cilium 仍是 beta。

-   ![Istio](../assets/logos/istio-icon-color.svg){ width="36" }
    ![Cilium](../assets/logos/cilium-icon-color.svg){ width="36" }

    **Day 4 · [Istio 與 Cilium 比較](../runbook/sprint4-day4-istio-vs-cilium.md)**

    ---

    在同一座叢集、同一個拓樸下比較 Istio 與 Cilium 的加密與身分、L7 能力、資源用量、延遲與對節點的改動，整理成決策表：Cilium 的強項在 L4，Istio 的強項在 L7 與身分。

</div>

## Part 2 · 機密運算（Day 5–9）

<div class="grid cards" markdown>

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 5 · [機密運算的威脅模型](../runbook/sprint4-day5-confidential-concepts.md)**

    ---

    不需要安裝。說明 Kata 沙箱保護主機不受工作負載影響，機密運算則保護工作負載的記憶體不被主機讀取，兩者方向相反；並介紹 Kata 與 Confidential Containers（CoCo）的關係。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 6 · [Kata Pod Sandboxing](../runbook/sprint4-day6-kata-sandboxing.md)**

    ---

    在 Part 1 的叢集加入 Kata 節點池，讓 pod 跑在有獨立 kernel 的輕量 VM 裡；找出 RuntimeClass handler 對應的節點設定，並確認 Kata 節點在自行安裝的 Cilium 下網路正常。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 7 · [kata-cc：SEV-SNP 機密硬體](../runbook/sprint4-day7-katacc.md)**

    ---

    把 handler 換成 `kata-cc`、節點換成 AMD SEV-SNP 機型，讓 pod 跑在記憶體由 CPU 硬體加密的 VM 裡；也說明怎麼查詢有供貨的區域、kata-cc 節點池的角色限制，以及 pod 為什麼要附安全政策才能啟動。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 8 · [遠端證明：MAA、SKR 與金鑰釋放](../runbook/sprint4-day8-attestation.md)**

    ---

    串接 MAA 遠端證明、SKR sidecar 與 Key Vault Premium，把金鑰的釋放條件綁在 pod 的 measurement 上：符合政策的 pod 拿得到金鑰，改了一個環境變數的 pod 會被拒絕。

-   ![Kubernetes](../assets/logos/kubernetes-icon-color.svg){ width="44" }

    **Day 9 · [總結與決策表](../runbook/sprint4-day9-decision-matrix.md)**

    ---

    不動手。說明移除機密節點池後叢集留下什麼，用決策表比較 AKS kata-cc 與上游 CoCo，並對照兩個 Part 對「主機是否該看得到工作負載」的不同立場。

</div>

## 兩個 Part 的關係

Part 1 的各種加密，金鑰都存在節點的記憶體裡，能以 root 登入節點的人就讀得到明文。Part 2 處理節點本身不可信的情況，把記憶體加密交給 CPU 硬體，金鑰由遠端證明的結果決定能不能釋放。兩個 Part 合起來，說明基礎設施能替應用承擔哪些安全責任，以及哪些仍要由應用自己處理。

從 [Day 0](../runbook/sprint4-day0-mesh-concepts.md) 開始。

---

!!! quote ""
    Envoy、Istio、Cilium、Kubernetes 標誌為 CNCF（Linux Foundation）官方資產，此處皆作社群教學用途。
