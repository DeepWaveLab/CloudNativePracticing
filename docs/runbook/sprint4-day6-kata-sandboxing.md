# Day 6: Kata Pod Sandboxing——RuntimeClass、獨立 kernel 與自管 Cilium

![Kubernetes 官方標誌](../assets/logos/kubernetes-icon-color.svg){ align=right width="80" }

> [Day 5](sprint4-day5-confidential-concepts.md) 把「防的是誰」講清楚了。今天動手做最基本的一層:把一個 pod 跑進**獨立 kernel 的輕量 VM**(Kata Pod Sandboxing),證明它不共用主機 kernel。這一天先不碰硬體記憶體加密——但它會讓你發現,整套機制其實就是 Sprint 3 那條 RuntimeClass → handler → containerd shim 的鏈,只是換一個 handler。順便,把 Kata 節點疊在 Part 1 的自管 Cilium 上會不會出事,今天當場驗。

!!! abstract "你在課程的哪裡"
    - **[Day 5](sprint4-day5-confidential-concepts.md)**:機密運算防誰、Kata↔CoCo 治理、eBPF 讓主機看清楚 guest 而 CoCo 讓主機看不到。
    - **今天**:在 Part 1 那座 BYOCNI 上游 Cilium 叢集上加一個 Kata 節點池,跑一個 `kata-vm-isolation` 的 pod,驗它有獨立 kernel、指出 handler 對應到節點設定哪一段,並確認 Kata 節點在自管 Cilium 下網路正常。
    - **接下來**:[Day 7](sprint4-day7-katacc.md) 同一條鏈換成 `kata-cc`,多一層 SEV-SNP 硬體記憶體加密。

## 這其實是 Sprint 3 的七層鏈

Sprint 3 Day 1 建立過一條鏈:pod 的 `runtimeClassName` → RuntimeClass 的 handler → 節點 containerd 設定裡對應的 runtime 區塊 → 那個區塊指向的 shim 二進位檔。WASM 是換一個 shim,**Kata 也是換一個 shim**——沒有新的 Kubernetes 概念要學,學的是那個 shim 裡裝的東西不一樣(一台輕量 VM,不是一個容器)。

AKS 的 Pod Sandboxing 用 Kata Containers 加 Microsoft Hypervisor 與 Cloud Hypervisor,官方文件的一句話就是入口:**設了 `runtimeClassName: kata-vm-isolation` 的 pod,hypervisor 會替它開一台有自己 kernel 的輕量 VM**。

