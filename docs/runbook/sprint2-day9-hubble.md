# Day 9: Hubble——用 flow 追查被擋下的流量，以及它記錯或記不到的地方

![Cilium 官方標誌](../assets/logos/cilium-icon-color.svg){ align=right width="95" }

假設同事來問你：某個服務對 `echo7` 發 POST，一直拿到 403，可是 `echo7` 的日誌一行都沒有，他想知道是誰在打、為什麼被擋。[Day 8](sprint2-day8-cilium-network-policy.md) 已經看過原因：L7 政策的拒絕是 Cilium 的 Envoy 回的，請求根本沒到應用，所以應用日誌查不到。要回答「誰在打」，得到網路層找紀錄。

這一章打開 Cilium 內建的觀測工具 Hubble，用它的 CLI 和網頁介面找到那筆被擋的請求，再拿 Day 8 留下的三種情況去測它：請求被 Envoy 截下、攻擊者鑽進別人的網路命名空間發請求、hostNetwork 的 pod 繞過全部政策。讀完之後，拿到 Hubble 上的一筆紀錄（或一張空表）時，你能判斷它能不能當證據：事件還在不在 Hubble 的緩衝區裡、記下的來源是不是真正發出請求的程式、繞過政策的流量會不會出現在你過濾的視圖裡。

## Hubble 從 Day 7 起就在記錄

Day 7 裝好 Cilium 之後，每一顆節點上的 cilium-agent 除了執行政策，也會把資料平面上發生的網路事件記下來。一筆紀錄叫一個 **flow**：哪個來源連到哪個目的、哪個埠、這個封包被放行還是丟掉。Hubble 就是讀取這些 flow 的工具。

在還沒做任何變更之前，進 cilium-agent 問一下狀態。`cilium-dbg` 是 agent 容器裡的除錯工具，這一章出現的 `cilium-dbg` 指令都要透過 `kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium-dbg …` 在 agent 容器裡執行，下面的區塊省略了前面這一段：

```console
### helm upgrade 之前,什麼都還沒改
$ cilium-dbg status | grep -i hubble
Hubble:   Ok   Current/Max Flows: 2285/4095 (55.80%), Flows/s: 19.36   Metrics: Disabled
```

`Hubble: Ok`，而且已經存了 2285 筆 flow。Cilium 的 Helm chart 裡 `hubble.enabled` 預設就是 `true`，所以 Day 7、Day 8 那兩天 agent 一直在記，只是沒有人去讀。這一章要加的是讀取的那一端，Helm 變更不會碰到 agent 記錄的方式。

`Current/Max Flows` 後面那個 4095 是這一章最要緊的數字，步驟 2 會回來量它。

## 步驟 1: 確認 Day 8 的環境還在

這一章接在 Day 8 之後，用同一座叢集、同一組政策。叢集如果停機重啟過，裸 Pod（`client`、`probe`、`nsenter-box`）不會自己回來，要照 Day 7、Day 8 的 manifest 重新 apply；Deployment 與 DaemonSet 會自己回來。

先確認 Day 7 換掉 kube-proxy 的修改還在：

```console
$ kubectl -n kube-system get ds kube-proxy -o custom-columns=…
kube-proxy   0   0   0   map[lab.local/no-kube-proxy:true]

$ cilium-dbg status --verbose | grep -A3 KubeProxyReplacement
KubeProxyReplacement:   True
Host firewall:          Disabled          ← 步驟 5 的探針 C 要用到這一行
  Socket LB:            Enabled
```

kube-proxy 的 DaemonSet 仍是 0 顆，`KubeProxyReplacement: True`。這個節點選擇器的修改改的是 AKS 受管的附加元件，AKS 升級版本時可能被蓋回去，所以每次升級後都要再查一次。輸出裡的 `Host firewall: Disabled` 先記著，步驟 5 會用到。

再確認 Day 8 的政策還在。下面的輸出只列出兩條，Day 8 另外還有一條 `multiport-8080-only`，步驟 3 打 `multiport:9090` 會被丟掉就是靠它，用 `kubectl get cnp -A` 確認你的叢集裡三條都在：

```console
$ kubectl get cnp -A
NAMESPACE   NAME                   AGE   VALID
netlab      default-deny-ingress   36m   True
netlab      echo7-get-only         31m   True
```

