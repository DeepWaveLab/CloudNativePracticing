# Day 9: Hubble——它看得見什麼,以及它看不見的那三件事

![Cilium 官方標誌](../assets/logos/cilium-icon-color.svg){ align=right width="95" }

> [Day 8](sprint2-day8-cilium-network-policy.md) 留下兩種被擋下的流量:L4 丟包(沉默,呼叫端只看到逾時)與 L7 拒絕(403,而被保護的應用一行日誌都沒有)。今天換上 Hubble,問一個很直接的問題——**這些東西看得見嗎?** 答案是「一個漂亮、一個難看、一個很難看」,而最後那個難看的答案是本 sprint 最值得帶走的一句話。

!!! abstract "你在課程的哪裡"
    - **Day 7–8**:自管的 Cilium 資料平面、kube-proxy 已換掉,以及一組從命名空間隔離寫到 HTTP 方法的網路政策。
    - **今天**:開 Hubble 的 CLI 與 UI,在 UI 上找到 Day 8 那條被 L7 擋掉的流量並用 CLI 印出對應事件。然後拿它去問前幾天留下的三個懸案。
    - **接下來**:Sprint 2 的動手部分到今天為止,剩下的是四套工具的分工總結。

## 「開 Hubble」這個說法並不準確

先講一件會改變你對這一天的預期的事:**Day 7 裝好 Cilium 的那一刻起,每一顆 agent 就一直在記 flow 了。**

```console
### helm upgrade 之前,什麼都還沒改
$ cilium-dbg status | grep -i hubble
Hubble:   Ok   Current/Max Flows: 2285/4095 (55.80%), Flows/s: 19.36   Metrics: Disabled
```

`hubble.enabled` 在 chart 裡出廠就是 `true`。今天的 Helm 變更只加了兩個**消費端**:relay(把各節點的 agent 聚合起來)與 UI。所以今天要走的路是:

| 步驟 | 做什麼 |
|---|---|
| 1 | 復原,並重驗兩道前幾天的驗收 |
| 2 | 開 relay 與 UI,量它的成本——以及那個**決定它有沒有用的數字** |
| 3 | `hubble observe`:正常請求、L7 被擋、L4 丟包各長什麼樣 |
| 4 | **驗收:UI 上找到那條被擋的流量,CLI 印出對應事件** |
| 5 | 三個探針:前幾天的三個懸案 |
| 6 | service map:它擅長什麼、會誤導你哪裡 |

## 步驟 1: 復原,並重驗兩道前幾天的驗收

**kube-proxy replacement 第三次撐過停機重啟。**

```console
$ kubectl -n kube-system get ds kube-proxy -o custom-columns=…
kube-proxy   0   0   0   map[lab.local/no-kube-proxy:true]     ← 修改存活了(第三次)

$ cilium-dbg status --verbose | grep -A3 KubeProxyReplacement
KubeProxyReplacement:   True
Host firewall:          Disabled          ← 步驟 5 的探針 C 要用到這一行
  Socket LB:            Enabled
```

三次觀察、三次存活。這只是三次觀察,不是保證——**版本升級之後仍然要重驗**,因為它本來就是受管控制面的行為,而那不在你的變更紀錄裡。

**Day 8 的政策也還在,而且還在執行:**

```console
$ kubectl get cnp -A
NAMESPACE   NAME                   AGE   VALID
netlab      default-deny-ingress   36m   True
netlab      echo7-get-only         31m   True
```

