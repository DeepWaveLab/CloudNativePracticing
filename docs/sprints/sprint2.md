# Sprint 2 · eBPF 與執行期安全

同一批核心事件,四種取用方式。從自己掛探針追 syscall 開始,一路走到把 Kubernetes 的 Service 實作整個換掉——這門課在 AKS 上把 **bpftrace**、**Falco**、**Tetragon**、**Cilium 與 Hubble** 各實測一輪,寫成可照抄的 runbook。每一章的指令與輸出都來自真實跑過的驗證紀錄,途中踩到的 **76 顆雷**以具名地雷收錄在各章。

<div style="text-align: center;" markdown>

[![eBPF](../assets/logos/ebpf-logo.svg#only-light){ width="150" }](https://ebpf.io/)
[![eBPF](../assets/logos/ebpf-logo-dark.svg#only-dark){ width="150" }](https://ebpf.io/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Falco](../assets/logos/falco-icon-color.svg){ width="72" }](https://falco.org/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Tetragon](../assets/logos/tetragon-icon-color.svg){ width="72" }](https://tetragon.io/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Cilium](../assets/logos/cilium-icon-color.svg){ width="72" }](https://cilium.io/)

*四位主角:bpftrace(自己動手看)、Falco(規則引擎替你判斷)、Tetragon(在核心裡攔下來)、Cilium 與 Hubble(換掉網路資料平面並看清楚流量)。*

</div>

**Day 0 從零講起,不預設你碰過 eBPF。** 而這個 sprint 有一個貫穿全程的問法:同一個動作,兩套工具同時開著跑——最值得看的結論,往往出現在**它們答案一樣錯**的那一格。

## 十一天的路線(Day 0–10)

<div class="grid cards" markdown>

-   ![eBPF](../assets/logos/ebpf-logo.svg#only-light){ width="60" }
    ![eBPF](../assets/logos/ebpf-logo-dark.svg#only-dark){ width="60" }

    **Day 0 · [eBPF 是什麼](../runbook/sprint2-day0-ebpf-concepts.md)**

    ---

    核心多長出的那套受控擴充介面:程式怎麼進去、掛得到哪些位置、verifier 憑什麼拒絕,以及為什麼同一支程式換一顆 kernel 還能跑。

-   ![bpftrace](../assets/logos/bpftrace-logo.svg#only-light){ width="72" }
    ![bpftrace](../assets/logos/bpftrace-logo-dark.svg#only-dark){ width="72" }

    **Day 1 · [bpftrace 三支經典工具](../runbook/sprint2-day1-bpftrace-basics.md)**

    ---

    `execsnoop`、`opensnoop`、`tcpconnect` 各自的邊界,加一支自己寫的追蹤腳本;共同盲點是沒有 pod 身分。

-   **Day 2 · [把核心事件接回 Kubernetes](../runbook/sprint2-day2-bpftrace-kubernetes.md)**

    ---

    cgroup id 換算回 pod 名字的五步鏈(整條不碰 API server),只追一顆 pod 的過濾器,以及一份手工的行為基線。

-   ![Falco](../assets/logos/falco-icon-color.svg){ width="44" }

    **Day 3 · [Falco 安裝與出廠規則](../runbook/sprint2-day3-falco-basics.md)**

    ---

    出廠只有 25 條規則,而閒置八分半是零告警。完整解剖一條規則的 condition 怎麼收斂到 syscall 欄位。

-   ![Falco](../assets/logos/falco-icon-color.svg){ width="44" }

    **Day 4 · [自訂規則、誤報調校與告警路由](../runbook/sprint2-day4-falco-custom-rules.md)**

    ---

    補上 Day 3 找到的兩個洞,把誤報從每分鐘 180 筆調到 0——然後把交出去的偵測力量給你看。

-   ![Tetragon](../assets/logos/tetragon-icon-color.svg){ width="44" }

    **Day 5 · [Tetragon 與 TracingPolicy](../runbook/sprint2-day5-tetragon-basics.md)**

    ---

    核心層過濾是真的:3000 次不符合的操作零筆出核心。但兩套工具在 `nsenter` 這題錯得一模一樣。

-   ![Tetragon](../assets/logos/tetragon-icon-color.svg){ width="44" }

    **Day 6 · [從偵測到攔截](../runbook/sprint2-day6-tetragon-enforcement.md)**

    ---

    SIGKILL 到底是擋住了操作,還是只是事後殺掉行程?量給你看。以及寫錯一條攔截規則,從應用側看是什麼樣子。

-   ![Cilium](../assets/logos/cilium-icon-color.svg){ width="44" }

    **Day 7 · [Cilium 與 kube-proxy replacement](../runbook/sprint2-day7-cilium-kubeproxy.md)**

    ---

    一座沒有 CNI 的叢集長什麼樣,以及動手換掉之前先問清楚:那個要被換掉的元件,現在到底還在做什麼?

-   ![Cilium](../assets/logos/cilium-icon-color.svg){ width="44" }

    **Day 8 · [CiliumNetworkPolicy(L3/L4 → L7)](../runbook/sprint2-day8-cilium-network-policy.md)**

    ---

    從命名空間隔離寫到 HTTP 方法:同一顆 pod、同一個服務,GET 通過而 POST 被擋。以及網路政策為什麼只會放寬不會收緊。

-   ![Cilium](../assets/logos/cilium-icon-color.svg){ width="44" }

    **Day 9 · [Hubble 可觀測性](../runbook/sprint2-day9-hubble.md)**

    ---

    被擋下的流量看得見嗎?三個探針各問一件事,而其中一個答案是:它看不見這座叢集最大的政策破口。

-   **Day 10 · [綜合:四套工具的分工](../runbook/sprint2-day10-decision-matrix.md)**

    ---

    整個 sprint 的數字收斂成一張分工表,每一格都追溯得到某一天的量測;以及那句貫穿全程的結論——**盲點就是執行點**。

</div>