有一個前置條件要先知道,否則第一步就白花錢:**Kata 節點池只支援 Azure Linux**(見[地雷 1](#mine-1))。

今天走三步:

| 步驟 | 做什麼 |
|---|---|
| 1 | 加一個 Kata(AzureLinux)節點池,並確認自管 Cilium 接得住它 |
| 2 | 跑一個 kata pod 加一個一般 pod,比 kernel、驗網路 |
| 3 | 指出 handler 對應到節點 containerd 設定的哪一段 |

## 步驟 1: 加 Kata 節點池,並看 Cilium 接不接得住

在 Part 1 那座 BYOCNI 上游 Cilium 叢集上加節點池,`--os-sku AzureLinux` 是硬需求、`--workload-runtime KataVmIsolation` 開 Kata、VM 要 gen2 且支援巢狀虛擬化(D4s_v3 合格):

```bash
az aks nodepool add -g <resource-group> --cluster-name <cluster> \
  --name katapool --mode User \
  --os-sku AzureLinux --workload-runtime KataVmIsolation \
  --node-vm-size Standard_D4s_v3 --node-count 1
```

Part 1 的資料平面是**自管的上游 Cilium**(不是受管的 Azure CNI)。加一個 AzureLinux 的 Kata 節點,Cilium 接不接得住?當場看:

```console
$ kubectl get nodes -o wide
aks-katapool-…-vmss000000    Ready   …   Microsoft Azure Linux 3.0   6.6.137.mshv2-1.azl3   containerd://2.2.4
aks-nodepool1-…-vmss000006   Ready   …   Ubuntu 24.04.4 LTS          6.8.0-1063-azure       containerd://2.3.3-2

$ kubectl -n kube-system get ds cilium
desired=3  ready=3            ← Cilium DaemonSet 涵蓋了新的 Kata 節點

$ kubectl get node <kata-node> -o jsonpath='{…runtimeHandlers}'
kata kata-cc runc untrusted   ← 注意這一行,連 kata-cc 都預先在了
```

**Kata 節點 81 秒內 `Ready`、拿到內部 IP、Cilium 三顆都就緒。** 自管上游 Cilium 接住了 AzureLinux Kata 節點——這件事微軟沒有明文背書,值得自己驗一次。而 `runtimeHandlers` 那行有個伏筆:`kata-cc` 這個 handler 在一個**非機密**的 D4s_v3 節點上就已經存在了,[地雷 2](#mine-2) 會回來講。

## 步驟 2: 比 kernel,驗網路

放兩個 pod:一個帶 `runtimeClassName: kata-vm-isolation`(kata),一個一般 pod(釘在 Ubuntu 一般池上當對照)。最直接的證據是 `uname`:

```console
$ kubectl -n kata-lab exec isolated-pod -- uname -r
6.6.137.mshv1-1.azl3          ← Microsoft Hypervisor 的 guest kernel

$ kubectl -n kata-lab exec normal-pod -- uname -r
6.8.0-1063-azure              ← Ubuntu 主機 kernel
```

**兩個不同的 kernel。** kata pod 跑在自己的輕量 VM 裡,那台 VM 有它自己的核心(`.mshv1`),跟主機的 `azure` 核心是兩回事。

!!! note "別拿「行程數」當隔離證據"
    直覺會想「kata pod 看不到主機行程,所以隔離」。但一般容器本來就有 PID namespace,兩邊 `ps` 都只看得到自己的幾個行程——**行程數分辨不出 Kata**。真正的證據是 kernel:一般容器共用主機 kernel,kata pod 有自己的。用 `uname -r`。

Part 1 的 Cilium 有沒有好好服務這個 kata pod?kata pod 拿到 pod IP,跨節點打一般池的 pod、查 DNS、打 API server:

```console
# kata pod (10.0.2.202) → normal-pod (10.0.1.103),跨節點跨池
2 packets transmitted, 2 received, 0% packet loss, rtt avg 3.016 ms

# DNS
$ nslookup kubernetes.default.svc.cluster.local  →  10.0.0.1
# Service(API server)
$ curl -sk https://kubernetes.default/healthz     →  http_code=401
```

Ping 通、DNS 通、Service 的 TLS 握手完成(401 = 沒帶憑證,但路徑是通的)。**自管 Cilium 給 Kata 節點與 kata pod 的網路是正常的**——Part 1 疊 Part 2 的第一半(Kata 這一半)成立。

## 步驟 3: handler 對應到節點的哪一段

Kata 節點掛一顆特權 pod 讀它的 containerd 設定,就看得到 handler 落在哪:

```console
$ grep -nE "kata|runtime_type" /host/etc/containerd/config.toml
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata]
  runtime_type = "io.containerd.kata.v2"
  [.kata.options] ConfigPath = "/usr/share/defaults/kata-containers/configuration.toml"

[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-cc]
  snapshotter = "tardev"
  runtime_type = "io.containerd.kata-cc.v2"
  [.kata-cc.options] ConfigPath = "…/configuration-clh-snp.toml"
```

RuntimeClass `kata-vm-isolation`(handler `kata`)→ 設定裡的 `runtimes.kata` 區塊 → `io.containerd.kata.v2` shim。**這就是 Sprint 3 的七層鏈,只換了 handler 名字。** 順帶看到 `runtimes.kata-cc` 也在(指向一份 `clh-snp` 的設定)——那是 Day 7 的東西,但它在這顆非機密節點上就備好了。

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| pod 有獨立 kernel | kata pod 與一般 pod 的 `uname -r` 不同 | kata `6.6.137.mshv1` vs 一般 `6.8.0-azure` |
| handler 對得到設定 | 指出 handler 對應節點 containerd 的哪一段 | `runtimes.kata` → `io.containerd.kata.v2` |
| 自管 Cilium 接得住 Kata 節點 | 節點 Ready、Cilium 3/3、kata pod 有 IP 且跨池連通 | 全成立(ping 2/2、DNS、Service 401) |

## 地雷記錄

### 地雷 1:Kata 節點只支援 AzureLinux,一般節點卻是 Ubuntu {#mine-1}

**根因**:AKS Pod Sandboxing 的一手文件明載「**Only the Azure Linux os-sku supports this feature**」。加節點池時漏了 `--os-sku AzureLinux`、沿用叢集預設的 Ubuntu,指令會失敗。

**後果**:同一座叢集因此**混兩種 OS**——一般節點池是 Ubuntu,Kata 節點池被迫是 AzureLinux。而這不只是換個發行版:兩者的 containerd 版本與設定路徑都不同(本課實測 Kata 節點是 containerd 2.2.4、走 `io.containerd.grpc.v1.cri` 這條 1.x 風格的 plugin 路徑;一般 Ubuntu 節點是 2.3.3、走 Sprint 3 量到的 2.x 路徑)。做節點層的排錯或設定比對時,要先確認自己在看哪一種節點。

**判斷準則**:Kata / kata-cc 節點池一律加 `--os-sku AzureLinux`;跨節點做 containerd 設定比對前,先分清 OS。

### 地雷 2:`kata-cc` handler 在非機密節點上就存在,但沒有 SEV-SNP 硬體 {#mine-2}

**症狀**:一個普通的 D4s_v3(`KataVmIsolation`)Kata 節點,`runtimeHandlers` 就列出 `kata-cc`,`config.toml` 也有 `runtimes.kata-cc` 區塊(指向 `configuration-clh-snp.toml`)。看起來好像不用 Day 7 就能跑機密容器。

**根因**:AzureLinux 的 Kata 節點映像**預先放好了** kata-cc 的 runtime 設定,但真的跑 kata-cc 需要 **SEV-SNP 機密運算硬體**(`_cc_v5` 系列 VM),D4s_v3 沒有那個硬體。

**判斷準則**:**「handler 在」不等於「硬體在」。** Day 7 跟今天的差別不是設定,是 SKU 與硬體——指一個 `kata-cc-isolation` 的 pod 到今天這種節點上,起不來。

## 帶得走的東西

- **Kata 沒有新的 Kubernetes 概念,只有新的 shim。** RuntimeClass → handler → containerd 區塊 → shim,這條 Sprint 3 建立的鏈原封不動,換的是 shim 裡裝一台輕量 VM。
- **獨立 kernel 是 Kata 的證據,行程數不是。** 一般容器也有 PID namespace;用 `uname -r` 看兩邊 kernel 版本不同,才證明得了「不共用主機 kernel」。
- **自管的資料平面接不接得住新型節點,要當場驗。** BYOCNI 上游 Cilium 接住 AzureLinux Kata 節點這件事沒有官方背書,而它成立——省下退回受管 CNI 的麻煩。
- **節點映像會預埋你今天用不到的能力。** kata-cc 的設定在非機密節點上就備好了,但沒有硬體就是跑不了。設定就緒是必要條件,不是充分條件。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[AKS Pod Sandboxing 文件](https://learn.microsoft.com/en-us/azure/aks/use-pod-sandboxing)** —— 今天的一手配方:`--workload-runtime KataVmIsolation`、`--os-sku AzureLinux` 的硬需求、`runtimeClassName: kata-vm-isolation`,以及用 `uname -r` 驗 kernel 的官方做法。
- **[Kata Containers 官網](https://katacontainers.io/)** —— shim 裡那台輕量 VM 的來源;它跟 gVisor、跟一般 runc 的定位差別。
- **[Sprint 3 Day 1:RuntimeClass 七層鏈](sprint3-day1-three-generations.md)** —— 站內連結。今天這條鏈就是它,換一個 handler;兩天並讀會發現機制完全相同。

## 下一步

今天證明了「不共用主機 kernel」,但主機**還是讀得到** kata pod 的記憶體明文——它只是一台一般的 VM。[Day 7](sprint4-day7-katacc.md) 把 handler 換成 `kata-cc`,節點換成 SEV-SNP 的 `_cc_v5` 硬體,讓那台 VM 的記憶體**連主機都解不開**。而換硬體這件事,會在 Day 7 撞上一連串「這個區沒賣、這個組合起不來」的現實。

---

!!! quote ""
    Kubernetes 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
