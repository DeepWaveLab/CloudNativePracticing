# Sprint 2 · eBPF 與執行期安全

eBPF 讓程式可以安全地在 Linux 核心裡執行，許多觀測、安全與網路工具都建立在它上面。這個 sprint 從 eBPF 的基本概念開始，在 AKS 上依序操作 **bpftrace**、**Falco**、**Tetragon**、**Cilium 與 Hubble**，學會每一套看得到什麼、看不到什麼，最後一章整理四套工具的分工。

<div style="text-align: center;" markdown>

[![eBPF](../assets/logos/ebpf-logo.svg#only-light){ width="150" }](https://ebpf.io/)
[![eBPF](../assets/logos/ebpf-logo-dark.svg#only-dark){ width="150" }](https://ebpf.io/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Falco](../assets/logos/falco-icon-color.svg){ width="72" }](https://falco.org/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Tetragon](../assets/logos/tetragon-icon-color.svg){ width="72" }](https://tetragon.io/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Cilium](../assets/logos/cilium-icon-color.svg){ width="72" }](https://cilium.io/)

*bpftrace 用來撰寫追蹤腳本，Falco 用規則偵測異常行為，Tetragon 在核心裡過濾與攔截事件，Cilium 取代網路資料平面，Hubble 觀察流量。*

</div>

Day 0 從零講起，不需要先接觸過 eBPF，也不需要 Sprint 1 的內容。Day 0 到 Day 6 在同一座叢集上進行，Day 7 起換成 Cilium 時要另建一座不帶 CNI 的 AKS 叢集（BYOCNI）。

## 課程路線（Day 0–10）

<div class="grid cards" markdown>

-   ![eBPF](../assets/logos/ebpf-logo.svg#only-light){ width="60" }
    ![eBPF](../assets/logos/ebpf-logo-dark.svg#only-dark){ width="60" }

    **Day 0 · [eBPF 是什麼](../runbook/sprint2-day0-ebpf-concepts.md)**

    ---

    說明 eBPF 程式怎麼載入核心、能掛在哪些位置、verifier 怎麼檢查程式是否安全，以及同一支程式為什麼能在不同版本的 kernel 上執行；最後部署一顆特權 pod，用一行 bpftrace 追蹤另一顆 pod 的程式執行。

-   ![bpftrace](../assets/logos/bpftrace-logo.svg#only-light){ width="72" }
    ![bpftrace](../assets/logos/bpftrace-logo-dark.svg#only-dark){ width="72" }

    **Day 1 · [bpftrace 三支經典工具](../runbook/sprint2-day1-bpftrace-basics.md)**

    ---

    用 `execsnoop`、`opensnoop`、`tcpconnect` 觀察行程執行、檔案開啟與對外連線，了解各自的限制；再自己寫一支 `.bt` 腳本，找出寫入特定目錄的行程。

-   **Day 2 · [把核心事件接回 Kubernetes](../runbook/sprint2-day2-bpftrace-kubernetes.md)**

    ---

    把 bpftrace 輸出的 cgroup id 對應回 pod 名稱，過程不需要呼叫 API server；再用這個對應寫出只追蹤單一 pod 的過濾器，替一顆 nginx 建立啟動行為基線。

-   ![Falco](../assets/logos/falco-icon-color.svg){ width="44" }

    **Day 3 · [Falco 安裝與預設規則](../runbook/sprint2-day3-falco-basics.md)**

    ---

    安裝 Falco，拆解一條預設規則從 `condition` 到 syscall 欄位的結構，觸發告警並找出告警裡用來辨識 pod 的欄位。

-   ![Falco](../assets/logos/falco-icon-color.svg){ width="44" }

    **Day 4 · [自訂規則、誤報調校與告警路由](../runbook/sprint2-day4-falco-custom-rules.md)**

    ---

    用 `list`、`macro`、`rule` 撰寫自訂規則，補上預設規則沒涵蓋的情況；用 `exceptions` 調校誤報並了解調校會犧牲哪些偵測能力，最後用 Falcosidekick 把告警送出節點。

-   ![Tetragon](../assets/logos/tetragon-icon-color.svg){ width="44" }

    **Day 5 · [Tetragon 與 TracingPolicy](../runbook/sprint2-day5-tetragon-basics.md)**

    ---

    安裝 Tetragon 並撰寫 TracingPolicy，了解在核心裡過濾事件與 Falco 在使用者空間比對規則的差別，再讓兩套工具同時觀察同一組可疑操作。

-   ![Tetragon](../assets/logos/tetragon-icon-color.svg){ width="44" }

    **Day 6 · [從偵測到攔截](../runbook/sprint2-day6-tetragon-enforcement.md)**

    ---

    用 Tetragon 對違規行程送出 SIGKILL，同時確認正常工作負載不受影響；並說明 SIGKILL 是在操作之前擋下還是事後終止，以及規則寫錯時應用程式會看到什麼。

-   ![Cilium](../assets/logos/cilium-icon-color.svg){ width="44" }

    **Day 7 · [Cilium 與 kube-proxy replacement](../runbook/sprint2-day7-cilium-kubeproxy.md)**

    ---

    建立 BYOCNI 叢集，自行安裝 Cilium 並開啟 kube-proxy replacement。動手前先了解 kube-proxy 負責哪些工作，完成後確認叢集裡沒有 kube-proxy、Service 仍然可用。

-   ![Cilium](../assets/logos/cilium-icon-color.svg){ width="44" }

    **Day 8 · [CiliumNetworkPolicy（L3/L4 → L7）](../runbook/sprint2-day8-cilium-network-policy.md)**

    ---

    撰寫 CiliumNetworkPolicy，從命名空間隔離、埠限制、FQDN 做到 L7 的 HTTP 方法控制，例如同一個服務允許 GET、拒絕 POST；也說明多條政策疊加時的生效規則。

-   ![Cilium](../assets/logos/cilium-icon-color.svg){ width="44" }

    **Day 9 · [Hubble 可觀測性](../runbook/sprint2-day9-hubble.md)**

    ---

    開啟 Hubble 的 CLI 與 UI，找出 Day 8 被 L4 丟棄與被 L7 拒絕的流量，並了解 Hubble 觀察不到的範圍。

-   **Day 10 · [綜合：四套工具的分工](../runbook/sprint2-day10-decision-matrix.md)**

    ---

    不動手。整理四套工具的掛載位置、資源用量與失效模式，說明每套工具的觀測盲點來自它掛在核心的哪個位置，以及各自適合的用途。

</div>
