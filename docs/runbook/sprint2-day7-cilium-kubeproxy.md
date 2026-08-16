# Day 7: 換掉資料平面——BYOCNI 叢集、Cilium,與一個裝了但沒在用的 kube-proxy

![Cilium 官方標誌](../assets/logos/cilium-icon-color.svg){ align=right width="95" }

> 前六天都在觀察行程與檔案:誰執行了什麼、誰開了哪個檔。今天換一個角度,把 eBPF 放進網路路徑本身——不是加一層監控,是**把 Kubernetes 的 Service 實作整個換掉**。而今天最值得記住的發現,是動手之前就先問清楚「現在到底是誰在做這件事」得到的:**那個要被換掉的元件,有一大半工作早就不是它在做了。**

!!! abstract "你在課程的哪裡"
    - **Day 0–6**:eBPF 的載入路徑與 verifier、bpftrace 的即時追蹤、Falco 的規則引擎、Tetragon 的核心層攔截。四套工具都掛在 syscall 與 LSM 那一層。
    - **今天**:建一座沒有 CNI 的叢集、自管 Cilium、開啟 kube-proxy replacement。驗收有兩半:連通性測試通過,而且叢集裡不再有 kube-proxy 但 Service 仍然可達。
    - **Day 8–9**:有了自己的資料平面,才能寫 L3 到 L7 的網路政策,以及用 Hubble 看流量。

## 為什麼這一天要另外開一座叢集

自管 Cilium **只能裝在建立時就指定 `--network-plugin none` 的叢集上,既有叢集轉換不了**。受管的「Azure CNI powered by Cilium」可以原地開啟,但它的 L7 政策與 Hubble UI 需要付費附加元件——而那兩項正是 Cilium 最值得學的部分。更關鍵的是,原地升級會**永久改掉**前六天那座叢集的資料平面,而 Falco 與 Tetragon 都還裝在上面。

所以今天新建一座臨時叢集。這個決定本身就是課程內容的一部分:**BYOCNI 不是一個設定選項,是一條從叢集建立那一刻就分岔的路。**

今天走六步:

| 步驟 | 做什麼 | 為什麼排在這 |
|---|---|---|
| 1 | 建一座沒有 CNI 的叢集 | **先看清楚 CNI 不在時壞的是什麼** |
| 2 | 裝 Cilium | 版本要自己釘、網段要自己算,兩件都有坑 |
| 3 | 連通性測試 | 驗收的第一半,而且要學會怎麼讀那份報表 |
| 4 | 換掉 kube-proxy | **先問它現在在做什麼** |
| 5 | 量化差異 | 規則數、查找路徑、延遲,以及哪一個數字不能宣稱 |
| 6 | 停機重啟的存活驗證 | 縮到零被 API 退件,`stop` 是唯一的收尾方式——而它沒被驗過 |

## 步驟 1: 建一座沒有網路的叢集

配額不要用推論的。前一天的節點池已經歸零,理論上額度是空的——但理論不算數:

```console
$ az vm list-usage -l japaneast --query "[?...].{n:localName,c:currentValue,l:limit}" -o tsv
Total Regional Low-priority vCPUs    0    50      ← spot 配額整條沒人用
Total Regional vCPUs                16   200
Standard DASv5 Family vCPUs          0    50      ← 要看的是這一欄
```

`Total Regional vCPUs` 是訂閱層級的總帳(含其他資源),`Standard DASv5 Family` 才是這次要動的家族。

