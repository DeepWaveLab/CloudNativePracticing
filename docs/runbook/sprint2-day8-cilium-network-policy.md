# Day 8: CiliumNetworkPolicy——從命名空間隔離寫到 HTTP 方法

![Cilium 官方標誌](../assets/logos/cilium-icon-color.svg){ align=right width="95" }

> 「工作負載連到不該連的地方」這件事,這個 sprint 已經處理過兩次:[Day 4](sprint2-day4-falco-custom-rules.md) 用 Falco **偵測**它、[Day 6](sprint2-day6-tetragon-enforcement.md) 用 Tetragon **殺掉行程**。今天是第三種——**直接丟封包**,而且判斷可以做到 HTTP 方法這一層。三種做法的代價完全不同,今天會把第三種的帳單算出來。

!!! abstract "你在課程的哪裡"
    - **[Day 7](sprint2-day7-cilium-kubeproxy.md)**:自管的 Cilium 資料平面已經就緒,kube-proxy 已經換掉,叢集裡所有東西彼此暢通無阻。
    - **今天**:寫網路政策決定誰不能通。從「證明沒有政策是什麼意思」開始,經過命名空間隔離、埠限制、FQDN,到 L7。驗收是同一顆 pod 對同一個服務,GET 通過而 POST 被擋。
    - **Day 9**:有了會被擋下的流量,才有東西可以在 Hubble 上觀察。

## 政策之前,先問「你是誰」

Kubernetes 的網路政策在心智模型上有一個陷阱:大家習慣用 IP 想事情,而 pod 的 IP 是會變的。

Cilium 不在規則裡放 IP。它把每顆 endpoint 的**標籤集合**雜湊成一個數字,叫 **security identity**,規則寫的是「哪個 identity 可以跟哪個 identity 講話」,BPF map 裡存的也是那個數字。這件事是今天其餘所有內容的地基,所以步驟 2 會先把它量清楚再寫任何一條規則。

今天走七步:

| 步驟 | 做什麼 |
|---|---|
| 1 | 復原環境,並重驗 Day 7 的 kube-proxy 驗收 |
| 2 | 證明「沒有政策」是什麼意思,並看懂 identity |
| 3 | default-deny 與命名空間隔離(**兩種寫法都失敗之後才找到能用的**) |
| 4 | L3/L4 埠限制 |
| 5 | FQDN egress——Day 4 那個 DNS 放大在這裡的長相 |
| 6 | **驗收:L7,GET 過而 POST 被擋** |
| 7 | `nsenter` 對照組——Day 6 的逃生門在這裡通不通 |

## 步驟 1: 復原,並重驗 Day 7 的驗收

Day 7 結束時留了一句話:kube-proxy 的節點選擇器修改撐過停機重啟**是單一觀察**,而它是 Day 7 驗收的一半,所以今天必須先重驗。

```console
$ kubectl -n kube-system get ds kube-proxy -o custom-columns=…
NAME         DES   CUR   RDY   SEL
kube-proxy   0     0     0     map[lab.local/no-kube-proxy:true]      ← 修改存活了(第二次)

$ kubectl get pods -A --no-headers | grep -c kube-proxy         → 0
$ (node-shell) nsenter -t 1 -m -p -- pgrep -a kube-proxy         → (沒有行程)
$ cilium-dbg status --verbose | grep -A2 KubeProxyReplacement
KubeProxyReplacement:   True
  Socket LB:            Enabled
  Socket LB Coverage:   Full
```

**兩次觀察不等於保證,但「受管控制面會在重建時把附加元件的 spec 蓋回去」這個假設,現在有兩次反例。**

順帶兩件 Day 7 已經記過、今天如期復現的事:每次停機重啟換的是**全新的 VM**(所以 nat 表天生就只有 16 行,這台從沒跑過 kube-proxy);以及 **Deployment 與 DaemonSet 會自己回來,裸 Pod 不會**。

## 步驟 2: 先證明「沒有政策」是什麼意思

不先做這一步,後面每一個「被擋了」都可以被質疑成「本來就不通」。

```console
### 同命名空間
client → svc-multiport:80(→8080)        PORT-8080
client → svc-multiport:90(→9090)        PORT-9090
client → svc-echo7 GET                  200
client → svc-echo7 POST                 200
### 出叢集
client → example.com                    200
### 跨命名空間（netlab-b/probe → netlab）
probe → svc-multiport:80                PORT-8080
probe → echo7 的 pod IP                 200      ← 連 Service 都不用經過
```

**最後那一行是重點**:跨命名空間打得到的不只是 Service,是 **pod IP 本身**。Kubernetes 的命名空間是 API 物件的分組,**不是網路邊界**。

### identity:標籤的函數,不是 pod 的函數