`AGE` 是真相——那個時間點是 Day 8 收工前建立的,**這些物件不是重建的,是同一批**。不過「物件在」不等於「在執行」([Day 8 地雷 3](sprint2-day8-cilium-network-policy.md#mine-3) 的教訓),所以要打一次:

```console
$ GET  http_code=200
$ POST http_code=403   body: Access denied   header: server: envoy
```

**今天要在 UI 上找的,就是這一筆。**

## 步驟 2: 開 relay 與 UI

```console
$ helm upgrade cilium cilium/cilium --version 1.20.0 -n kube-system -f cilium-values-hubble.yaml
Release "cilium" has been upgraded.  REVISION: 3
（從指令到 relay 與 UI 都就緒:27 秒）
```

而**沒有**發生的事更重要:

```console
$ kubectl -n kube-system get pods -o custom-columns=N:…,RESTARTS:…
cilium-mvrd5           0
cilium-envoy-xxbzc     0
```

**cilium-agent 一次都沒有重啟,資料平面完全沒有被碰到。** 這跟 Day 7 開 kube-proxy replacement 是完全不同的操作等級——那次改的是 BPF 程式的行為,今天只是多兩顆讀取端的 Deployment。

**開觀測面不需要冒動資料平面的險**,而這是它跟前面兩套工具都不一樣的地方:Falco 要裝核心驅動、Tetragon 要換 agent。

### chart 有沒有替你想過 resources 與 priorityClassName

Day 3、5、7 都問過同一組問題,今天的答案**跟同一張 chart 上的 agent 完全相反**:

| | requests | QoS | priority |
|---|---|---|---|
| `cilium` agent(Day 7) | `{}`(但有 init container 借來的) | Burstable | **2000001000** |
| `cilium-envoy`(Day 7) | `{}` | — | **2000001000** |
| **`hubble-relay`** | `{}` | **BestEffort** | **0** |
| **`hubble-ui`** | `{}` | **BestEffort** | **0** |

**同一個 Helm chart、同一個維護團隊,agent 是 `system-node-critical`,relay 與 UI 是 priority 0 加 BestEffort。**

這個差別其實是對的——**agent 掛掉節點斷網,relay 掛掉只是看不到東西**。但後果要講清楚:在滿載節點上,搶佔演算法第一個挑中的就是 BestEffort 加 priority 0,**也就是 Hubble 最容易在節點壓力最大的時候消失,而那正是你最需要它的時候**。

資源本身很便宜:relay 2m CPU / 20Mi,UI 兩個容器合計 9m / 28Mi,**兩者加起來 11m CPU、48Mi**。對照同一顆節點上的 agent 是 28m / 222Mi。

### 那個決定 Hubble 有沒有用的數字

見[地雷 1](#mine-1)。這是本章最重要的一段,而它不在任何一份入門文件的第一頁。

## 步驟 3: 三種 flow 各長什麼樣

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

**一次 `curl` 在 Hubble 上是 15 筆 flow,而且是兩段連線。** `10.244.0.69` 那幾筆是 Envoy 代理出去的那一段——**這件事在客戶端與應用端都完全看不到,只有在這裡看得到。**(那個 `to-proxy` 事件型別,就是 [Day 8](sprint2-day8-cilium-network-policy.md#step-6) 那條 `PROXY PORT 12286` 在觀測面的樣子。)

### 一次被 L7 擋掉的 POST

```console
09:27:35.125  client:52324 -> echo7:8080  policy-verdict:none … ALLOWED (SYN)   ← L4 放行
09:27:35.125  client:52324 -> echo7:8080  to-proxy      FORWARDED (SYN)
09:27:35.126  client:52324 -> echo7:8080  http-request  DROPPED (HTTP/1.1 POST http://svc-echo7.netlab/)
09:27:35.126  client:52324 <- echo7:8080  http-response FORWARDED (HTTP/1.1 403 0ms)
```

**只有 10 筆,而且沒有 `10.244.0.69 → echo7` 那一段。** 步驟 5 的探針 A 會回來用這個形狀。

另外注意第一行:**同一條連線的 L4 判定是 `ALLOWED`,L7 判定才是 `DROPPED`。** 一個只看「有沒有連線被拒」的儀表板,會把這次違規算成正常連線。

### 一次 L4 丟包

```console
09:27:35.132  client:57822 <> multiport:9090  policy-verdict:none … DENIED (SYN)
09:27:35.132  client:57822 <> multiport:9090  Policy denied DROPPED (SYN)
09:27:36.177  …（TCP 重送,同樣兩筆）×3
```

```json
{ "verdict": "DROPPED", "Type": "L3_L4",
  "drop_reason": 133, "drop_reason_desc": "POLICY_DENIED" }
```

同一個欄位在 L7 那筆上不存在,那是[地雷 2](#mine-2)。

## 步驟 4: 驗收——UI 半邊與 CLI 半邊

打開 UI 之前有兩個純操作的坑:網址寫法([地雷 3](#mine-3)),以及**必須先有人在發流量**,否則表是空的([地雷 1](#mine-1) 的直接後果)。本課為此加了一顆每 5 秒發三個請求的 pod,剛好產出三種不同顏色的 flow。

### UI 半邊

![Hubble UI 的服務圖與 flow 表:echo7:8080 是紅色虛線(GET 過、POST 擋),multiport:9090 是紅色實線(全擋)](../assets/screenshots/sprint2-day9/05-service-map-l7-dashed-vs-l4-solid.png)

服務圖上那兩條線的**顏色本身就有資訊**:`echo7:8080` 是**虛線**(有通有不通),`multiport:9090` 是**實線紅**(全擋)。

點開 flow 表裡那筆 `→ POST / 0ms` 的列:

![Flow Details 側欄:Verdict 是 dropped,而 Drop reason 寫著 Unknown drop reason](../assets/screenshots/sprint2-day9/06-flow-detail-l7-unknown-drop-reason.png)

`Verdict: dropped` ——**驗收的 UI 半邊過了**。而它下面那一格寫著 `Unknown drop reason`,那是[地雷 2](#mine-2),對照組長這樣:

![同一個側欄,L4 被擋的那筆寫的是 Policy denied,還帶 TCP flags](../assets/screenshots/sprint2-day9/07-flow-detail-l4-policy-denied.png)

**同一個 UI、同一個面板,L4 那筆說得出理由(`Policy denied`),L7 那筆說不出。**

側欄裡**沒有**的東西也值得記:它有判定、丟棄原因、identity、標籤、IP、埠、流量方向,**但沒有 HTTP 的方法、URL 或 header**。表格的 `L7 info` 欄只給 `→ POST / 0ms`。**要看那次請求長什麼樣,只能回 CLI。**

### CLI 半邊

```console
$ export HUBBLE_SERVER=localhost:4245
$ hubble observe --namespace netlab --protocol http --verdict DROPPED --last 500 -o compact
Aug  7 13:02:19.180: netlab/flowgen-…:39002 (ID:51695)
    -> netlab/echo7-…:8080 (ID:42793)
    http-request DROPPED (HTTP/1.1 POST http://svc-echo7.netlab/)
```

完整 JSON(節錄):

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

**兩半都過。** 而那個 `headers` 欄位藏著今天最後一顆地雷([地雷 4](#mine-4))。

## 步驟 5: 三個探針

### 探針 A:L7 的拒絕在應用日誌裡真的一行都沒有 {#probe-a}

[Day 8 地雷 7](sprint2-day8-cilium-network-policy.md#mine-7) 的宣稱是推論出來的(「請求在 proxy 就被截了」)。今天直接量:打三次 POST、兩次 GET,應用日誌**一行都沒有增加**。

**誠實記一筆**:被測的那支程式出廠不記存取日誌,所以那兩次成功的 GET 也沒有留下痕跡——**這個對照本身不能證明「只有被擋的看不到」。**

真正乾淨的證據在 flow 的形狀裡:

| | `client → proxy` 那一段 | `proxy → echo7` 那一段 |
|---|---|---|
| GET(放行) | 有 | **有**(5 筆) |
| POST(被擋) | 有 | **完全沒有** |

**Hubble 用「第二段不存在」證明了應用真的沒有收到那個請求。** 這比查日誌強得多:**查日誌只能得到「沒看到」,這裡得到的是「它沒被送過去」。**

順帶一個 Day 8 沒注意到的細節:那筆 403 在 Hubble 上記成 `<- netlab/echo7-… http-response FORWARDED`。**`echo7` 從來沒有產生過這個回應,是 Envoy 產的**,但 flow 的方向與來源都掛在 `echo7` 名下。看報表的人會得到「echo7 回了 403」這個結論,而 `echo7` 完全無辜。

### 探針 B:Hubble 分不出誰違規,而且會指名受害者 {#probe-b}

條件對齊到逐字相同——都用 pod IP、都是 POST,一次由 `client` 自己發,一次由攻擊者 `nsenter` 進 `client` 的網路命名空間之後發。兩筆 flow 在 Hubble 上:

```console
time      : …09:29:50.541Z            time      : …09:29:53.168Z
  source  : netlab/client               source  : netlab/client
  identity= 6874                        identity= 6874
  IP= 10.244.0.191                      IP= 10.244.0.191
  labels  : ['k8s:app=client']          labels  : ['k8s:app=client']
  行程欄位: 無                           行程欄位: 無
```

**逐欄相同。** 唯一不同的是 `Content-Length`,而那是兩次 payload 字數不同的巧合,不是歸屬資訊。整份 flow JSON 的頂層欄位裡**沒有任何一個與行程、PID、cgroup 或執行檔有關**。

Day 8 對這件事的說法是「光看丟包分不出誰違規」。今天要說得更重:**Hubble 不是保持沉默,它是主動把攻擊者的請求記名在 `netlab/client` 身上。** 事故報告上那一行「是 client 幹的」會是錯的,UI 上那個標籤會是錯的,而**工具本身沒有任何表達「我不確定」的方式**。

這也把本 sprint 四套工具的分工釘死了:

| | 看得到 `nsenter` 這個動作 | 擋得住這個封包 | 說得出是**誰**做的 |
|---|---|---|---|
| Falco(Day 3–4) | ✅ | ❌ | ✅ 行程 |
| Tetragon(Day 5–6) | ✅ | ✅ 但被繞 | ✅ 行程 |
| Cilium 政策(Day 8) | ❌ | ✅ 繞不過 | ❌ |
| **Hubble(Day 9)** | ❌ | — | ❌ **而且會給錯名字** |

**Hubble 是網路的鏡子,不是行程的鏡子。** 要把一條 flow 對應到一個行程,需要的是 Tetragon 那條線的資料,而**這兩份資料在本 sprint 的四套工具裡沒有任何一個地方會合。**

### 探針 C:政策層最大的破口,在監控上長得像合法轉發 {#probe-c}

[Day 8 地雷 9](sprint2-day8-cilium-network-policy.md#mine-9):`Host firewall: Disabled` 之下,任何 `hostNetwork` pod 穿透全部網路政策。今天問:Hubble 看得到嗎?

**看得到封包,看不出那是繞過。** 三個過濾器全部失手:

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

不加任何過濾、只用時間才看得到,而來源欄位是:

```json
"source": { "identity": 1, "labels": ["reserved:host"] }
```

**沒有 pod 名稱、沒有命名空間、沒有容器——只有 `reserved:host`。** 而那個位址:

```console
$ cilium-dbg status | grep -i "proxy status"
Proxy Status:   OK, ip 10.244.0.69, 1 redirects active on ports 10000-20000
```

**那正是 Cilium 自己 Envoy proxy 的位址。** 也就是說,步驟 3 裡那幾筆「Envoy 把合法 GET 轉給應用」的 flow,和這裡「攻擊者繞過全部政策打進去」的 flow,**在 Hubble 的來源欄位裡是同一個位址、同一個 identity、同一組標籤。**

**這座叢集最大的政策破口,在監控工具上長得跟 Cilium 自己的合法轉發一模一樣,而且不會出現在任何一個「找違規」的視圖裡。**

這不是因為它壞了,而是因為**它的視角就是 endpoint 的視角,而那個破口的定義就是「不經過 endpoint 政策」**。工具的盲點跟它的執行點是同一件事的兩面——Day 6 的 Tetragon 是 cgroup、Day 8 的政策是網路命名空間、今天的 Hubble 是 endpoint。

## 步驟 6: service map——它擅長什麼,會誤導你哪裡

```console
SOURCE                      DESTINATION                        PORT    FLOWS  VERDICTS
reserved:host               reserved:world                       80      784  FORWARDED=784
reserved:host               kube-system/metrics-server         4443      364  FORWARDED=364
netlab/flowgen              netlab/echo7                       8080      204  REDIRECTED=34, FORWARDED=153, DROPPED=17
reserved:host               kube-system/hubble-relay           4222      136  FORWARDED=136
netlab/flowgen              netlab/multiport                   9090      106  DROPPED=106
```

**擅長什麼**:`flowgen → echo7:8080` 那一列同時有 `FORWARDED=153` 與 `DROPPED=17`,一眼看得出「這條邊有通有不通」——**這是靜態讀政策讀不出來的,因為政策寫的是規則,這裡寫的是實際發生的事**。同理 `multiport:9090` 那列全部 DROPPED 且沒有任何 FORWARDED,代表那條政策確實在擋、而且有人一直在撞它。要回答「這條規則刪掉會不會出事」,service map 是最快的路。

**會誤導你哪裡**,四件:

1. **只有最近說過話的東西才在圖上。** 這份匯出裡完全沒有 `client`、`probe`、`svc-backend`——它們全都還活著,只是最近兩分鐘沒發流量。**service map 是「最近的通聯紀錄」,不是「拓撲」。** 把它當拓撲用,就會刪掉一個每天只跑一次的批次任務所需要的規則。而「最近」有多近由[地雷 1](#mine-1) 決定。
2. **會被節點噪音淹沒。** 排名第一的是節點自己對外的流量,前十名裡有六列是 `reserved:host` 出發的。**應用流量在自己的服務圖上是少數。**
3. **觀測工具會出現在自己的圖上。** relay、UI、metrics-server 加起來佔掉可觀的篇幅,**而且同時在消耗那個本來就很淺的緩衝區**。
4. **`reserved:host` 是個黑洞。** 探針 C 的全部繞過流量都聚在那一列裡,跟 Envoy 的合法轉發混在一起,verdict 全是 FORWARDED。**在服務圖上,那次繞過看起來就是節點在正常工作。**

## 誠實的差距

- **緩衝區容量的預設值,文件與實測對不上。** 見[地雷 1](#mine-1)。本課只能確定「這座叢集是 4095」,不能宣稱那是所有版本的預設。
- **沒有開匯出。** 要把 Hubble 當事後鑑識工具就得把 flow 落地或送 Prometheus,兩者都是額外的 Helm 變更與儲存成本,本課刻意沒開(會改資料平面設定,而且要另外驗證)。**所以「接出去之後歷史有多深、成本多少」本課沒有答案。**
- **探針 A 的對照組不完整。** 被測程式出廠不記存取日誌,所以「成功的請求有沒有留下日誌」這一半沒有對照。結論只涵蓋「被擋的請求在應用端沒有痕跡」。
- **host firewall 從頭到尾沒開。** 探針 C 的破口在本課環境是開著的,而堵法(`hostFirewall.enabled=true` 加叢集範圍政策)沒有實作也沒有驗證。
- **單節點。** relay 的價值是把多個節點的 agent 聚合起來,而這座叢集只有一顆節點——**跨節點聚合這件事本課等於沒測。**

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| 前幾天的驗收仍成立 | kube-proxy 仍為 0,Day 8 的 L7 政策仍在執行 | 第三次存活;GET 200 / POST 403 |
| 開 Hubble 不動資料平面 | cilium-agent 的重啟次數 | **0**,relay 與 UI 27 秒就緒 |
| 緩衝區深度可量化 | 現在時間與緩衝區裡最舊那筆的差 | 閒置 **3 分 02 秒**;加一顆小流量 pod 後 **2 分 24 秒** |
| **驗收:UI 半邊** | 在 UI 上指出那條被 L7 拒絕的流量 | Flow Details 顯示 `Verdict: dropped`,擷圖入庫 |
| **驗收:CLI 半邊** | 印出同一筆的事件 | `http-request DROPPED (HTTP/1.1 POST …)`,JSON 含 method 與 URL |
| 探針 A | 應用日誌的行數變化,以及 flow 的段數 | 日誌 0 增加;放行有兩段、被擋只有一段 |
| 探針 B | 兩種來源的 flow 欄位是否可區分 | **逐欄相同**,且無任何行程欄位 |
| 探針 C | 三個常用過濾器能否找到繞過流量 | **三個全部回空** |

## 地雷記錄

### 地雷 1:緩衝區只有幾千筆、per-node、環狀覆蓋——實測歷史只有兩三分鐘 {#mine-1}

**這是本章最重要的數字,而它不在任何一份入門文件的第一頁。**

事件的來源是每顆 cilium-agent 自己的環狀緩衝區,滿了就覆蓋最舊的,**不落地、不外送**。這座叢集的 agent 自己回報容量是 **4095**:

```console
$ cilium-dbg status | grep -i hubble
Hubble:   Ok   Current/Max Flows: 2508/4095 (61.25%), Flows/s: 19.49
```

**先講一個必須誠實處理的落差**:官方 Helm 參考文件對 `hubble.eventBufferCapacity` 列出的預設值是 **65536**,而這座叢集(chart 1.20.0)實測是 **4095**——chart 的 `values.yaml` 裡這一行是註解掉的,`helm show values` 只看得到 `# eventBufferCapacity: "4095"`。**本課只能確定自己量到的數字,不能宣稱哪一個是「所有版本的預設」。** 而這件事本身就是結論:**這個數字要在自己的叢集上問 agent,不要相信任何一份文件上的數字,包括這一章。**

實測填充速度(節點剛開機、八顆 pod、沒有任何業務流量):

```console
17:25:28  Current/Max Flows: 2508/4095 (61.25%), Flows/s: 19.49
17:25:59  Current/Max Flows: 3060/4095 (74.73%), Flows/s: 19.28
17:27:06  Current/Max Flows: 4095/4095 (100.00%)      ← 節點開機約 7 分鐘就滿了
```

滿了之後直接量歷史深度(現在時間對緩衝區裡最舊那一筆):

| 情境 | **歷史深度** |
|---|---|
| 閒置(八顆 pod,無業務流量) | **3 分 02 秒** |
| 加一顆每 5 秒三個請求的 pod | **2 分 24 秒** |

**一顆每 5 秒發三個 curl 的 pod,就把整座叢集的可觀測歷史砍掉四分之一。** 換算到真實環境:一個中等忙碌的節點跑幾百 flows/s,緩衝區的深度是**十幾秒**。

**三個實務結論:**

1. **「出事了去 Hubble 看一下」在出廠設定下不成立。** 從被叫醒、連上網、port-forward 到打開瀏覽器,時間早就超過緩衝區深度了。**Hubble 出廠是即時儀表板,不是事後鑑識工具。**
2. **要當鑑識工具就得接出去**(把 flow 落地或送指標系統),而那是額外的設定與儲存成本。
3. **`Current/Max Flows: 100%` 不是健康指標,是溢位指標。** 它的意思是「你正在丟資料」,但它長得像進度條。

### 地雷 2:L7 的拒絕沒有丟棄原因,UI 上直接寫「Unknown drop reason」 {#mine-2}

L4 丟包的 JSON 有完整的原因:

```json
{ "verdict": "DROPPED", "Type": "L3_L4", "drop_reason": 133, "drop_reason_desc": "POLICY_DENIED" }
```

L7 拒絕的同一份 JSON:

```console
drop_reason      : (不存在)
drop_reason_desc : (不存在)
```

**`verdict: DROPPED` 有,`Type: L7` 有,`drop_reason` 整個欄位不存在。** UI 誠實地把這件事寫出來——那兩張並排的擷圖就是證據:同一個面板,L4 那筆寫 `Policy denied`,L7 那筆寫 `Unknown drop reason`。

**後果分兩層:**

- **追查上**:L4 丟包可以照原因分類統計,**L7 拒絕全部擠在同一個「不知道」桶裡**——是方法不合、路徑不合、還是 header 不合,flow 上都沒寫,你得自己回去讀政策。
- **告警上**:任何「按丟棄原因分流」的規則都會**漏掉全部的 L7 拒絕**,而 L7 拒絕正是最需要有人看的那一種——它代表有東西正在敲你明確禁止的介面。

### 地雷 3:UI 的網址要用 `?namespace=`,而且過濾狀態不在網址裡 {#mine-3}

直覺的 `http://localhost:12000/netlab` 會得到:

```text
Unexpected Application Error!
404 Not Found
```

根目錄也不是答案——它是一張只有「Choose namespace」下拉選單的歡迎頁,**flow 表與服務圖都不存在**。能用的寫法是查詢參數:

```text
http://localhost:12000/?namespace=netlab
```

**而且只有 `namespace` 這個參數會被讀。** 實測 `?namespace=netlab&verdict=dropped` 完全沒有作用(右上角仍是 `Any verdict`)。

**後果**:UI 的過濾狀態多半不在網址裡,**一個「已經濾好的 Hubble 視圖」沒辦法用連結交給同事**——只能截圖。

**給自動化的第三個坑**:Hubble UI 會一直開著 websocket,所以 headless 瀏覽器那種「等頁面 idle 之後自動截圖」的做法會**永遠掛著不返回**,必須改用瀏覽器的除錯協定定時抓。

### 地雷 4:HTTP request header 原文進 flow buffer,而 relay 出廠不開 TLS {#mine-4}

驗收那段 JSON 裡的 `headers` 不是摘要,是原文:

```console
$ curl -H 'Authorization: Bearer SUPERSECRET-DEMO-TOKEN-abc123' \
       -H 'Cookie: session=demo-cookie-value' \
       "http://svc-echo7.netlab/?apikey=demo-key-in-url"

$ hubble observe --protocol http -o json
url: http://svc-echo7.netlab/?apikey=demo-key-in-url
     Authorization = Bearer SUPERSECRET-DEMO-TOKEN-abc123      ← 原文
     Cookie = session=demo-cookie-value                        ← 原文
```

而 `helm upgrade` 自己印出來的警告是:

```text
WARNING: TLS is not enabled for the Hubble Relay server (hubble.relay.tls.server.enabled=false).
```

**把兩件事疊起來:任何能 port-forward 到 relay 的人,就能明文讀到叢集裡每一個走 L7 政策的請求的認證標頭。**

三層要分開講:

- **它只發生在有 L7 政策的路徑上。** 沒有 HTTP 規則就沒有 Envoy,沒有 Envoy 就沒有 `l7` 區段。**也就是說「把政策寫細一點」這個安全決定,順便打開了一份明文的認證標頭日誌**——兩件事在文件裡分屬不同章節。
- **RBAC 上它不是一個「機密」。** relay 是一個普通的 ClusterIP Service,能做 `pods/portforward` 的人就讀得到,**而那個權限在多數叢集裡是給「維運」而不是給「資料存取」的**。
- **緩衝區淺這件事在這裡反而是保護。** 只留兩三分鐘代表這份明文不會累積——但那也代表**你沒辦法既要鑑識深度又不要這份風險**:一旦把 flow 落地,落下去的就是含 token 的原文。要兩者兼得得自己設定標頭遮罩,而那不是預設值。

## 帶得走的東西

- **一個工具的盲點,跟它的執行點是同一件事的兩面。** Hubble 看不見 hostNetwork 的繞過,不是因為它壞了,是因為它的視角就是 endpoint 的視角,而那個破口的定義就是「不經過 endpoint 政策」。同樣的邏輯解釋了 Tetragon 為什麼被 `nsenter` 繞過(它綁 cgroup),以及網路政策為什麼繞不過(它綁網路命名空間)。
- **Hubble 是網路的鏡子,不是行程的鏡子——而且它會給錯名字。** 攻擊者鑽進別人的網路命名空間之後,flow 逐欄記在受害者身上,而工具沒有任何表達「我不確定」的方式。要把一條 flow 對應到一個行程,需要另一條資料線。
- **「第二段不存在」比「日誌裡沒有」是強得多的證據。** 查日誌只能得到「沒看到」;flow 的兩段結構可以證明「它沒被送過去」。這是 Hubble 最站得住的用途:**任何在 proxy 被截掉的東西,客戶端與應用端各只看得到半個故事。**
- **先問緩衝區有多深,再決定它在你的事故流程裡是什麼角色。** 幾千筆、每節點、環狀覆蓋,在閒置的實驗叢集上是三分鐘,在忙碌的節點上是十幾秒。這個數字決定了 Hubble 是「即時儀表板」還是「鑑識工具」,而它預設是前者。
- **服務圖是最近的通聯紀錄,不是拓撲。** 最近沒說過話的東西不在圖上,而那個「最近」由緩衝區深度決定。把它當拓撲用會刪掉一天只跑一次的批次任務所需要的規則。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[Hubble 的組成](https://docs.cilium.io/en/stable/observability/hubble/)** —— 官方對 relay(跨節點與跨叢集聚合)與 UI(L3/L4 到 L7 的服務相依圖)的定位說明,以及設定 TLS、匯出 flow 等子頁的入口。
- **[Cilium Helm 參數表](https://docs.cilium.io/en/stable/helm-reference/)** —— 地雷 1 的另一半:`hubble.eventBufferCapacity` 在這裡列的數字與本課實測不同,**兩邊並讀正好說明為什麼這個值要在自己的叢集上量**。
- **[Host Firewall](https://docs.cilium.io/en/stable/security/host-firewall/)** —— 探針 C 那個破口的堵法。開了它,節點層的流量才會有政策判定,Hubble 才有東西可以標成 DROPPED。
- **[L7 政策](https://docs.cilium.io/en/stable/security/policy/layer7/)** —— 步驟 3 那個「兩段連線」的來源:官方說明 L7 政策會把流量導進節點上的 Envoy,而違反規則不會丟包、是回一個 403。

## 下一步

Sprint 2 的動手部分到這裡結束。九天下來,同一批核心事件被四套工具用四種方式取用過:bpftrace 臨場追、Falco 用規則判斷、Tetragon 在核心裡攔、Cilium 在網路層擋與看。

而最值得回頭看的,是它們**各自看不見的東西剛好是別人的長處**——`nsenter` 那一題四套工具給了四個不同的答案,`hostNetwork` 那個破口有三套工具完全沒有提過。[Day 10](sprint2-day10-decision-matrix.md) 不動手,把這些散在九章裡的實測數字收攏成一張分工決策表,每一格都要能追溯到某一天的某一個量測。

---

!!! quote ""
    Cilium 標誌為 Cilium 專案之官方資產(CNCF artwork),此處作社群教學用途。Hubble UI 擷圖為本課程實測畫面。