接著建叢集。直覺的做法是「2 台 spot」,而它在第一行指令就撞牆了([地雷 1](#mine-1)):

```bash
az aks create -g <resource-group> -n <cluster> \
  --location japaneast --tier free \
  --network-plugin none \
  --nodepool-name system --node-count 1 --node-vm-size Standard_D2as_v5 \
  --generate-ssh-keys
```

**5 分 13 秒。** 回傳的 JSON 裡 `networkProfile.networkPlugin` 是 `none`、`serviceCidr` 是 `10.0.0.0/16`,而 `podCidr` 是 **`null`**——最後那個 null 等一下會變成一顆地雷。

### 沒有 CNI 的叢集長什麼樣子

這是本章最值得慢慢看的一段,因為多數人不會刻意去看它。

```console
$ kubectl get nodes
NAME                             STATUS     ROLES   AGE     VERSION
<node>                           NotReady   <none>  2m14s   v1.35.6

$ kubectl get node -o jsonpath='{range .status.conditions[*]}…'
Ready  False  KubeletNotReady
       container runtime network not ready: NetworkReady=false
       reason:NetworkPluginNotReady
       message:Network plugin returns error: cni plugin not initialized

$ kubectl get node -o jsonpath='{.spec.taints}'
[{"effect":"NoSchedule","key":"node.kubernetes.io/not-ready"}]
```

**節點 `NotReady` 不是 kubelet 壞了。** `MemoryPressure`、`DiskPressure`、`PIDPressure` 三個條件全部正常,kubelet 活著、有在回報、有在跑容器。它唯一說不出口的是「我的 CNI 沒初始化」——而 `Ready=False` 一旦成立,Kubernetes 就替它打上 `node.kubernetes.io/not-ready:NoSchedule`。

於是 pod 分成兩半:

```console
$ kubectl get pods -A -o custom-columns=NAME:…,HOSTNET:.spec.hostNetwork,STATUS:.status.phase
cloud-node-manager-cz2z9              true     Running
csi-azuredisk-node-fr7dk              true     Running
kube-proxy-bxgkf                      true     Running
konnectivity-agent-7fc6788985-jlh2t   true     Pending      ← 注意這一顆
coredns-5d474ff6db-8qrgn              <none>   Pending
metrics-server-6f4547db5c-c9chh       <none>   Pending
```

直覺的說法是「hostNetwork 的活著,其他的排不上」。**這個說法只對了一半**,而錯的那一半見[地雷 2](#mine-2)。

CoreDNS 的排程錯誤訊息也值得抄下來,因為它會誤導人:

```console
Warning  FailedScheduling  default-scheduler  no nodes available to schedule pods
```

「沒有節點」——但 `kubectl get nodes` 明明列得出一台。scheduler 說的「沒有」是「沒有可用的」,而它不會告訴你為什麼。**在 BYOCNI 叢集上,這行訊息的正解永遠是「你還沒裝 CNI」。**

這個中間狀態可以這樣講:**沒有 CNI 的 Kubernetes 是一座只能跑 hostNetwork DaemonSet 的叢集。** control plane 全部正常、API 全部能回、`kubectl` 一切如常——壞掉的是「pod 有自己的 IP」這個前提,而幾乎所有東西都建立在那個前提上。

## 步驟 2: 裝 Cilium——版本自己釘,網段自己算

裝法選 Helm 而不是 `cilium install`,理由有兩層:版本是明文的而不是某個 CLI 二進位檔內建的常數([地雷 3](#mine-3));以及 Sprint 2 的 Falco 與 Tetragon 都是 Helm 裝的,**要拿三套工具的資源佔用互相比較,安裝方式得是同一把尺**。cilium-cli 仍然留著,但只當診斷工具——`cilium status` 與 `cilium connectivity test` 這兩件事 Helm 做不到。

網段必須自己指定,原因見[地雷 4](#mine-4)。正確的 values:

```bash
cat > cilium-values.yaml <<'EOF'
# AKS BYOCNI preset: writes the CNI conflist and skips the AKS-specific bits.
aksbyocni:
  enabled: true
nodeinit:
  enabled: true

# MUST be set. Cilium's cluster-pool default is 10.0.0.0/8, which swallows
# AKS's service CIDR (10.0.0.0/16). 10.244.0.0/16 is what AKS's own kube-proxy
# already assumes — see mine 4.
ipam:
  mode: cluster-pool
  operator:
    clusterPoolIPv4PodCIDRList: ["10.244.0.0/16"]
    clusterPoolIPv4MaskSize: 24

kubeProxyReplacement: false     # turned on in step 4, deliberately not yet
EOF

helm repo add cilium https://helm.cilium.io/ && helm repo update cilium
helm install cilium cilium/cilium --version 1.20.0 -n kube-system -f cilium-values.yaml
```

裝出來的是**三個 DaemonSet 加一個 Deployment**:

| 元件 | 形狀 | 做什麼 |
|---|---|---|
| `cilium`(agent) | DaemonSet,每節點 1 | 掛載 eBPF 程式、管 endpoint、寫 CNI conflist |
| `cilium-envoy` | DaemonSet,每節點 1 | L7 政策的執行體(Day 8 的 HTTP 規則要靠它) |
| `cilium-node-init` | DaemonSet,每節點 1 | 節點層前置,跑完就 sleep(實測 1m CPU / 0Mi) |
| `cilium-operator` | Deployment | 叢集層:IPAM 配發 pod CIDR、身分回收 |

`cilium-envoy` 獨立成一個 DaemonSet 是 1.16 之後的預設(以前塞在 agent 裡)。它現在只吃 13Mi;Day 8 開 L7 政策之後要再量一次(**後續實測是沒有長**——一顆 endpoint、一條 HTTP 規則的規模下仍是 14Mi)。

另外裝了 **10 個 CRD**,Day 8 要用的 `CiliumNetworkPolicy` 是其中之一。

### 資源足跡:跟 Falco、Tetragon 正面對照

Day 3 與 Day 5 對 Falco 與 Tetragon 問過同一組問題——chart 有沒有給 `resources`?有沒有給 `priorityClassName`?Cilium 的答案**一半不一樣,而不一樣的那一半剛好是重要的那一半**:

| | requests(chart 預設) | 實測用量/節點 | QoS | priority |
|---|---|---|---|---|
| **Falco** 0.44.1 | `100m/512Mi` | 14–24m / 84–90Mi | Burstable | **0** |
| **Tetragon** 1.7.0 | `{}` | 5–11m / 83–120Mi | **BestEffort** | **0** |
| **Cilium** 1.20.0 agent | `{}` | 23–30m / 85–102Mi | Burstable | **2000001000** |

**[Day 3 地雷 1](sprint2-day3-falco-basics.md#mine-1) 那個問題,Cilium 答對了。** Falco 與 Tetragon 的 chart 都不設 `priorityClassName`,priority 0 比每一個系統元件都低,在滿載節點上是搶佔演算法第一個挑中的;Cilium 直接掛 `system-node-critical`,跟 kube-proxy 同級。

這是合理的——**它不是監控,它是網路本身,被搶佔的後果是節點斷網**。但值得記住的是「chart 有沒有替你想過這件事」在三套 CNCF 專案之間答案不同。

至於那個 Burstable 是怎麼來的,見[地雷 5](#mine-5)——答案不太好看。

## 步驟 3: 連通性測試——驗收的第一半

```console
$ cilium status --wait
 /¯¯\__/¯¯\    Cilium:             OK
 \__/¯¯\__/    Operator:           OK
 /¯¯\__/¯¯\    Envoy DaemonSet:    OK
 \__/¯¯\__/    Hubble Relay:       disabled          ← Day 9 才開
```

```console
$ cilium connectivity test --tolerations kubernetes.azure.com/scalesetpriority \
      --test-namespace cilium-test-kp
（約 23 分鐘）

📋 Test Report
❌ 2/82 tests failed (0/672 actions), 50 tests skipped, 1 scenarios skipped
```

**這一行很容易被誤讀成「有兩件事壞了」,所以要把數字讀清楚:**

- **672 個實際動作失敗數是 0。** 每一次真正發出去的封包、每一條政策判定,全部符合預期。
- 132 項裡 **50 項被跳過**,理由分三類:功能沒開(加密、Egress Gateway 等本課刻意沒開)、會改動節點狀態(CLI 預設不跑,這是對的)、版本條件(舊版加密測試已被 v2 取代)。
- 剩下 82 項真的跑了,**80 項通過**。

那兩項失敗跟資料平面無關,見[地雷 6](#mine-6) 與[地雷 7](#mine-7)。

**本日對第一半的判定:通過,但要附上那兩顆地雷。** 判定根據是「0/672 動作失敗」加上「失敗原因是測試框架的探針排不上一顆被強制打 taint 的節點,而它們測的功能本來就沒開」。**把不能跑的東西記成綠燈,跟把它藏起來一樣糟**——所以步驟 5 的第二輪測試明確排除那兩項,讓報表能夠真的全綠,而排除的理由寫在地雷裡。

## 步驟 4: 換掉 kube-proxy——先問它現在到底在做什麼 {#step-4}

`cilium status` 寫著 `KubeProxyReplacement: False`,而 kube-proxy 兩顆 pod 好端端跑著。看起來很清楚:現在 ClusterIP 是 kube-proxy 在做,等一下換成 Cilium。

**這個推論是錯的,而且錯得很有教育意義。**

### 先看 kube-proxy 寫了什麼

節點視角需要一顆 `hostNetwork: true` 加 `privileged` 的 pod。少了 `hostNetwork`,`iptables -t nat -S` 會給你一份空的規則表——那個空不是「沒有規則」,是**你在看容器自己的網路命名空間**。

被測服務是一個 ClusterIP,兩顆後端。完整的查找路徑:

```console
$ iptables -t nat -S PREROUTING
-A PREROUTING -m comment --comment "cilium-feeder: CILIUM_PRE_nat" -j CILIUM_PRE_nat
-A PREROUTING -m comment --comment "kubernetes service portals" -j KUBE-SERVICES

$ iptables -t nat -S | grep <cluster-ip>
-A KUBE-SERVICES -d <cluster-ip>/32 -p tcp --dport 80 -j KUBE-SVC-XS3ZU56555AQFGIC

$ iptables -t nat -S KUBE-SVC-XS3ZU56555AQFGIC
-A KUBE-SVC-… -m statistic --mode random --probability 0.50000000000 -j KUBE-SEP-JZF7FF4KHPDVC7PP
-A KUBE-SVC-… -j KUBE-SEP-A6QIQCODODBCXNWL

$ iptables -t nat -S KUBE-SEP-JZF7FF4KHPDVC7PP
-A KUBE-SEP-… -p tcp -j DNAT --to-destination 10.244.1.202:8080
```

這就是 iptables 模式的形狀:**`KUBE-SERVICES` 是一條線性鏈**(每個 Service 一條規則,逐條比對到中),命中後跳進該 Service 的 `KUBE-SVC-*`,用 `statistic --mode random --probability` 逐條擲骰子挑後端,最後在 `KUBE-SEP-*` 做 DNAT。兩顆後端就是第一條 0.5、第二條吃剩下的;三顆會是 0.333 / 0.5 / 1,因為那是條件機率。

### 誰真的做了那次翻譯

`cilium service list` 這時候也是滿的——兩邊都「有」這條 Service:iptables 有鏈,Cilium 有 map。**有資料不代表資料被用到。**

唯一不會騙人的是封包計數器:清零、打固定次數、再讀。

```console
### A) 呼叫端 = 一般 pod
$ iptables -t nat -Z                                    ← 全表計數器清零
$ kubectl exec client -- sh -c 'for i in $(seq 1 20); do curl -s -o /dev/null -w "%{http_code}" http://<cluster-ip>/; done'
200200200200200200200200200200200200200200200200200200200200
$ iptables -t nat -L KUBE-SERVICES -n -v | grep <cluster-ip>
    0     0 KUBE-SVC-XS3ZU56555AQFGIC  tcp  --  0.0.0.0/0  <cluster-ip>  tcp dpt:80
      ↑ 20 次請求全部成功,iptables 一個封包都沒看到

### B) 呼叫端 = hostNetwork(節點 root netns)
$ iptables -t nat -Z
$ (在 node-shell 裡) for i in $(seq 1 20); do curl -s -o /dev/null -w "%{http_code}" http://<cluster-ip>/; done
200200200200200200200200200200200200200200200200200200200200
$ iptables -t nat -L KUBE-SERVICES -n -v | grep <cluster-ip>
   20  1200 KUBE-SVC-XS3ZU56555AQFGIC  tcp  --  0.0.0.0/0  <cluster-ip>  tcp dpt:80
$ iptables -t nat -L KUBE-SVC-XS3ZU56555AQFGIC -n -v
   10   600 KUBE-SEP-JZF7FF4KHPDVC7PP  … probability 0.50000000000
   10   600 KUBE-SEP-A6QIQCODODBCXNWL
      ↑ 20 次全部走 iptables,而且骰子擲出漂亮的 10/10
```

**同一個 ClusterIP、同一顆節點、同一秒,兩個呼叫端的答案完全相反。** 這是[地雷 8](#mine-8),也是今天的頭條。

### 換掉它

順序很重要:**先讓 Cilium 接手,再拆 kube-proxy**。反過來做的話,中間那段時間 hostNetwork 的流量沒有人服務,而 cilium-agent 重啟時要用的就是 hostNetwork。

```bash
# The two k8sService* lines are NOT optional. Once kube-proxy is gone, nobody
# translates the `kubernetes` Service (10.0.0.1:443) — and cilium-agent needs the
# API server to learn what Services exist. Give it the control plane's real FQDN
# to break that circular dependency.
cat >> cilium-values.yaml <<'EOF'
kubeProxyReplacement: true
k8sServiceHost: <api-server-fqdn>
k8sServicePort: "443"
EOF

helm upgrade cilium cilium/cilium --version 1.20.0 -n kube-system -f cilium-values.yaml --wait
kubectl -n kube-system rollout restart ds/cilium
```

漏掉那兩行不會報錯,會是 agent CrashLoopBackOff 而**整座叢集的 Service 一起停擺**。

換完之後 agent 的自我描述變了:

```console
KubeProxyReplacement Details:
  Status:               True
  Socket LB:            Enabled          ← 從 Disabled 變成 Enabled,這才是重點
  Services:
  - ClusterIP:      Enabled
  - NodePort:       Enabled (Range: 30000-32767)   ← 這四項才是 replacement 真正帶來的
  - LoadBalancer:   Enabled
  - externalIPs:    Enabled
  - HostPort:       Enabled
```

拆掉 kube-proxy 本身有兩顆地雷:[地雷 9](#mine-9)(刪了會自己回來)與[地雷 10](#mine-10)(走了規則不會走)。

## 步驟 5: 量化——規則數、查找路徑,以及不能宣稱的那一項

### 規則數

叢集裡 10 個 Service:

| | nat 表總行數 | 其中 `KUBE-*` | 其中 `CILIUM_*` | Service 相關規則 |
|---|---|---|---|---|
| kube-proxy 在 | **135** | 120 | 11 | 120 |
| replacement 之後 | **16** | 1(`KUBE-KUBELET-CANARY`,kubelet 的空鏈) | 11 | **0** |

**10 個 Service 等於 120 條 nat 規則,而且是線性成長**:每多一個 Service,`KUBE-SERVICES` 多一條要逐條比對的規則;每多一顆後端,那個 Service 的鏈多一條。剩下的 11 條 `CILIUM_*` 全部是 masquerade,**跟 Service 查找無關,而且不隨 Service 數量成長**。

### 查找路徑

這比規則數更根本。

```console
$ bpftool cgroup show /run/cilium/cgroupv2
ID     AttachType                Name
4706   cgroup_inet4_connect      cil_sock4_connect        ← 就是這一支
```

`cil_sock4_connect` 掛在 `cgroup_inet4_connect`——也就是**行程呼叫 `connect(2)` 的那一刻,在任何封包被組出來之前**。應用程式以為自己連到 ClusterIP,核心在 socket 層就把目的地改寫成後端 pod 的位址,之後這條連線的每一個封包從頭到尾都是「一般的 pod 對 pod 流量」。

| | kube-proxy(iptables 模式) | Cilium kube-proxy replacement |
|---|---|---|
| 發生時機 | 每個封包,在 netfilter hook | **每條連線一次**,在 `connect()` |
| 資料結構 | 線性規則鏈,O(Service 數) 比對 | eBPF hash map,O(1) 查找 |
| 後端選擇 | 逐條擲骰子 | map 的 slot 索引 |
| 之後每個封包 | 走 conntrack 查 NAT 狀態 | **不需要**,位址已經是真的 |

### 延遲:這一項沒有辦法誠實地宣稱改善

`time_connect`(只含 TCP 三次握手),各 300 次:

| 量測 | mean | p50 | p90 | p99 |
|---|---|---|---|---|
| kube-proxy(iptables) | 0.370 ms | 0.305 | 0.515 | 1.600 |
| replacement 之後 run1 | 0.311 ms | 0.257 | 0.378 | 0.937 |
| replacement 之後 run2 | 0.250 ms | 0.221 | 0.332 | 0.617 |
| **對照組:直接打後端 pod IP** | **0.249 ms** | **0.220** | 0.334 | 0.727 |

兩個問題讓「快了 27%」站不住:**BEFORE 那一組是在連通性測試同時跑的時候量的**(負載不同),而 **run1 與 run2 的差距(p50 36 µs)比任何想宣稱的效果都大**——這台機器的 run-to-run 雜訊已經吃掉整個訊號。

但同一張表裡有一個**乾淨**的結論,而且比延遲比較更有意思:**走 ClusterIP(p50 0.221)與直接打後端 pod IP(p50 0.220)在量測精度內完全一樣。** 對照組沒有經過任何 Service 翻譯,而 socket LB 那一組多做了一次 map 查找——**多出來的成本量不到**。

所以誠實的說法是:**「Cilium 的 ClusterIP 翻譯在這台機器上量不到成本」有證據;「比 kube-proxy 快多少」沒有。** 而在 10 個 Service 的實驗叢集上,線性鏈的代價本來就還沒開始顯現——**那個差距要在幾百上千個 Service 的叢集才會變成數字**,那也正是它真正的適用場景。

### 第二輪連通性測試

排除地雷 6 那兩項之後重跑:

```console
✅ All 80 tests (678 actions) successful, 52 tests skipped, 0 scenarios skipped.
```

動作總數反而從 672 變成 **678**——多出來的 6 個是 NodePort 相關的:replacement 開啟之後 `NodePort: Enabled`,測試框架多測了幾條先前被跳過的路徑。

### 驗收第二半的完整證據

```console
$ kubectl -n kube-system get ds kube-proxy
NAME         DESIRED   CURRENT   READY   NODE SELECTOR
kube-proxy   0         0         0       lab.local/no-kube-proxy=true

$ kubectl get pods -A | grep -c kube-proxy                       → 0
$ (兩顆節點) nsenter -t 1 -m -p -- pgrep -a kube-proxy           → (no kube-proxy process)
$ cilium-dbg status | grep KubeProxyReplacement                  → True
$ kubectl exec client -- curl http://svc-backend…/               → 200
$ (node-shell, hostNetwork) curl http://<cluster-ip>/            → 200
$ kubectl exec client -- curl -k https://10.0.0.1:443/healthz    → 401
```

最後那個 `401` 是好消息:它代表 TLS 握手完成、HTTP 請求送達 API server,只是沒帶憑證。**`kubernetes` Service 這條「換掉 kube-proxy 最可能弄壞」的路徑是活的。**

## 步驟 6: 停機重啟的存活驗證——BYOCNI 只剩一條收尾路

直覺的收尾方式是「兩個節點池都縮到 0」,而它被 API 退件([地雷 11](#mine-11))。**BYOCNI 叢集要停止計費,只剩 `az aks stop` 一條路——而那正好是一件沒人驗證過的事**:停機再開機之後,CNI 會不會自己回來?

既然它是唯一的路,就當場驗證:

```console
$ az aks stop  …      （2 分 04 秒）
$ az aks start …      （4 分 04 秒）
$ kubectl get nodes
<node>-vmss000001   Ready   <none>   90s   v1.35.6
              ↑ 注意編號變了:這是一台全新的 VM,不是原來那台
```

驗收全部一次過,沒有任何人工修補:Cilium 四個元件全部 Running、**kube-proxy 的 patch 存活**、`KubeProxyReplacement: True`、Service 可達。

**節點是全新的 VM**——停機再開機不是把原來那台叫醒,是重建。所以「CNI 會不會回來」這個問題實際上是「新節點上 CNI 會不會自己裝好」,而答案是會。

## 誠實的差距

- **延遲改善沒有證據。** 上面已經寫清楚為什麼:負載不對等、雜訊大於訊號。要回答「比 kube-proxy 快多少」需要固定負載、交錯取樣的 A/B,本課的條件不具備。
- **兩項連通性測試沒有跑過。** 它們測的是加密功能,而這座叢集沒開加密;失敗原因是測試框架的探針排不上 spot 節點。**這兩項在本課從未被驗證過,只是被排除。**
- **只有 10 個 Service。** 規則數與查找路徑的差別在這個規模下是結構性的而不是效能性的。線性鏈真正變成問題的規模,本課沒有重現。
- **kube-proxy 沒有真正被移除。** DaemonSet 物件還在(而且刪不掉),只是被釘在一個不存在的節點選擇器上。原生的關法綁在訂閱層級的預覽旗標,為了一座臨時叢集不做那個異動。
- **`az aks stop`/`start` 只驗過一次。** kube-proxy 的 patch 撐過那一次,但單一次觀察不足以當成保證。

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| BYOCNI 叢集建立 | `networkProfile.networkPlugin` 是 `none`,且節點在裝 CNI 前是 `NotReady` | 兩項都成立,建立耗時 5 分 13 秒 |
| Cilium 就緒 | `cilium status` 四個元件皆 OK,pod 網段不與 service CIDR 重疊 | agent / envoy / node-init / operator 全 OK;pod CIDR `10.244.0.0/16` |
| **連通性測試** | 實際執行的測試全數通過,且**失敗項的原因可歸因** | 第一輪 80/82(2 項為框架排程問題);排除後第二輪 **80/80、678 動作全過** |
| **kube-proxy 不在** | 零個 pod **且**兩顆節點上零個行程 | 兩項都成立 |
| Service 仍可達 | pod、hostNetwork、跨節點、以及 `kubernetes` Service 四條路徑 | 四條全通(`kubernetes` 回 401 = 握手完成) |
| 規則數下降可量化 | nat 表行數與 `KUBE-*` 規則數 | **135 → 16 行**,120 條 Service 規則 → **0** |
| 停機重啟後仍成立 | 重開之後 kube-proxy 仍為 0、Service 仍可達 | 成立(新 VM,無人工修補) |

## 地雷記錄

### 地雷 1:`az aks create` 根本沒有 `--priority` 這個參數 {#mine-1}

**症狀**:照著「2 台 spot 節點」的計畫下指令,被拒絕。

```console
$ az aks create … --priority Spot --eviction-policy Delete --spot-max-price -1 …
ERROR: unrecognized arguments: --priority Spot --eviction-policy Delete --spot-max-price -1
```

**根因**:不是「參數值不合法」,是**這個子命令沒有這組參數**。AKS 的第一個節點池一定是 system pool,而 system pool 不能是 spot——這條限制不是在執行期擋你,是直接不出現在 CLI 介面上(`az aks nodepool add` 才有 `--priority`)。

**後果**:「一座 2 台 spot 的叢集」在 AKS 上不存在。最省的形狀是 **1 台隨需 system 加 N 台 spot user pool**,而那台隨需節點就是成本估算裡最容易漏掉的一項——本課的實際每小時成本因此比計畫估的高出約一倍。

### 地雷 2:決定能不能排到 `NotReady` 節點的是 taint 容忍,不是 hostNetwork {#mine-2}

**症狀**:在還沒裝 CNI 的叢集上,`konnectivity-agent` 明明是 `hostNetwork: true`,卻一樣 Pending。

**根因**:決定一顆 pod 能不能排到 `NotReady` 節點上的,是它容不容忍 `node.kubernetes.io/not-ready:NoSchedule`。**DaemonSet controller 會自動替它管的 pod 加上這個容忍**(所以 `kube-proxy`、`csi-*`、`cloud-node-manager` 都在跑),**而 Deployment 不會**。

`konnectivity-agent` 是 Deployment:它的第一顆副本在節點被打上 taint **之前**就排上去了(所以 Running),第二顆碰上 taint 就卡住。

**教訓**:「hostNetwork 的活著」這個直覺會在這裡騙你。兩顆同樣是 hostNetwork 的 pod 一顆 Running 一顆 Pending,差別在它們的控制器類型。

### 地雷 3:cilium-cli 會告訴你 stable 是哪一版,然後裝另一版 {#mine-3}

```console
$ cilium version --client
cilium-cli: v0.19.7 compiled with go1.26.5 on darwin/arm64
cilium image (default): v1.19.5
cilium image (stable):  v1.20.0
```

**同一段輸出裡,default 和 stable 差了一個 minor。** `cilium install` 不帶 `--version` 用的是 default。

這不是 bug——CLI 的預設值有自己的發佈節奏——但它讓「照官方快速上手打一行指令」的結果是一個**比當下 stable 舊一版的叢集,而且沒有任何一行輸出提醒你**。

**修法**:用 Helm 並寫死版本,或者 `cilium install --version` 明確指定。

### 地雷 4:Cilium 預設的 pod 網段把 AKS 的 Service 網段整個吃掉 {#mine-4}

**症狀**:照官方 AKS BYOCNI 快速上手裝完,節點 Ready,然後 metrics-server 進 `CrashLoopBackOff`:

```console
E0807 06:19:07 … dial tcp: lookup <api-fqdn> on 10.0.0.10:53:
  read udp 10.0.0.140:34964->10.0.0.10:53: read: connection refused
```

**根因**:pod 拿到的 IP 是 `10.0.0.x`,而這座叢集的 service CIDR 是 `10.0.0.0/16`、kube-dns 的 ClusterIP 是 `10.0.0.10`。**pod 正在從 Service 的網段裡拿位址。** 封包送到 `10.0.0.10` 時,Cilium 認為那是本節點的一顆 pod 而不是一個 Service,於是 `connection refused`。

來源是 Cilium `cluster-pool` IPAM 的預設 `clusterPoolIPv4PodCIDRList` 為 **`10.0.0.0/8`**,而 AKS 的預設 service CIDR `10.0.0.0/16` 完全包含在裡面。`aksbyocni` 這個 preset **不會**幫你處理。

**最諷刺的是正確答案就寫在叢集自己身上**:

```console
$ kubectl -n kube-system get ds kube-proxy -o jsonpath='{…containers[0].command}'
["kube-proxy", …, "--cluster-cidr=10.244.0.0/16", …]
```

AKS 預設的 kube-proxy 一直在說「pod 在 `10.244.0.0/16`」。`az aks show` 的 `networkProfile.podCidr` 是 `null`(因為 BYOCNI 不由 AKS 配發),**但 kube-proxy 的參數不是 null**。

**教訓**:BYOCNI 的意思是「pod 網段由你負責」,**不是「叢集其他元件對 pod 網段沒有假設」**。

**修法**是重裝而不是升級——`CiliumNode` 上的網段是已分配狀態,要先刪掉:

```console
$ helm uninstall cilium -n kube-system
$ kubectl delete ciliumnode --all
$ helm install cilium … -f cilium-values.yaml      # 含正確的 clusterPoolIPv4PodCIDRList
```

而且**已經拿到舊網段 IP 的 pod 不會自己修好**,要手動重建。在正式環境這一點是重點:**改 pod 網段不是改設定,是整個資料平面重建。**

### 地雷 5:`cilium` pod 的 Burstable 是一顆 init container 借來的 {#mine-5}

**症狀**:Cilium 是 Burstable、Tetragon 是 BestEffort,兩者的長駐容器卻同樣是 `resources: {}`。

**根因**:

```console
$ kubectl -n kube-system get ds cilium -o jsonpath='{range .spec.template.spec.initContainers[*]}…'
config                   -> {}
mount-cgroup             -> {}
…
install-cni-binaries     -> {"limits":{"cpu":"1","memory":"1Gi"},"requests":{"cpu":"100m","memory":"10Mi"}}
```

七個 init container 裡只有最後一個有 requests。Kubernetes 算一顆 pod 的有效 request 時取 **max(所有長駐容器的總和, 各 init container 的最大值)**,於是這顆 pod 的 request 變成 `100m/10Mi`——**來自一個複製完二進位檔就結束的 init container**,而不是那個要跑到節點壽命結束的 agent。

**後果有兩層**:QoS 被拉成 Burstable,看起來比 Tetragon「有保障」,但保障的量是錯的;而**記憶體 request 10Mi、實測 102Mi,差 10 倍**——排程器對這顆節點的記憶體帳本少算了約 92Mi。

[Day 3 地雷 7](sprint2-day3-falco-basics.md#mine-7) 記的是相反方向的問題(Falco 超額 4–7 倍,於是排不進去)。**兩種方向都是錯的,而低估的這種更難發現**:它不會讓 pod 排不上,它會讓節點在你以為還有餘裕的時候開始換頁。

`priorityClassName` 救得了「被搶佔」,救不了「排程器以為你只要 10Mi」。

### 地雷 6:`--tolerations` 沒有套到測試框架的 DaemonSet {#mine-6}

**症狀**:連通性測試兩項失敗,而訊息不是「封包不通」:

```console
🟥 Fail to acquire host namespace pod on <spot-node> (client's node)
🟥 Could not find host network namespace pod on client node <spot-node>
```

**根因**:測試框架需要每顆節點各一顆 `hostNetwork` 探針 pod,而它只排上了一顆。

```console
$ kubectl -n cilium-test-kp-1 get ds host-netns
NAME         DESIRED   CURRENT   READY
host-netns   1         1         1        ← 兩顆節點,DESIRED 只有 1

$ kubectl … get deploy client -o jsonpath='{…tolerations}'
[{"key":"kubernetes.azure.com/scalesetpriority","operator":"Exists"}]   ← 指令帶的 --tolerations 有生效
$ kubectl … get ds host-netns -o jsonpath='{…tolerations}'
                                                                        ← 空的
```

**`--tolerations` 只套到了測試的 Deployment,沒有套到 `host-netns` DaemonSet。**

而 spot 節點的 taint 拔不掉([地雷 7](#mine-7)),所以在有 spot 節點池的 AKS 叢集上,**這兩項測試無法執行**。同一組裡的另一項被正確地以「功能沒開」跳過了,這兩項沒有——那是跳過條件沒有覆蓋完整。

### 地雷 7:AKS spot 節點池的 taint 拔不掉,而兩種拔法的失敗方式不一樣 {#mine-7}

```console
$ az aks nodepool update … -n spotpool --node-taints ""
Succeeded                                          ← ARM 說成功
$ az aks nodepool show … --query nodeTaints
["kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]     ← 東西還在
```

```console
$ kubectl taint node <spot-node> kubernetes.azure.com/scalesetpriority=spot:NoSchedule-
Error from server: admission webhook "aks-node-validating-webhook.azmk8s.io" denied the request:
  Taint delete request … refused. User is attempting to delete a taint configured on aks node pool "spotpool".
```

**ARM 那一條是靜默無效(回 `Succeeded` 而什麼都沒改),kubectl 那一條是明確拒絕。** 兩者對照很有價值:**如果只試了第一種,你會以為指令生效了,然後去找別的原因。**

**實務結論**:在 AKS 上用 spot 省錢,代價是每一個要跑在那顆節點上的東西都必須自己帶容忍——**你自己的工作負載可以加,但第三方 chart 與工具不一定給得了**。做成本規劃時這是一項隱藏的相容性成本,不只是「便宜 81.5%」。

### 地雷 8:`kubeProxyReplacement: false` 不代表 kube-proxy 在做事 {#mine-8}

**症狀**:沒有症狀——這正是問題所在。在 `kubeProxyReplacement: false`、kube-proxy 好端端跑著的狀態下,**一般 pod 對 ClusterIP 的流量根本沒有經過它**([步驟 4](#step-4) 的封包計數器實測)。

**根因**:

```console
KubeProxyReplacement:   False
KubeProxyReplacement Details:
  Status:               False
  Socket LB:            Disabled
  Services:
  - ClusterIP:      Enabled          ← 就是這一行
  - NodePort:       Disabled
  - LoadBalancer:   Disabled
  - externalIPs:    Disabled
  - HostPort:       Disabled
```

`kubeProxyReplacement: false` 關掉的是 **NodePort、LoadBalancer、externalIPs、HostPort 加 socket LB**,**ClusterIP 一直是開的**。Cilium 掛在每個 pod veth 上的 tc BPF 程式在封包離開 pod 的網路命名空間那一刻就做完 DNAT,封包到達節點時目的地已經是後端 pod IP——`KUBE-SERVICES` 鏈當然一個字都比對不到。

所以在一座還沒開 replacement 的 Cilium 叢集上,kube-proxy 實際負責的是**節點自己發起的流量**(hostNetwork pod、節點上的服務、kubelet)以及 NodePort 那幾類入口。

**這會誤導兩種人**:以為「先不開 replacement 比較保守」的(保守的範圍比想像中小得多),和拿 iptables 規則數量當「kube-proxy 負載」指標的(**那些規則絕大多數是死的**)。

### 地雷 9:刪掉 kube-proxy DaemonSet,受管控制面 80 秒後把它裝回來 {#mine-9}

```console
$ kubectl -n kube-system delete ds kube-proxy
daemonset.apps "kube-proxy" deleted
t+20s      not found
t+60s      not found
t+80s    kube-proxy   2   2   0   2   0   <none>   0s     ← 回來了
t+100s   kube-proxy   2   2   2   2   2   <none>   21s    ← 而且已經跑起來
```

**根因**:在受管 Kubernetes 上 kube-proxy 是受管附加元件,控制面的 reconciler 會把它補回來。刪除指令會成功、`kubectl get` 會說不存在,**一分多鐘之後它自己回來**——如果你在這中間下結論「移除成功」,就會得到相反的答案。

**修法**:官方給受管叢集的做法是把 DaemonSet 釘在一個**不存在的節點選擇器**上。那個標籤鍵取什麼名字都可以,唯一的要求是叢集裡沒有任何節點帶著它。

```console
$ kubectl -n kube-system patch daemonset kube-proxy \
    -p '{"spec":{"template":{"spec":{"nodeSelector":{"lab.local/no-kube-proxy":"true"}}}}}'
```

這個 patch **沒有**被 reconciler 改回去(觀察 125 秒,並且撐過一次停機重啟)。

誠實記錄驗收的實際形狀:**DaemonSet 物件還在(而且刪不掉),但零個 pod、兩顆節點上零個行程**。這是受管叢集上「沒有 kube-proxy」能達到的最強狀態。

### 地雷 10:kube-proxy 走了,它寫的規則不會跟著走 {#mine-10}

```console
$ (kube-proxy 的 pod 早就不在了) iptables -t nat -S | wc -l
135
$ iptables -t nat -S | grep -c KUBE-
120
```

**根因**:kube-proxy 是使用者空間的行程,規則是它寫進 netfilter 的**持久狀態**。行程結束,規則留著。

這些規則現在是純粹的死重量:沒有人維護它們(Service 增減不會更新),但**每個從節點發出的封包仍然要走過那條線性鏈**。更糟的是它們現在是**過期的**——哪天有封包真的比對中一條指向已經不存在的 pod IP 的 DNAT,結果是連到黑洞。

**修法**:

```console
$ iptables-save | grep -v KUBE- | iptables-restore
```

清完剩 16 行,其中只有一條 `KUBE-KUBELET-CANARY`(那是 kubelet 的空鏈,不是 kube-proxy 的)。

### 地雷 11:AKS 的 system pool 不能縮到 0 {#mine-11}

```console
$ az aks nodepool scale … -n spotpool --node-count 0
Succeeded                                          ← spot pool 沒問題

$ az aks nodepool scale … -n system --node-count 0
ERROR: (InvalidParameter) agentPoolProfile.count was 0. It must be greater or equal to
       minCount:1 … 3) The node is a system pool.
```

**system pool 最少一台。** 而那一台是隨需的([地雷 1](#mine-1)),留著過夜的成本會把整個 sprint 的節點預算燒穿。

**後果**:「縮容到零」這個收尾方式在這座叢集上不存在,只剩 `az aks stop`。而那正好是一件沒驗證過的事(BYOCNI 叢集停機再開,CNI 會不會自己回來),於是**這個未驗證項目成了必經之路**。

**處理方式**:既然必經,就當場驗證完整的停機、開機、驗收循環,不把風險留到下一次。

## 帶得走的東西

- **動手換掉一個元件之前,先量清楚它現在到底在做什麼。** 今天最大的發現不是「換掉 kube-proxy 之後如何」,是「換掉之前它就已經沒在做那件事了」——而看出這件事只需要 `iptables -t nat -Z` 加兩輪 20 次請求。**有規則不代表規則被用到,唯一不會騙人的是封包計數器。**
- **設定的預設值是為別人的環境寫的。** Cilium 的預設 pod 網段吃掉 AKS 的 Service 網段、cilium-cli 的預設版本落後它自己回報的 stable 一版、測試框架的 `--tolerations` 沒套到自己的 DaemonSet。三件都不是 bug,都是「兩邊各自合理的預設值撞在一起」。
- **受管 Kubernetes 加自管資料平面,成本在交界處。** 第一個節點池不能是 spot、kube-proxy 刪了會自己回來、system pool 不能縮到 0、spot 的 taint 拔不掉——四件都跟 Cilium 無關,但四件都要處理。**BYOCNI 的真實成本不在 CNI,在這些邊界。**
- **量不出來就說量不出來。** 延遲那一項有數字、有表格,但負載不對等而且雜訊大於訊號,所以不能宣稱。同一組數據裡倒是有一個乾淨的結論(socket LB 的翻譯成本量不到),**而那個結論比原本想證明的更有意思。**
- **請求量與實際用量兩個方向都會錯,而低估更難發現。** 高估讓 pod 排不進去(看得見),低估讓排程器以為節點還有餘裕(看不見,直到開始換頁)。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[Cilium 的 kube-proxy replacement](https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free/)** —— 官方說明 `kubeProxyReplacement` 實際開關了哪些 Service 類型、socket LB 是整套機制的基礎,以及移除 kube-proxy 之後為什麼必須給 `k8sServiceHost`/`k8sServicePort`,對得上步驟 4。
- **[AKS 的 BYOCNI 說明](https://learn.microsoft.com/en-us/azure/aks/use-byo-cni)** —— `--network-plugin none` 的建立方式,以及「裝 CNI 之前節點會是 `NotReady`」那段輸出跟本課逐字相同;它也明講 BYOCNI 之下 pod 的 IP 配發完全由你選的 CNI 負責。
- **[Cilium 的 cluster-pool IPAM](https://docs.cilium.io/en/stable/network/concepts/ipam/cluster-pool/)** —— 地雷 4 的一手來源:官方文件自己在疑難排解段把 `10.0.0.0/8` 這個預設 pod CIDR 當成網段衝突的警語。

## 下一步

資料平面換完了,但今天只證明了它「能通」——所有東西都還是彼此暢通無阻。Cilium 真正的價值在於它能**決定誰不能通**,而且判斷可以做到 HTTP 方法這一層。

Day 8 寫網路政策:從命名空間隔離、埠限制,一路到「同一顆 pod 對同一個服務,GET 通過而 POST 被擋」。而那一天有一個對照特別值得等——**同樣是「工作負載連到不該連的地方」這件事,[Day 4](sprint2-day4-falco-custom-rules.md) 用 Falco 偵測、[Day 6](sprint2-day6-tetragon-enforcement.md) 用 Tetragon 殺行程,Day 8 則是直接丟封包。三種做法的代價完全不同。**

---

!!! quote ""
    Cilium 標誌為 Cilium 專案之官方資產(CNCF artwork),此處作社群教學用途。