```console
$ kubectl get cep -A -o custom-columns=NS:…,NAME:…,ID:.status.identity.id,IP:…
NS         NAME                           ID      IP
netlab     client                         6874    10.244.0.78
netlab     echo7-…                        42793   10.244.0.212
netlab     multiport-…                    1029    10.244.0.104
netlab     svc-backend-…-h2hkx            53393   10.244.0.178
netlab     svc-backend-…-t5qzj            53393   10.244.0.20      ← 兩顆 pod 同一個號
netlab-b   probe                          17242   10.244.0.164
```

`53393` 那兩行是整個概念的縮影:**兩顆不同 IP 的 pod 共用同一個 identity,因為它們的標籤一樣。**

```console
$ cilium-dbg identity get 53393
53393   k8s:app=svc-backend
        k8s:io.cilium.k8s.namespace.labels.kubernetes.io/metadata.name=netlab
        k8s:io.cilium.k8s.policy.cluster=default
        k8s:io.cilium.k8s.policy.serviceaccount=default
        k8s:io.kubernetes.pod.namespace=netlab
```

只有五個標籤。而 pod 身上實際掛著的還有 `pod-template-hash` 與 `topology.kubernetes.io/*`——**那些不在 identity 裡**,Cilium 預設的標籤過濾器把它們排除了。

這不是細節:**如果 `pod-template-hash` 算數,同一個 Deployment 每次滾動更新都會生出一個新 identity**,政策要嘛跟著失效、要嘛 identity 表爆炸。