網路政策是存在 API server 裡的 CRD 物件，停機重啟後還在，不必重建。物件列得出來不代表政策在執行（見 Day 8 的[「`ingress: []` 套上去成功，流量卻完全沒變」](sprint2-day8-cilium-network-policy.md#mine-3)），所以用 Day 8 步驟 6 的 curl 指令（`-w 'http_code=%{http_code}'`）從 `client` 對 `echo7` 各打一次 GET 和 POST。下面是整理過的結果，`$` 後面的 `GET`、`POST` 是請求方法，不是要輸入的指令：

```console
$ GET  http_code=200
$ POST http_code=403   body: Access denied   header: server: envoy
```

GET 通過、POST 拿到 Envoy 回的 403，跟 Day 8 結束時一樣。這一章要在 Hubble 上找的，就是這筆 POST。

## 步驟 2: 加上 relay 與 UI

flow 存在每顆節點的 agent 裡，要從叢集外讀，還需要兩個元件：

- **hubble-relay**：一個 Deployment，向每顆節點的 agent 要 flow，合成一個叢集層級的讀取端點。少了它，CLI 只看得到你直接連上的那一顆節點。
- **hubble-ui**：網頁介面，本身不存資料，畫面上的東西都是向 relay 要來的。

下圖把這幾個元件由上往下排，最上面是你的電腦，最下面是節點：

![你的電腦上的 hubble CLI 經 port-forward 4245 連到 hubble-relay，瀏覽器經 port-forward 12000 連到 hubble-ui，hubble-ui 再向 relay 要 flow；relay 透過 gRPC 4244 向每顆節點上的 cilium-agent 讀取，agent 把每個事件寫進預設 4095 筆、滿了會蓋掉最舊資料的環狀緩衝區](../assets/diagrams/s2d9-hubble-parts.svg)

照著箭頭從上往下看：CLI 和瀏覽器都不直接碰節點，一律經過 relay；真正存資料的只有最下面那塊緩衝區。本課的叢集只有一顆節點，所以 relay 跨節點合併資料這件事，這一章沒有驗證到。

Helm values 用 Day 7 那份 values 檔，把下面這一段附加在檔案最後，另存成 `cilium-values-hubble.yaml`：

```yaml
hubble:
  enabled: true
  relay:
    enabled: true
  ui:
    enabled: true
```

```console
$ helm upgrade cilium cilium/cilium --version 1.20.0 -n kube-system -f cilium-values-hubble.yaml
Release "cilium" has been upgraded.  REVISION: 3
```

接著看 Cilium 自己的 pod 有沒有因為這次升級重啟：

```console
$ kubectl -n kube-system get pods -o custom-columns=N:…,RESTARTS:…
cilium-mvrd5           0
cilium-envoy-xxbzc     0
```

cilium-agent 和 cilium-envoy 的重啟次數都是 0。這次變更只多了兩個讀取端的 Deployment，負責執行政策的資料平面沒有被動到。Day 7 開 kube-proxy replacement 時改的是 agent 載入的 BPF 程式，風險完全不同；Day 3 的 Falco 要裝核心驅動、Day 5 的 Tetragon 要換 agent，也都比這次重。

### relay 和 UI 的資源設定

Day 3、5、7 都檢查過 chart 有沒有替 pod 設 `resources` 和 `priorityClassName`。這次的結果是：chart 沒有替 relay 和 UI 設 requests，也沒有設 `priorityClassName`，所以兩者的 QoS 是 BestEffort、priority 是 0；同一張 chart 裡的 agent 和 envoy 則是 `system-node-critical`。節點資源吃緊時，kubelet 先驅逐 BestEffort 的 pod，所以節點忙到資源不夠時，Hubble 的讀取端可能先被移走，需要時自己補 `resources` 和 `priorityClassName`。實際用量很小：relay 加 UI 合計 11m CPU、48Mi 記憶體。

### 緩衝區留得住多久 {#buffer-depth}

回到開頭那個 4095。agent 把 flow 存在一個**環狀緩衝區**裡：容量固定，滿了就用新的蓋掉最舊的，不寫進磁碟，也不送到別處。容量由 `hubble.eventBufferCapacity` 決定，而且是每顆節點各一份。

升級後再問一次 agent：

```console
$ cilium-dbg status | grep -i hubble
Hubble:   Ok   Current/Max Flows: 2508/4095 (61.25%), Flows/s: 19.49
```

`Flows/s` 是每秒寫進多少筆。本課的叢集只有一顆節點、八顆 pod、沒有業務流量，每秒仍有將近 20 筆，從上面的六成填到滿只花了約一分半鐘，從節點開機算起約 7 分鐘就滿了。滿了以後，數字會一直停在 4095，這時要問的是「最舊的那一筆離現在多久」，也就是緩衝區實際留得住多長的歷史：

| 情境 | **歷史深度** |
|---|---|
| 閒置（八顆 pod，無業務流量） | **3 分 02 秒** |
| 加一顆每 5 秒三個請求的 pod | **2 分 24 秒** |

只多一顆每 5 秒發三個請求的 pod，可回查的時間就少了約五分之一。照同樣的算法，一顆每秒處理幾百筆 flow 的節點，4095 筆只夠十幾秒。

這個數字決定了 Hubble 在你的事故流程裡能做什麼：

- 預設設定下，Hubble 適合看「現在正在發生的事」。等你收到告警、連上叢集、開好 port-forward，事件多半已經被蓋掉了，事後回查不能靠它。
- 要拿來事後回查，就得把 flow 送出節點：`hubble.export` 把逐筆 flow 寫成節點上的檔案；`hubble.metrics` 送到 Prometheus，但那是聚合後的數字，不是逐筆 flow，查不到某一次請求。兩者都是另外的 Helm 設定和儲存成本，本課沒有開，所以送出去之後能留多久、要花多少，這一章沒有答案。
- `Current/Max Flows` 到 100% 不代表健康，代表新的 flow 正在蓋掉舊的。

chart 1.20.0 的 `values.yaml` 裡，這個參數是一行註解掉的 `# eventBufferCapacity: "4095"`，所以 `helm show values` 只看得到這行註解；沒有另外設定時就是 4095，跟實測一致。要確認自己叢集的容量，看 `cilium-dbg status` 的 `Current/Max Flows` 斜線後面的數字，升級 Cilium 或改了這個參數之後再看一次。

## 步驟 3: 讀懂三種 flow

先把 relay 接到本機。本機要有 `hubble` CLI（本課用 1.19.4，連 1.20.0 的 relay 沒有問題）：

```console
$ kubectl -n kube-system port-forward svc/hubble-relay 4245:80 &
$ export HUBBLE_SERVER=localhost:4245
$ hubble status
```

接著用 `hubble observe --follow` 盯著 `netlab` 命名空間，從 `client` 依序發三個請求：一次會放行的 GET、一次會被 L7 政策擋下的 POST、一次打 `multiport:9090` 的 L4 丟包。每一行是一筆 flow，最後一欄是 **verdict**，也就是 Cilium 對這個事件的判定：`FORWARDED`（轉送）、`DROPPED`（丟掉）、`REDIRECTED`（轉給 Envoy 這類 proxy）。以 `policy-verdict` 開頭的行是 Cilium 套用政策時記下的事件，後面的 `ALLOWED`／`DENIED` 是政策判定的結果，冒號後面的 `none` 這一章用不到。

### 一次成功的 GET

```console
$ hubble observe --follow --namespace netlab -o compact
09:27:35.116  client:52314 (ID:6874) -> echo7:8080 (ID:42793)  policy-verdict:none … ALLOWED (SYN)
09:27:35.116  client:52314 -> echo7:8080  to-proxy      FORWARDED (SYN)
09:27:35.117  client:52314 -> echo7:8080  http-request  FORWARDED (HTTP/1.1 GET http://svc-echo7.netlab/)
09:27:35.117  10.244.0.69:48550 -> echo7:8080  to-endpoint FORWARDED (SYN)    ← 第二段:proxy → 應用
09:27:35.117  10.244.0.69:48550 -> echo7:8080  to-endpoint FORWARDED (ACK, PSH)
09:27:35.118  client:52314 <- echo7:8080  http-response FORWARDED (HTTP/1.1 200 1ms)
```

這裡只節錄了幾行，完整的一次 `curl` 在 Hubble 上有 15 筆。看第二行的 `to-proxy`：連線先被送進 Envoy，也就是 Day 8 [步驟 6](sprint2-day8-cilium-network-policy.md#step-6) 那條 `PROXY PORT 12286` 在 Hubble 上的樣子。再看帶箭頭註解的那一行，來源換成 `10.244.0.69`，這是 Envoy 自己發出、轉給 `echo7` 的第二條連線。客戶端和應用各自只看得到其中一段，兩段同時出現的地方只有 Hubble。

### 一次被 L7 擋掉的 POST

```console
09:27:35.125  client:52324 -> echo7:8080  policy-verdict:none … ALLOWED (SYN)   ← L4 放行
09:27:35.125  client:52324 -> echo7:8080  to-proxy      FORWARDED (SYN)
09:27:35.126  client:52324 -> echo7:8080  http-request  DROPPED (HTTP/1.1 POST http://svc-echo7.netlab/)
09:27:35.126  client:52324 <- echo7:8080  http-response FORWARDED (HTTP/1.1 403 0ms)
```

這次完整只有 10 筆，而且找不到任何 `10.244.0.69 -> echo7` 的行。下圖把兩次請求並排：

![上半是放行的 GET：client 連到 cilium-envoy（10.244.0.69）是第一段，Envoy 再連到 echo7 是第二段，Hubble 上兩段都有。下半是被 L7 政策擋下的 POST：client 只連到 cilium-envoy，Envoy 直接回 403，沒有轉送給 echo7；這筆 403 是 Envoy 產生的，Hubble 卻記在 echo7 名下](../assets/diagrams/s2d9-two-segments.svg)

我們可以從圖上看到兩件事。第一，被擋的 POST 少了整個第二段，請求停在 Envoy，`echo7` 沒有收到；步驟 5 的探針 A 會用這個差別當證據。第二，那筆 403 在 Hubble 上記成 `client <- echo7` 的 `http-response`，看起來像 `echo7` 回的，實際上是 Envoy 產生的。

再看 POST 的第一行：同一條連線，L4 的判定是 `ALLOWED`，到 L7 才是 `DROPPED`。如果儀表板只統計「被拒絕的連線」，這次違規會被算成一條正常的連線。

### 一次 L4 丟包

```console
09:27:35.132  client:57822 <> multiport:9090  policy-verdict:none … DENIED (SYN)
09:27:35.132  client:57822 <> multiport:9090  Policy denied DROPPED (SYN)
09:27:36.177  …（TCP 重送,同樣兩筆）×3
```

L4 丟包沒有回應，客戶端只能重送，所以同樣的兩行每隔一秒出現一次。用 `-o json` 印出完整欄位，可以看到丟包的原因：

```json
{ "verdict": "DROPPED", "Type": "L3_L4",
  "drop_reason": 133, "drop_reason_desc": "POLICY_DENIED" }
```

`drop_reason_desc` 是 **drop reason**，說明封包為什麼被丟。compact 輸出裡的 `Policy denied` 就是它。被 L7 擋下的那筆 POST 沒有這個欄位，下一步在 UI 上會看到後果。

## 步驟 4: 在 UI 上找到那筆 POST，再用 CLI 印出來

### 先讓緩衝區裡有東西

緩衝區只留兩三分鐘，打開 UI 時如果沒有人在發流量，畫面會是一張空表。所以先放一顆持續發請求的 pod，每 5 秒發三個請求，剛好對應步驟 3 的三種 flow。它掛 `app: client` 標籤，Day 8 寫給 `client` 的政策會一樣套到它身上：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: flowgen
  namespace: netlab
spec:
  replicas: 1
  selector:
    matchLabels: {app: client, role: flowgen}
  template:
    metadata:
      labels: {app: client, role: flowgen}
    spec:
      containers:
        - name: gen
          image: nicolaka/netshoot:latest
          command: ["/bin/sh", "-c"]
          args:
            - |
              while true; do
                curl -s -o /dev/null -m 5 http://svc-echo7.netlab/ || true
                curl -s -o /dev/null -m 5 -X POST -d ping=1 http://svc-echo7.netlab/ || true
                curl -s -o /dev/null -m 3 http://svc-multiport.netlab:90/ || true
                sleep 5
              done
          resources:
            requests: {cpu: 5m, memory: 16Mi}
            limits: {memory: 64Mi}
```

```console
$ kubectl apply -f flowgen.yaml
```

它多了一個 `role: flowgen` 標籤，所以 identity 跟 `client` 不同（Day 8 教過，identity 是由整組標籤決定的）；但政策的 `fromEndpoints` 只要求含有 `app: client`，照樣選得到它。

### UI 這一半

把 UI 也接到本機，網址要用 `?namespace=` 帶命名空間：

```console
$ kubectl -n kube-system port-forward svc/hubble-ui 12000:80 &
```

```text
http://localhost:12000/?namespace=netlab
```

直接打 `http://localhost:12000/netlab` 會得到 404，打根目錄只會看到選命名空間的頁面，見[疑難排解：UI 網址打 `/netlab` 得到 404](#mine-3)。

畫面上半是 **service map**（服務圖）：Hubble 把緩衝區裡的 flow 按「誰連到誰的哪個埠」合併成一條條線；下半是逐筆的 flow 表。

![Hubble UI 的服務圖與 flow 表：echo7:8080 是紅色虛線（GET 過、POST 擋），multiport:9090 是紅色實線（全擋）](../assets/screenshots/sprint2-day9/05-service-map-l7-dashed-vs-l4-solid.png)

服務圖上兩條線的樣子就帶著資訊：`client → echo7:8080` 是紅色虛線，表示這條線上有放行也有被擋的流量；`client → multiport:9090` 是紅色實線，表示全部被擋。

在 flow 表裡找到 L7 info 欄寫著 `→ POST / 0ms` 的那一列，點開它：

![Flow Details 側欄：Verdict 是 dropped，而 Drop reason 寫著 Unknown drop reason](../assets/screenshots/sprint2-day9/06-flow-detail-l7-unknown-drop-reason.png)

右側的 Flow Details 寫著 `Verdict: dropped`，就是那筆被 L7 政策擋下的 POST。它下面的 `Drop reason` 卻寫 `Unknown drop reason`。對照一筆 L4 丟包的側欄：

![同一個側欄，L4 被擋的那筆寫的是 Policy denied，還帶 TCP flags](../assets/screenshots/sprint2-day9/07-flow-detail-l4-policy-denied.png)

同一個面板，L4 那筆寫得出 `Policy denied`，L7 那筆寫不出原因，因為步驟 3 看過的 `drop_reason` 欄位在 L7 的 flow 上根本不存在。這會影響你怎麼分類和告警，見[疑難排解：UI 上 L7 被擋的那筆寫 Unknown drop reason](#mine-2)。

側欄裡有判定、丟棄原因、identity、標籤、IP、埠、流量方向，但沒有 HTTP 的方法、URL 或 header；表格的 L7 info 欄也只有 `→ POST / 0ms`。想知道那次請求的內容，要回到 CLI。

### CLI 這一半

用三個過濾條件把範圍縮到「`netlab` 裡、HTTP 層、被丟掉的」：

```console
$ export HUBBLE_SERVER=localhost:4245
$ hubble observe --namespace netlab --protocol http --verdict DROPPED --last 500 -o compact
Aug  7 13:02:19.180: netlab/flowgen-…:39002 (ID:51695)
    -> netlab/echo7-…:8080 (ID:42793)
    http-request DROPPED (HTTP/1.1 POST http://svc-echo7.netlab/)
```

這裡印出的是 `flowgen` 發的 POST。改用 `-o json` 印出 `client` 那一筆的完整內容（節錄）：

```json
{
  "verdict": "DROPPED",
  "Type": "L7",
  "source": {"identity": 6874, "namespace": "netlab", "pod_name": "client",
             "labels": ["k8s:app=client", …]},
  "destination": {"identity": 42793, "pod_name": "echo7-…",
                  "workloads": [{"name": "echo7", "kind": "Deployment"}]},
  "l7": {"type": "REQUEST",
         "http": {"method": "POST", "url": "http://svc-echo7.netlab/",
                  "protocol": "HTTP/1.1", "headers": [...]}},
  "traffic_direction": "INGRESS"
}
```

`Type: L7`、來源 pod、目的 Deployment、HTTP 方法和 URL 都在這裡。開場那位同事的問題，到這裡可以回答一半：被擋的是 `client` 發的 POST，原因要對照 Day 8 的 `echo7-get-only` 政策。另一半是「`client` 真的是發出請求的那支程式嗎」，步驟 5 的探針 B 會回答。

`l7.http.headers` 這一欄存的是 header 原文，包括認證用的 token，見[疑難排解：flow 裡看得到 `Authorization` 的原文](#mine-4)。

## 步驟 5: 拿 Day 8 的三種情況測 Hubble

### 探針 A：被 L7 擋下的請求，應用真的沒收到 {#probe-a}

Day 8 的[「應用日誌查不到被拒絕的請求」](sprint2-day8-cilium-network-policy.md#mine-7)說應用日誌查不到被擋的請求，那時是從「請求在 proxy 就被截下」推論出來的。這次直接測：從 `client` 打三次 POST、兩次 GET，前後比對 `echo7` 的日誌行數，一行都沒有增加。

不過這個測試的對照組不完整。`echo7` 跑的程式預設不記存取日誌，所以連成功的 GET 也沒有留下紀錄，光看日誌不能證明「只有被擋的看不到」。

證據要回到步驟 3 那張圖的兩段連線：

| | `client → proxy` 那一段 | `proxy → echo7` 那一段 |
|---|---|---|
| GET（放行） | 有 | **有**（5 筆） |
| POST（被擋） | 有 | **完全沒有** |

日誌只能告訴你「沒看到」；Hubble 上第二段整段不存在，說明 Envoy 沒有把請求轉給 `echo7`。被 proxy 截下的請求，客戶端和應用各自只看得到一半，要看全貌只能來 Hubble。

這裡也要記住步驟 3 的第二個觀察：那筆 403 在 Hubble 上記成 `<- netlab/echo7-… http-response FORWARDED`。看報表的人會以為是 `echo7` 回了 403，但 `echo7` 從頭到尾沒收到請求。

### 探針 B：攻擊者發的請求，被記在 client 名下 {#probe-b}

Day 8 示範過，攻擊者可以用 `nsenter` 鑽進 `client` 的網路命名空間發請求。這次讓 `client` 自己發一次 POST，再讓攻擊者鑽進去發一次，兩次都打 pod IP、都是 POST：

```console
$ kubectl -n netlab exec client -- curl -X POST -d who=client   http://<echo7-pod-ip>:8080/
client POST=403

$ kubectl -n attacker exec nsenter-box -- nsenter -t <client-pid> -n -- \
    curl -X POST -d who=attacker http://<echo7-pod-ip>:8080/
attacker-in-client-netns POST=403
```

`<echo7-pod-ip>` 用 `kubectl -n netlab get pod -o wide` 查 `echo7` 那一列的 IP。`<client-pid>` 是 `client` 那顆 pod 的行程在節點上的 PID，找法跟 Day 8 一樣：在攻擊者 pod 裡逐一 `nsenter -t <pid> -n` 進去，比對 eth0 的位址和 `client` 的 pod IP。

兩筆 flow 在 Hubble 上並排：

```console
time      : …09:29:50.541Z            time      : …09:29:53.168Z
  source  : netlab/client               source  : netlab/client
  identity= 6874                        identity= 6874
  IP= 10.244.0.191                      IP= 10.244.0.191
  labels  : ['k8s:app=client']          labels  : ['k8s:app=client']
  行程欄位: 無                           行程欄位: 無
```

這是從兩筆 flow 的 JSON（`hubble observe --protocol http -o json`）挑出來源相關欄位並排整理的結果。除了時間，每一欄都一樣。完整 JSON 裡唯一不同的是 HTTP header 中的 `Content-Length`（10 對 12），那只是 `who=client` 和 `who=attacker` 長度不同，不能拿來分辨是誰發的。整份 flow JSON 的頂層欄位裡，沒有任何一個和行程、PID、cgroup 或執行檔有關。

Day 8 的說法是「光看丟包分不出誰違規」。在 Hubble 上，攻擊者的請求被記成 `netlab/client` 發的，UI 上也標成 `client`，flow 裡沒有任何欄位可以標示來源不確定。只根據這筆紀錄寫事故報告，結論會是「`client` 發了這個請求」。

把這個情境放回本 sprint 用過的四套工具：

| | 看得到 `nsenter` 這個動作 | 擋得住這個封包 | 說得出是**誰**做的 |
|---|---|---|---|
| Falco（Day 3–4） | ✅ | ❌ | ✅ 行程 |
| Tetragon（Day 5–6） | ✅ | ✅ 但被繞 | ✅ 行程 |
| Cilium 政策（Day 8） | ❌ | ✅ 繞不過 | ❌ |
| **Hubble（Day 9）** | ❌ | — | ❌ **而且會給錯名字** |

Hubble 記的是網路 flow，不是行程。要知道一條 flow 是哪個行程發的，得對照 Falco 或 Tetragon 這類看得到行程的資料，而本 sprint 的四套工具之間沒有把兩種資料接起來的地方。

### 探針 C：hostNetwork 的繞過，在 Hubble 上像正常轉發 {#probe-c}

Day 8 的[「同節點的 hostNetwork pod 仍能連到受限服務」](sprint2-day8-cilium-network-policy.md#mine-9)：在本課保留本機 host 預設放行的環境，`hostNetwork: true` 的 pod 能連到同節點上受本章政策保護的目標。步驟 1 的 host firewall 也是 `Disabled`，但那個開關不能單獨解釋 endpoint 的放行。這次照 Day 8 步驟 7 第 0 組的做法：`nsenter-box` 本身就是 hostNetwork 的 pod，不需要 `nsenter`，直接在它裡面用 curl 對 `echo7` 發 GET 和 POST、再連 `multiport` 的 9090 埠。結果跟 Day 8 一樣，POST 拿到 200、9090 連得上。發請求之前先記下當時的時間，下面過濾條件裡的 `<mark>` 就換成這個時間（`--since` 也接受 `5m` 這類相對時間）。

接著用維運最常用的三個過濾條件找：

```console
### 1) 維運找「哪個命名空間在亂打」會這樣濾
$ hubble observe --namespace attacker --since <mark>
（空）

### 2) 找違規會這樣濾
$ hubble observe --verdict DROPPED --since <mark>
（空）                    ← 繞過的流量記成 ALLOWED / FORWARDED

### 3) 找 HTTP 層的事會這樣濾
$ hubble observe --protocol http --since <mark>
0 筆                      ← 沒經過 Envoy 就沒有 L7 事件,那次 POST 在 HTTP 視圖裡不存在
```

三個都找不到。拿掉所有過濾、只按時間範圍列出來，才看得到那幾筆，而它們的來源欄位是：

```json
"source": { "identity": 1, "labels": ["reserved:host"] }
```

沒有 pod 名稱、沒有命名空間，只有 `reserved:host`。這是 Cilium 給節點本身的保留身分，identity 固定是 1；hostNetwork 的 pod 用的是節點的網路，所以 Cilium 把它的流量當成節點的流量。來源 IP 是 `10.244.0.69`，再問 agent 一次這個位址是誰：

```console
$ cilium-dbg status | grep -i "proxy status"
Proxy Status:   OK, ip 10.244.0.69, 1 redirects active on ports 10000-20000
```

`10.244.0.69` 也是 Cilium 的 Envoy proxy 用的位址，步驟 3 那個第二段的來源就是它。下圖把兩種來源放在一起：

![左邊兩個來源：cilium-envoy 把放行的 GET 轉給 echo7，以及 hostNetwork 的攻擊者 pod attacker/nsenter-box；兩者在 Hubble 上記下的來源都是 10.244.0.69、identity 1、reserved:host，再連到 echo7。下方三個常用過濾器 --namespace attacker、--verdict DROPPED 都是空，--protocol http 是 0 筆](../assets/diagrams/s2d9-host-bypass.svg)

照著圖從左往右看：Envoy 的合法轉發和攻擊者的繞過，在 Hubble 上是同一個 IP、同一個 identity、同一組標籤。而且繞過的流量判定是 `ALLOWED`／`FORWARDED`，不會出現在任何找違規的視圖裡。

這些流量其實有經過 `echo7` 的 endpoint 政策，也有判定：不加過濾列出來時，那幾筆是 `(host) -> echo7 … policy-verdict:none … ALLOWED`。原因在 Day 8 看過的 policy map：這次環境保留本機 host 的預設放行，每顆 endpoint 的 policy map 第一行是 `Allow Ingress reserved:host`，對 `reserved:host` 無條件放行。所以判定是 `ALLOWED`，不是 `DROPPED`。被繞過的是你寫的那些規則，不是 Hubble 的記錄；Hubble 照實記下了這次放行，只是放行本身就是那條無條件規則造成的。

要控制本機 host 到 endpoint 的連線，Day 8 區分了 `--allow-localhost=policy` 搭配 endpoint 政策，以及另行管理主機流量的 host firewall。應先依流量路徑選擇設定、保留必要連線，再驗證拒絕結果與 Hubble 紀錄。本課沒有實作這些修法，不能保證只開一個旗標就會出現 `DROPPED`。

## 步驟 6: service map 適合回答什麼

步驟 4 的服務圖，資料就是緩衝區裡的 flow。UI 沒有匯出按鈕，所以本課用 `hubble observe --last 4000 -o json` 取出最近 4000 筆 flow，再用一小段腳本按「來源、目的、埠」加總，得到跟服務圖一樣的內容。腳本沒有放進本章，你可以用自己習慣的工具（例如 `jq`）做同樣的加總。下面節錄其中幾列，完整結果還有其他 `reserved:host` 開頭的列：

```console
SOURCE                      DESTINATION                        PORT    FLOWS  VERDICTS
reserved:host               reserved:world                       80      784  FORWARDED=784
reserved:host               kube-system/metrics-server         4443      364  FORWARDED=364
netlab/flowgen              netlab/echo7                       8080      204  REDIRECTED=34, FORWARDED=153, DROPPED=17
reserved:host               kube-system/hubble-relay           4222      136  FORWARDED=136
netlab/flowgen              netlab/multiport                   9090      106  DROPPED=106
```

先看它擅長的事。`flowgen → echo7:8080` 那一列同時有 `FORWARDED=153` 和 `DROPPED=17`，代表這條線上有放行也有被擋的流量；`multiport:9090` 那一列全部是 `DROPPED`，代表那條政策確實在擋，而且持續有流量被它擋下。只讀政策 YAML 看得到規則，看不到實際發生了什麼。想知道「這條規則刪掉會不會出事」，從這裡開始查最快。

再看它容易誤導的地方：

1. **只有最近有流量的東西才在圖上。** 這份結果裡沒有 `client`、`probe`、`svc-backend`（Day 7 建的測試服務），它們都還在叢集裡，只是最近兩分鐘沒發流量。服務圖是最近的連線紀錄，不是完整的拓撲；把它當拓撲用，可能會刪掉一個每天只跑一次的批次工作需要的規則。「最近」有多近，由步驟 2 量到的緩衝區深度決定。
2. **節點自己的流量佔大多數。** 排第一的是節點對外的流量，完整結果的前十列裡有八列來源是 `reserved:host`。應用之間的流量反而是少數。
3. **觀測工具自己也在圖上。** relay、UI、metrics-server 都佔了好幾列，它們的流量也在消耗同一個只有 4095 筆的緩衝區。
4. **`reserved:host` 底下什麼都混在一起。** 探針 C 的繞過流量都併進這一列，和 Envoy 的合法轉發混在一起，判定全是 `FORWARDED`。在服務圖上，那次繞過看起來就是節點在正常工作。

## 清理

`flowgen` 會一直發請求、佔用緩衝區，用完就刪掉，並關掉前面放到背景執行的兩個 port-forward（relay 的 4245 和 UI 的 12000）：

```console
$ kubectl -n netlab delete deploy flowgen
```

## 疑難排解

### UI 的 flow 表是空的，或找不到幾分鐘前的事件 {#mine-1}

**原因**：flow 存在每顆節點固定 4095 筆的環狀緩衝區裡，滿了就蓋掉最舊的；在本課幾乎沒有流量的叢集上也只留得住兩三分鐘。量法和影響見[步驟 2 的「緩衝區留得住多久」](#buffer-depth)。

**修法**：打開 UI 前先讓叢集裡有持續的流量（步驟 4 的 `flowgen`）；要事後回查，就得把 flow 匯出到儲存，本課沒有驗證這部分。

### UI 上 L7 被擋的那筆寫 Unknown drop reason {#mine-2}

**症狀**：Flow Details 寫著 `Verdict: dropped`，`Drop reason` 卻是 `Unknown drop reason`；L4 丟包的同一個欄位寫得出 `Policy denied`。

**原因**：L4 丟包的 JSON 帶著完整的原因：

```json
{ "verdict": "DROPPED", "Type": "L3_L4", "drop_reason": 133, "drop_reason_desc": "POLICY_DENIED" }
```

L7 拒絕的 JSON 裡，這兩個欄位整個不存在：

```console
drop_reason      : (不存在)
drop_reason_desc : (不存在)
```

`verdict: DROPPED` 和 `Type: L7` 都在，只是沒有 `drop_reason`，UI 就照實寫成「不知道」。

**影響與修法**：

- 追查時，L4 丟包可以按原因分類統計，L7 拒絕全部落在同一個「不知道」裡；是方法、路徑還是 header 不符合，flow 上沒有寫，要回去對照政策。
- 告警規則如果按 drop reason 分流，會漏掉全部的 L7 拒絕，而 L7 拒絕代表有人在打你明確禁止的介面。要抓它，改用 `Type: L7` 或 `--protocol http --verdict DROPPED`（步驟 4 的 CLI 那一半）來篩。

### UI 網址打 `/netlab` 得到 404 {#mine-3}

**症狀**：把命名空間放在路徑裡，`http://localhost:12000/netlab` 得到：

```text
Unexpected Application Error!
404 Not Found
```

只打根目錄，則只會看到一個「Choose namespace」下拉選單，沒有服務圖也沒有 flow 表。

**原因**：UI 用查詢參數接收命名空間：

```text
http://localhost:12000/?namespace=netlab
```

**修法**：照步驟 4 的網址開。另外要知道，網址上的過濾參數多半不會生效：加上 `&verdict=dropped` 沒有作用（右上角仍是 `Any verdict`），只有 `&flows-filter=protocol%3Dhttp` 這種寫法會帶進過濾框。在 UI 裡設好的判定等過濾條件不會留在網址上，想讓同事看同一個畫面，截圖或給他 CLI 指令比較可靠。

### flow 裡看得到 `Authorization` 的原文 {#mine-4}

**症狀**：對有 L7 政策的服務發帶認證資訊的請求，再用 CLI 看 flow：

```console
$ curl -H 'Authorization: Bearer SUPERSECRET-DEMO-TOKEN-abc123' \
       -H 'Cookie: session=demo-cookie-value' \
       "http://svc-echo7.netlab/?apikey=demo-key-in-url"

$ hubble observe --protocol http -o json
url: http://svc-echo7.netlab/?apikey=demo-key-in-url
     Authorization = Bearer SUPERSECRET-DEMO-TOKEN-abc123      ← 原文
     Cookie = session=demo-cookie-value                        ← 原文
```

bearer token、cookie、URL 裡的 api key 都原樣進了 flow。而步驟 2 執行 `helm upgrade` 時，Helm 自己印過這行警告：

```text
WARNING: TLS is not enabled for the Hubble Relay server (hubble.relay.tls.server.enabled=false).
```

**原因**：兩件事加在一起，能 port-forward 到 relay 的人，就能讀到叢集裡每一個走 L7 政策的請求的認證標頭。

- 只有走 L7 政策的流量會這樣。沒有 HTTP 規則就不會經過 Envoy，flow 裡也就沒有 `l7` 這一段。把政策寫到 HTTP 層，同時也讓這些標頭被記了下來。
- relay 是 `kube-system` 裡一個普通的 ClusterIP Service，有 `pods/portforward` 權限就連得上。很多叢集把這個權限當成維運權限發出去，沒有當成讀取敏感資料的權限在管。
- 緩衝區只留兩三分鐘，所以這些原文不會累積。可是一旦為了事後回查把 flow 匯出到儲存，存下來的就是含 token 的原文。

**修法**：收緊 `kube-system` 的 `pods/portforward` 權限，並照 Helm 警告裡的參數開啟 relay 的 TLS；匯出 flow 之前，先設定 header 的遮罩。TLS 和遮罩本課都沒有實作，也沒有驗證。

## 想一想

- 凌晨兩點收到告警，你在兩點十分連上叢集，打開 Hubble UI 想看兩點整發生了什麼，結果表上最早的一筆是兩點八分。這代表 Hubble 壞了嗎？你會先看哪個數字，下次要怎麼安排才查得到？
- 稽核報告指出 `netlab/client` 在半夜對內部服務發了一批被擋的 POST，證據是 Hubble 的紀錄。在認定是 `client` 的程式出問題之前，你還要查哪一類資料？為什麼光靠 flow 不夠？
- 主管想用一條告警「`--verdict DROPPED` 的筆數突然增加」來發現有人繞過網路政策。根據這一章的探針 C，這條告警抓得到 hostNetwork 的繞過嗎？要讓這類流量出現在 DROPPED 裡，得先改什麼？

## 小結

Hubble 讀的是 cilium-agent 從 Day 7 起就在記的 flow，開啟它只是加上 relay 和 UI 兩個讀取端，不會動到資料平面。它最有用的地方，是同時看到 Envoy 前後兩段連線，能證明被 L7 政策擋下的請求沒有送到應用，這是應用日誌和客戶端都給不了的。但它的紀錄有三個限制：每顆節點只有 4095 筆的環狀緩衝區，預設只留得住幾分鐘；來源記的是網路身分，攻擊者鑽進別人的網路命名空間發的請求會記在受害 pod 名下；hostNetwork pod 的流量以 `reserved:host` 出現，被 endpoint 政策對 `reserved:host` 的無條件規則放行，判定是 `ALLOWED`，找違規的過濾條件全部找不到。之後要拿 Hubble 的紀錄當證據時，先確認事件還在緩衝區的時間範圍內、來源需不需要用看得到行程的工具交叉比對，以及你要找的流量會不會被一條無條件放行的規則記成正常。

## 延伸閱讀

想再深入，可以從這幾份開始：

- **[Hubble 的組成](https://docs.cilium.io/en/stable/observability/hubble/)** —— 官方對 relay（跨節點與跨叢集聚合）與 UI（L3/L4 到 L7 的服務相依圖）的定位說明，以及設定 TLS、匯出 flow 等子頁的入口。
- **[Cilium Helm 參數表](https://docs.cilium.io/en/stable/helm-reference/)** —— 本章提到但沒有開的 `hubble.metrics`、`hubble.relay.tls` 等設定，完整參數都列在這裡。
- **[本機 host 與 endpoint 的政策](https://docs.cilium.io/en/stable/security/policy/layer3/#access-to-from-local-host)** —— 探針 C 的本機預設放行，以及交由政策控制的設定。
- **[Host Policies](https://docs.cilium.io/en/stable/security/policy/host/)** —— 另行管理主機流量的政策範圍，不能由啟用 host firewall 推論 endpoint 已拒絕本機來源。
- **[L7 政策](https://docs.cilium.io/en/stable/security/policy/layer7/)** —— 步驟 3 那個「兩段連線」的來源：官方說明 L7 政策會把流量導進節點上的 Envoy，違反規則時不會丟包，而是回一個 403。

## 下一步

Sprint 2 的動手部分到這裡結束。同一批核心事件，被四套工具用四種方式取用過：bpftrace 即時追蹤、Falco 用規則判斷、Tetragon 在核心裡攔下、Cilium 在網路層擋下並記錄。`nsenter` 那個情境，四套工具給了四個不同的答案；hostNetwork 那個破口，另外三套工具的章節完全沒有碰到。[Day 10](sprint2-day10-decision-matrix.md) 不動手，把散在九章裡的量測結果整理成一張分工表，說明每種需求該交給哪一套工具。

---

!!! quote ""
    Cilium 標誌為 Cilium 專案之官方資產（CNCF artwork），此處作社群教學用途。Hubble UI 擷圖為本課程實測畫面。