擴容驗證:`svc-backend` 從 2 顆長到 3 顆,第三顆的 identity 仍然是 53393。反過來改標籤會發生什麼,見[地雷 1](#mine-1)。

## 步驟 3: default-deny 與命名空間隔離

這一段的價值在於**兩種直覺寫法都失敗了,而且失敗的方式完全不同**——一種看得見([地雷 2](#mine-2)),一種看不見([地雷 3](#mine-3))。能用的寫法是這樣:

```bash
cat > default-deny-ingress.yaml <<'EOF'
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: netlab
spec:
  endpointSelector: {}                        # every endpoint in this namespace
  enableDefaultDeny: {ingress: true, egress: false}
  ingress:
    - {}    # an empty rule: puts the endpoints into ingress enforcement mode
            # while allowing no source. THIS is what makes it take effect —
            # enableDefaultDeny alone is rejected, and `ingress: []` loads but
            # enforces nothing. See mines 2 and 3.
EOF
kubectl apply -f default-deny-ingress.yaml
```

驗收要問 agent,不是問 `kubectl`([地雷 4](#mine-4)):

```console
$ cilium-dbg endpoint list -o json | …
680   1029    ingress   k8s:app=multiport,k8s:io.kubernetes.pod.namespace=netlab
2272  6874    ingress   k8s:app=client,…
62    17242   none      k8s:app=probe,k8s:io.kubernetes.pod.namespace=netlab-b   ← 隔壁不受影響

$ client → svc-multiport    curl: (28) 逾時
$ probe  → svc-multiport    curl: (28) 逾時
```

### 命名空間隔離:空的 selector 不是「任何人」

```yaml
  ingress:
    - fromEndpoints:
        - {}          # "any endpoint in THIS namespace"
```

```console
client(netlab)  → svc-multiport:80    PORT-8080     ✅
probe(netlab-b) → svc-multiport:80    逾時          ❌
probe(netlab-b) → multiport 的 pod IP  逾時          ❌   ← 繞過 Service 也沒用
```

CNP 是命名空間資源,Cilium 會自動替 `fromEndpoints` 的空 selector 補上「本命名空間」這個標籤。**這一條就是命名空間隔離的全部**,而它擋得住直接打 pod IP,因為判定的是 identity 不是位址。

### 丟包長什麼樣子

```console
$ cilium-dbg monitor --type drop
xx drop (Policy denied) flow … to endpoint 680, file bpf_lxc.c:2410,
    identity 6874->1029: 10.244.0.78:59028 -> 10.244.0.104:8080 tcp SYN     ×4
```

三個細節值得停一下:

1. **執行點在目的地 endpoint 的 `bpf_lxc`**,不是來源端——ingress 政策由被保護的那一顆執行。
2. **記的是 identity 對 identity**,位址只是附帶資訊。
3. **目的地已經是後端 pod 的位址,不是 ClusterIP**——socket LB 早在 `connect(2)` 就翻譯完了([Day 7 地雷 8](sprint2-day7-cilium-kubeproxy.md#mine-8)),政策看到的是後端。SYN 被丟四次(TCP 重送)就是呼叫端那個逾時的來源。

## 步驟 4: L3/L4 埠限制

```yaml
  ingress:
    - fromEndpoints:
        - matchLabels: {app: client}
      toPorts:
        - ports: [{port: "8080", protocol: TCP}]   # the endpoint's port, not the Service's
```

**在寬規則還沒移掉之前,這條窄規則一點作用都沒有**——那是[地雷 5](#mine-5),而且是網路政策的頭號事故形狀。移掉之後:

```console
client → :80(8080)        PORT-8080          ✅ 放行的埠通
client → :90(9090)        curl: (28) 逾時     ❌ 沒放行的埠不通
client → svc-backend:80   curl: (28) 逾時     ❌ 沒放行的目標不通
```

資料平面那一側:

```console
$ cilium-dbg bpf policy get 680           # 680 = multiport 這顆 endpoint
POLICY   DIRECTION   LABELS                     PORT/PROTO   PROXY PORT   BYTES   PACKETS
Allow    Ingress     reserved:host              ANY          NONE          968     12
Allow    Ingress     k8s:app=client             8080/TCP     NONE          974     12
                     k8s:…namespace=netlab
Allow    Egress      ANY                        ANY          NONE            -      -
```

整條政策就是這張表。**沒有 9090 這一列,所以 9090 被拒**——不是有一條 deny 規則,是**沒有 allow 條目**。

而 `BYTES/PACKETS` 是逐條計數器:跟 Day 7 用 iptables 計數器判定「誰真的做了那次翻譯」是同一招,這裡可以直接判定**哪一條規則真的被用到**。

`toPorts` 寫的是 **8080**(endpoint 的埠)而不是 Service 的 80。**政策掛在 endpoint 上,Service 的 port 對它不存在。**

最上面那行 `Allow Ingress reserved:host ANY ANY` 先擱著,步驟 7 會回來算帳。

## 步驟 5: FQDN egress——Day 4 那個 DNS 放大的第二次現身

[Day 4 地雷 4](sprint2-day4-falco-custom-rules.md#mine-4) 的教訓:`ndots:5` 讓一次 HTTP 請求變成六次 `connect()`,Falco 規則因此每分鐘噴 180 條誤報。FQDN 政策正是「不要手工維護 IP 清單」的正解,所以要問三件事:做得到嗎、代價是什麼、Cilium 在這裡怎麼看見 DNS。

```yaml
  egress:
    - toEndpoints:
        - matchLabels: {"k8s:k8s-app": kube-dns}
      toPorts:
        - ports: [{port: "53", protocol: ANY}]
          rules:
            dns: [{matchPattern: "*"}]    # without this there is no DNS proxy,
                                          # and toFQDNs never learns any address
    - toFQDNs:
        - matchName: "example.com"
```

```console
client → example.com       200
client → ifconfig.me       curl: (28) 逾時
client → www.google.com    curl: (28) 逾時
client → svc-multiport     curl: (28) 逾時      ← 叢集內也一起被擋了
client 的 dig example.com  <addr-a>       ← DNS 本身還通
```

**倒數第二行是實務上最容易出事的**:`egress` 區段一出現,這顆 endpoint 的整個出向就變成預設拒絕,包含它原本理所當然的叢集內流量。**FQDN 政策不是「加一條對外的允許」,是「把這顆 pod 的對外世界整個換掉」。**

### 它底下怎麼運作

```console
$ cilium-dbg bpf policy get 2272          # 2272 = client
Allow  Egress  k8s:k8s-app=kube-dns …   53/UDP    PROXY PORT 44427    34828 bytes
Allow  Egress  fqdn:example.com         ANY       NONE                16864 bytes
       reserved:world
```

兩層清清楚楚:**DNS 那條帶 `PROXY PORT`**——封包被導進 cilium-agent 內建的 DNS proxy;**`fqdn:example.com` 那條是動態產生的**,來源是 proxy 在回應裡看到的位址記錄。

```console
$ cilium-dbg fqdn cache list
Endpoint  Source  FQDN                TTL   IPs
2272      lookup  example.com.        30    <addr-b>,<addr-a>
2272      lookup  ifconfig.me.        30    <addr-c>
2272      lookup  www.google.com.     30    <addr-d>,…
```

**Cilium 不是去解析 `example.com` 存成靜態 IP,它是站在查詢路徑上等答案。** 所以沒有那條 `rules.dns` 就沒有 proxy、沒有 proxy 就沒人看見解析、`toFQDNs` 永遠是空清單——而失敗形狀是「政策看起來對,流量全被擋」。

那份快取裡有兩個**連線被擋掉**的域名,見[地雷 6](#mine-6)。

### 代價:一次 curl,DNS proxy 看見十次查詢

| # | 查詢名稱 | 結果 |
|---|---|---|
| 1–2 | `example.com.netlab.svc.cluster.local.` | 空 |
| 3–4 | `example.com.svc.cluster.local.` | 空 |
| 5–6 | `example.com.cluster.local.` | 空 |
| 7–8 | `example.com.<Azure 內部搜尋域>.` | 空 |
| 9–10 | `example.com.` | **有答案** |

第 7–8 那一組是受管平台額外塞進 `/etc/resolv.conf` 的搜尋域,所以放大係數比標準 Kubernetes 還多一輪。**十次查詢裡有八次註定是空的,而十次全部要走一趟使用者空間的 proxy。**

```text
                     mean     p50     p90     max
無 egress 政策       1.99 ms  1.61    1.85    7.56
FQDN 政策            4.35 ms  3.98    6.26    8.12
```

**p50 從 1.61 ms 變 3.98 ms,2.5 倍。**

跟 Day 4 對照很有意思:同一個 `ndots:5` 放大,在 Falco 那裡的代價是**每分鐘 180 條要人去看的誤報**,在這裡的代價是**每次解析多 2.4 毫秒的機器時間**。後者可以接受,前者不行——**同一個結構性問題,落在偵測工具上是人的成本,落在執行工具上是機器的成本。**

## 步驟 6: 驗收——L7,GET 過而 POST 被擋 {#step-6}

政策只在 L3/L4 的基礎上多三行:

```yaml
      toPorts:
        - ports: [{port: "8080", protocol: TCP}]
          rules:
            http:
              - method: "GET"
```

```console
$ curl -o /dev/null -w 'http_code=%{http_code} time_total=%{time_total}\n' http://svc-echo7.netlab/
http_code=200 time_total=0.003433

$ curl -X POST -d x=1 http://svc-echo7.netlab/
Access denied
http_code=403 time_total=0.003860

$ PUT → 403     DELETE → 403
```

**同一顆 pod、同一個 Service、同一個 8080/TCP,只有 HTTP 方法不同,結果不同。** 這是 L4 做不到的區分——在封包層看,GET 和 POST 是一模一樣的 TCP 連線。

### 客戶端看到的東西:L7 與 L4 的差別顯現的地方

```console
### L7 拒絕（POST → echo7）
$ curl -i -X POST -d x=1 http://svc-echo7.netlab/
HTTP/1.1 403 Forbidden
content-type: text/plain
server: envoy               ← 注意這一行
connection: close

Access denied

### L4 丟包（client → multiport:9090）
$ curl -v http://svc-multiport.netlab:90/
*   Trying 10.0.78.148:90...
* Connection timed out after 5002 milliseconds
curl: (28) Connection timed out after 5002 milliseconds
```

三個實務差別:

1. **失敗速度**:403 在 **3.9 毫秒**內回來;L4 丟包要等呼叫端的連線逾時(本例 5 秒,正式環境常設 30 秒以上)。**這直接決定事故時的執行緒與連線池耗盡速度。**
2. **可追查性**:403 帶著本文與 header,日誌裡是一筆應用層事件;L4 丟包在呼叫端看起來跟「對方掛了」「網路分區」「DNS 壞了」一模一樣,只有在節點上開 `cilium monitor` 才看得到 `Policy denied`。
3. **重試行為**:多數 HTTP 用戶端對 403 不重試、對逾時會重試。**同一條政策,L4 寫法會讓被擋的呼叫端持續重打,L7 寫法會讓它安靜下來。**

那個 `server: envoy` 有代價,見[地雷 7](#mine-7)。

### L7 的成本:量得到,而且訊號遠大於雜訊

[Day 7](sprint2-day7-cilium-kubeproxy.md) 對延遲的結論是「量了但不宣稱」,因為 run-to-run 雜訊比效果大。今天的條件好得多:**同一顆 endpoint、同一個呼叫端、同一個請求,唯一的變因是 `rules.http` 那三行在不在**。兩種政策交替各跑兩輪、每輪 200 次:

| 量測 | mean | p50 | p90 | p99 |
|---|---|---|---|---|
| L4 政策 run1 | 1.555 ms | 1.429 | 1.793 | 3.882 |
| L4 政策 run2 | 1.463 ms | **1.411** | 1.581 | 2.784 |
| L7 政策 run1 | 2.074 ms | 1.899 | 2.606 | 5.161 |
| L7 政策 run2 | 2.098 ms | **1.927** | 2.716 | 5.313 |

**組內雜訊 18–28 µs,組間差距 p50 +0.49 ms(+34%)、p99 +1.9 ms。訊號是雜訊的 20 倍以上,所以這次可以宣稱。**

資料平面那一側也對得起來:

```console
### L7 政策生效時
$ cilium-dbg bpf policy get 160         # 160 = echo7
Allow  Ingress  k8s:app=client   8080/TCP   PROXY PORT 12286
$ cilium-dbg status | grep Proxy
Proxy Status:  OK, … 1 redirects active on ports 10000-20000, Envoy: external

### 換成 L4 政策
Allow  Ingress  k8s:app=client   8080/TCP   PROXY PORT NONE
Proxy Status:  OK, … 0 redirects active
```

`PROXY PORT` 從 `NONE` 變成一個數字,是那 0.49 毫秒**可歸因的路徑差異**:封包不再是 BPF 查一次 map 就轉走,而是**進使用者空間、由 Envoy 解 HTTP、判定、再送出去**。沒有再往下做 profiling,所以只能說路徑變了、時間多了,不能拆出每一段各佔多少。

記憶體的增幅倒是很小:`cilium-envoy` 全程 13–14 Mi,開了 L7 政策、跑完 800 次請求之後還是 14 Mi。**在一顆 endpoint、一條 HTTP 規則的規模下沒有長。** 真正長的是 agent(Day 7 收工 140–153 Mi,今天做完全部實驗是 216 Mi)——跟 Day 7 的觀察一致:**本次量到的 agent 記憶體更像是隨叢集歷史累積,而不是隨當下負載起伏**——兩天加起來只有數小時,不足以當成通則。

## 步驟 7: `nsenter` 對照組——Day 6 的逃生門在這裡通不通

[Day 6](sprint2-day6-tetragon-enforcement.md#mine-1) 證明 Tetragon 的攔截是綁 cgroup 的,而 `nsenter` 換命名空間不換 cgroup,所以別的命名空間的特權 pod 可以鑽進受管 pod 做被禁的事而不被殺。今天對同一招問網路政策。

```console
=== 0) 攻擊者用自己的網路命名空間（hostNetwork: true）===
  GET  echo7                200
  POST echo7                200          ← L7 政策形同不存在
  multiport:9090            PORT-9090    ← client 都打不到的埠

=== 1) nsenter -t <client-pid> -n（鑽進 client 的 netns）===
  GET  echo7                200
  POST echo7                403          ← 被 client 的政策擋住
  multiport:8080            PORT-8080
  multiport:9090            curl: (28) 逾時   ← 被 client 的政策擋住

=== 2) nsenter -t <probe-pid> -n（鑽進另一個命名空間的 pod）===
  GET  echo7                curl: (28) 逾時
  multiport:8080            curl: (28) 逾時   ← 比原本更少權限
```

**第 1、2 組的答案跟 Day 6 完全相反**,原因見[地雷 8](#mine-8)。

**但第 0 組才是真正該擔心的**,見[地雷 9](#mine-9)——**攻擊者根本不需要 `nsenter`。**

## 誠實的差距

- **`ingressDeny` / `egressDeny` 沒有測。** [地雷 5](#mine-5) 的結論是「加規則永遠不會收緊」,而 Cilium 有一組 deny 規則可以表達「即使有人允許也要擋」。本課完全沒有碰那一層。
- **hostFirewall 沒有開。** [地雷 9](#mine-9) 是這座叢集最大的洞,而堵法(`hostFirewall.enabled=true` 加上叢集範圍政策管 `reserved:host`)是 Helm 層的資料平面變更,Day 9 還要用這座叢集,所以刻意沒做。**這個洞在本課從頭到尾是開著的。**
- **DNS 外洩通道沒有關。** 同樣是[地雷 6](#mine-6) 的結論:要關掉得把 `matchPattern` 收成明確清單,而那跟「FQDN 政策是為了不用手工維護清單」的初衷打架。本課沒有做這個取捨。
- **只有一顆 endpoint、一條 HTTP 規則。** L7 的 0.49 毫秒與 envoy 的 14 Mi 都是這個規模下的數字,不能外推到幾十條規則或高流量。
- **政策變更的傳播延遲只粗量過一次。** [地雷 1](#mine-1) 記到標籤變更造成的 identity 換號有 10–20 秒的窗口,但沒有系統性地量政策生效延遲。

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| 沒有政策時全通 | 同命名空間、跨命名空間、直接打 pod IP、對外全部可達 | 六條路徑全通(含跨命名空間打 pod IP) |
| identity 是標籤的函數 | 標籤相同的兩顆 pod 拿到同一個 identity;擴容第三顆仍相同 | 三顆 `svc-backend` 同為 `53393` |
| 政策真的在執行 | **agent 的 `endpoint list` 顯示 ingress 執行中**,而不是 `kubectl apply` 成功 | 三顆 endpoint 進入 ingress 模式,隔壁命名空間不受影響 |
| 命名空間隔離 | 跨命名空間連 Service **與 pod IP** 都不通 | 兩者皆逾時 |
| L3/L4 | 放行的埠通、未放行的埠不通,且 BPF policy map 裡沒有該埠的條目 | 8080 通 / 9090 逾時 / map 無 9090 列 |
| FQDN egress | 允許的名字通、其他名字不通 | `example.com` 200,其餘逾時 |
| **驗收:L7** | 同一顆 pod、同一個 Service、同一個埠,**GET 與 POST 結果不同** | **GET 200 / POST 403**,回應本文 `Access denied`、header `server: envoy` |
| L7 的成本可量化 | 組間差距明顯大於組內雜訊 | 雜訊 18–28 µs,差距 p50 +0.49 ms(+34%) |

## 地雷記錄

### 地雷 1:改一個標籤就換一個 identity,而且同一組副本不同步 {#mine-1}

**症狀**:給兩顆 `svc-backend` 加一個 `tier=extra` 標籤。

```console
$ kubectl -n netlab label pod -l app=svc-backend tier=extra --overwrite
$ kubectl get cep …                                （15 秒後）
svc-backend-…-h2hkx   53393        ← 還沒換
svc-backend-…-t5qzj   3065         ← 已經換了

$ cilium-dbg endpoint list
1389   …  53393   … waiting-for-identity     ← 過渡狀態
632    …  3065    … ready

（再 10 秒）兩顆都是 3065
```

**三件事同時發生**:identity 換號、換號有傳播延遲、而且同一個 Deployment 的副本**不是同時換的**。

**後果**:政策是綁在 identity 上的,所以**改 pod 標籤等於改它適用哪些政策**,而中間有一段兩顆副本適用不同政策的窗口(實測約 10–20 秒)。

**教訓**:把標籤當成純粹的 metadata(加個 `tier`、加個 `owner` 方便查詢)在 Cilium 叢集上不成立——**標籤是網路身分**。

### 地雷 2:`enableDefaultDeny` 單獨寫會被 CRD 退件 {#mine-2}

```console
$ kubectl apply -f default-deny-ingress.yaml
The CiliumNetworkPolicy "default-deny-ingress" is invalid:
* <nil>: Invalid value: "": "spec" must validate at least one schema (anyOf)
* spec.ingress: Required value
```

**這種失敗是好的失敗**:看得見、擋在 API 層、不會誤以為生效。

`enableDefaultDeny` 的設計目的其實是相反方向——把某個方向的隱含 default-deny **關掉**——它不是一條可以獨立存在的規則。把它跟[地雷 3](#mine-3) 並排看才知道自己有多幸運。

### 地雷 3:加上 `ingress: []` 就「成功」了,然後什麼都沒發生 {#mine-3}

```console
$ kubectl apply -f dd-a.yaml            # enableDefaultDeny + ingress: []
ciliumnetworkpolicy.cilium.io/default-deny-ingress created

$ kubectl get cnp -n netlab
NAME                   AGE   VALID
default-deny-ingress   8s    False        ← 只有這一欄透露真相

$ kubectl -n netlab get cnp default-deny-ingress -o yaml | tail -5
status:
  conditions:
  - message: rule must have at least one of Ingress, IngressDeny, Egress, EgressDeny
    status: "False"
    type: Valid

### 流量呢？
client → svc-multiport   PORT-8080     ← 完全沒變
probe  → svc-multiport   PORT-8080     ← 完全沒變
```

**`kubectl apply` 回 `created`、退出碼 0、沒有任何警告,而 agent 從頭到尾沒收下這條規則。**

**嚴重性在於失敗方向**:一條無效的網路政策不會讓流量停擺,它會讓流量照舊——**fail-open**。「我套了 default-deny」與「我以為我套了 default-deny」在 `kubectl get` 的預設輸出裡幾乎長一樣。

**修法**:CI 上要擋這個,檢查的是 `.status.conditions[?(@.type=="Valid")].status`,**不是 `kubectl apply` 的退出碼**。

### 地雷 4:`kubectl get cep` 的政策欄永遠是空的 {#mine-4}

```console
$ kubectl get cep -n netlab -o custom-columns=NAME:…,IN:.status.policy.ingress.enforcing
NAME       IN
client     <none>       ← 政策明明在執行
multiport  <none>
```

**根因**:`CiliumEndpoint` 的 `.status.policy` 在這個部署裡是空的——政策欄位預設不同步到 CRD,因為它很吵。

**要看資料平面的真實狀態只能問 agent**(`cilium-dbg endpoint list`)。

這跟 [Day 7 地雷 8](sprint2-day7-cilium-kubeproxy.md#mine-8) 是同一課的不同版本:**`kubectl` 看得到的物件狀態,跟 BPF map 裡的實際狀態,是兩件要分開驗證的事。**

### 地雷 5:把窄規則疊在寬規則上,寬的那條贏 {#mine-5}

**症狀**:寫了一條「只放行 8080」的規則,9090 照樣通。

```console
$ kubectl apply -f l3l4-port.yaml        # 只放行 client → multiport:8080
client → :80(8080)   PORT-8080
client → :90(9090)   PORT-9090        ← 一點都沒收緊
```

移掉原本那條「同命名空間全放行」之後才生效。

**根因**:CiliumNetworkPolicy(和 Kubernetes NetworkPolicy 一樣)是**白名單模型,多條規則取聯集**——官方文件的原話是:兩條規則存在時,較寬的那條所匹配的流量全部會被放行。

**「我補一條更嚴格的規則」這個動作在網路政策裡不存在。** 要收緊只能刪掉或改掉那條寬的。

**這在多人維護的命名空間裡是頭號事故來源**:A 寫了一條 `fromEndpoints: [{}]` 圖方便,B 之後寫的所有細緻規則全部是裝飾品——**而且 B 自己測的時候會全部「通過」**。

(想表達「即使有人允許也要擋」要用 `ingressDeny` / `egressDeny`,那是另一個層級,沒有測。)

### 地雷 6:被政策擋掉的域名,照樣被解析、照樣進快取 {#mine-6}

FQDN 快取裡有 `ifconfig.me` 和 `www.google.com`——**這兩個目標的連線是被擋掉的,但它們的 DNS 查詢成功了。** 因為 `matchPattern: "*"` 放行所有查詢,只有後續的 TCP 連線受 `toFQDNs` 管。

**後果分兩面:**

- **正面**:失敗形狀是「解析成功、連線逾時」,比「解析失敗」好追查。
- **負面**:**DNS 這條外洩通道沒有被關上。** 一顆被 FQDN 政策鎖住的 pod 仍然可以把資料編碼進域名查出去,而 Cilium 的 DNS proxy 會忠實轉發**並且寫進快取**。

要關掉這條路得把 `matchPattern` 收成明確清單,**而那正好跟「FQDN 政策是為了不用手工維護清單」的初衷打架**。這跟 [Day 4 地雷 5](sprint2-day4-falco-custom-rules.md#mine-5) 的取捨同型:**把誤報調到 0 的那一步,同時把外洩路徑讓了出去。**

另外 TTL 全部被夾成 **30 秒**(原始回應是 8 秒)。這代表**後端換 IP 之後最長 30 秒內舊 IP 仍然被允許**——FQDN 政策不是即時的。

### 地雷 7:L7 的拒絕會告訴對方「這裡有 Envoy」 {#mine-7}

`server: envoy` 這個 header 是 Cilium 插進路徑的那顆 proxy 自己加的。官方文件也明說:**與 L3/L4 不同,違反 L7 規則不會丟包,而是回一個 HTTP 403。**

**後果分兩層:**

- **對正常使用者**:一個看起來像應用回的 403,其實**應用完全沒收到那個請求**。查應用日誌會查不到——**沒有任何一行**,因為請求在 proxy 就被截了。追查方向會整個歪掉。
- **對攻擊者**:403 加 `server: envoy` 等於免費告訴他「這裡有 L7 網路政策、執行點是 Envoy」,而且可以用不同方法與路徑**逐一探測政策邊界**,每次探測只花 4 毫秒且不會逾時。L4 丟包不給這個資訊(每次探測要等逾時,而且結果跟服務不存在無法區分)。

**L7 政策把「拒絕」從一個沉默的網路事件,變成一個會回話、可被列舉的應用層事件。** 這在營運上大多是好事,但它確實是一個資訊揭露面,而預設是開著的。

### 地雷 8:`nsenter` 對 endpoint 範圍的政策不是繞過,是換一套政策來套自己 {#mine-8}

**攻擊者鑽進哪個網路命名空間,就繼承哪顆 pod 的政策,連 identity 都是被害者的。**

```console
identity 17242->1029: …  ← probe 的 identity
identity  6874->1029: …  ← client 的 identity
```

**根因**結構上很清楚:Cilium 的執行點是掛在 **veth 上的 tc BPF**,而 veth 屬於網路命名空間。你進了那個命名空間,你的封包就從那條 veth 出去,就撞上那條 veth 的 policy map。

Day 6 的 Tetragon 掛在 cgroup 上、`nsenter` 不動 cgroup,所以能繞;**Cilium 掛在網路命名空間上,而 `nsenter -n` 正是換命名空間,所以這一招在這裡是自投羅網**——鑽進權限更少的 pod,拿到的權限更少。

**但有一個防守方的損失**:因為丟包記在被害者的 identity 上,**光看 `cilium monitor` 分不出「client 自己違規」與「有人鑽進 client 的命名空間違規」**。網路政策的稽核軌跡**沒有行程身分**——那是 Tetragon 有而 Cilium 沒有的東西。

三套工具在這個情境剛好互補:**Falco 看得到 `nsenter` 這個行為、Tetragon 殺得掉行程(但會被繞)、Cilium 擋得住封包(但不知道是誰)。**

### 地雷 9:`Host firewall: Disabled` 之下,任何 hostNetwork pod 完全不受 CNP 管 {#mine-9}

每一顆 endpoint 的 policy map 第一行都是:

```console
$ cilium-dbg bpf policy get 680
Allow    Ingress     reserved:host      ANY      NONE      968     12
```

`ANY/ANY`、無條件,而且**不是任何一條政策寫出來的**——是 `Host firewall: Disabled`(預設)的結果。

**這條規則有一半是必要的**:節點自己出去的流量(kubelet、系統服務、節點探針)不受任何 CNP 管,否則第一條 default-deny 就會切斷節點自己的健康檢查。

**但同一條規則也讓任何一顆 `hostNetwork: true` 的 pod,不管在哪個命名空間、不管有沒有特權,完全穿透所有 CiliumNetworkPolicy。** 實測:它打穿了 L7 的方法限制、打穿了 L3/L4 的埠限制、打穿了命名空間隔離。

**對照組**:另一個命名空間的 `probe` 是同樣「別的命名空間的一顆 pod」,唯一差別是它不是 hostNetwork——**它什麼都打不到。**

**所以「誰能建 hostNetwork pod」這件事,在 Cilium 叢集上等價於「誰能豁免全部網路政策」。** 這是一個 RBAC 與 PodSecurity 的問題,不是網路政策的問題,但**它會讓網路政策的保證整個失效,而 `kubectl get cnp` 上看不出來**。

**堵法**是 `hostFirewall.enabled=true` 加上叢集範圍的政策管 `reserved:host`,官方文件對此有專章。本課刻意沒做(會動到資料平面設定,而後續還要用這座叢集)——**這是本課環境目前最大的一個洞,而且是開著的。**

## 帶得走的東西

- **在 Cilium 裡,標籤就是網路身分。** 規則寫的是 identity 對 identity,而 identity 是標籤集合的雜湊。所以擴容不影響政策(標籤沒變),但**改一個標籤等於改這顆 pod 適用哪些政策**,而且副本之間有十幾秒不同步的窗口。
- **網路政策只會放寬,不會收緊。** 多條規則取聯集,加一條更嚴格的規則不存在。一條圖方便的寬規則會讓之後所有細緻規則變成裝飾品,而寫細緻規則的人自己測的時候會全部通過。
- **一條無效的網路政策是 fail-open。** `kubectl apply` 成功、物件列得出來、流量照舊,真相只在 `.status.conditions` 的 `Valid` 欄位裡。**驗收要檢查那個欄位,不是退出碼。**
- **L7 拒絕與 L4 丟包對呼叫端是兩件完全不同的事。** 前者 4 毫秒回一個 403、不會被重試、日誌裡是應用層事件;後者要等連線逾時、會被重試、看起來跟「對方掛了」一模一樣。代價是每個請求多約 0.5 毫秒,以及一個會回話、可被逐一探測的執行點。
- **網路政策的保證,取決於「誰能建 hostNetwork pod」。** 預設設定下那類 pod 完全豁免所有 CNP,而這件事在任何一條政策的內容裡都看不出來。網路層的邊界,實際上是由 admission 層決定的。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[政策規則的基本語意](https://docs.cilium.io/en/stable/security/policy/intro/)** —— 地雷 5 的一手來源:官方明載政策是白名單模型,兩條規則並存時較寬的那條所匹配的流量全部放行。
- **[L7 政策](https://docs.cilium.io/en/stable/security/policy/layer7/)** —— HTTP 的 method 與 path 規則怎麼寫、為什麼要經過節點上的 Envoy,以及官方對「違反 L7 規則不會丟包,而是回 403」的說明,對得上[步驟 6](#step-6)。
- **[Host Firewall](https://docs.cilium.io/en/stable/security/host-firewall/)** —— 地雷 9 的堵法。官方也在這裡說明主機命名空間(含 hostNetwork pod)本來就不在一般網路政策的涵蓋範圍內,以及那個 `reserved:host` 特殊 endpoint。
- **[Pod 的 DNS 設定與 ndots](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/)** —— 步驟 5 那十次查詢的來源;同一個機制在 Day 4 是誤報的來源,在這裡是延遲的來源。

## 下一步

今天產生了兩種被擋下的流量:L4 丟包(沉默、呼叫端只看到逾時)與 L7 拒絕(403、應用日誌裡卻一行都沒有)。**後者尤其麻煩——請求在 proxy 就被截了,被保護的那個應用完全不知道有人來過。**

Day 9 換上 Hubble,問一個很直接的問題:**這些被擋下的東西,看得見嗎?** 而今天留下的三件事會一起被拿去問它——L7 拒絕在應用日誌裡是空白的、丟包記的是受害者的 identity 而沒有行程身分、以及 hostNetwork 那個穿透一切的洞。**一個監控工具能不能看見政策層最大的破口,是那一天最值得知道的答案。**

---

!!! quote ""
    Cilium 標誌為 Cilium 專案之官方資產(CNCF artwork),此處作社群教學用途。
