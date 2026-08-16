# Day 3: 用 Cilium 做 east-west mesh——傳輸加密與 L7 落在 eBPF,身分綁定還在 beta

![Cilium 官方標誌](../assets/logos/cilium-icon-color.svg){ align=right width="76" }

> Day 2 用 Istio ambient 做服務間的加密與斷路。今天換 Cilium 自己的 mesh,做同一組驗收——服務間加密、身分、一個 L7 介入。差別在 Cilium 用 eBPF 與 WireGuard 做這些;而「加密」和「身分綁定」是兩件常被混為一談的事,今天分開驗,其中一件會看到還沒到位。

!!! abstract "你在課程的哪裡"
    - **Day 2**:Istio ambient 做到服務間自動 mTLS(ztunnel 的 HBONE 隧道)加可設的斷路。
    - **今天**:先乾淨拆掉 Istio,換成 Cilium mesh;抓包驗 WireGuard 傳輸加密、套一條 L7 policy、誠實看 mutual-auth 現況。
    - **Day 4**:把 Istio 與 Cilium 兩邊的實測擺成一張決策表橫向比。

## 加密和身分是兩件事,先分清楚

Istio ambient 的 mTLS 把兩件事一起做:ztunnel 的 HBONE 隧道同時把封包內容加密、又在兩端帶上 SPIFFE 身分。Cilium 把這兩件事拆成兩層,所以要分開驗:

- **傳輸加密**:把節點之間的封包內容藏起來,抓包看不到明文。Cilium 用 **WireGuard** 在 eBPF 資料面做。這是加密,本身不綁「這條流量是哪個工作負載對哪個工作負載」。
- **工作負載身分 / mutual auth**:確認 client 這個工作負載對 web 這個工作負載,用 SPIFFE 身分加雙向驗證。Cilium 這塊走 **SPIFFE/SPIRE**,官方標 beta。

把它們分開的理由,今天就會看到:傳輸加密驗得過,身分綁定這層在本課環境起不來。今天要走的路:先乾淨拆 Istio → 抓包驗 WireGuard → 套一條 L7 policy → 看 mutual-auth 現況。

## 步驟 1: 乾淨拆掉 Istio,不是口頭關

Cilium 的 L7 要用它自己的 per-node Envoy 執行。如果 Day 2 的 ztunnel/waypoint 還在,它們會先攔截流量,量到的就不是 Cilium 這條路。所以先把 Istio 拆乾淨:移除 namespace 的 ambient 標籤,再 `istioctl uninstall --purge`。

```text
$ kubectl label ns mesh-demo istio.io/dataplane-mode-
$ istioctl uninstall --purge -y
# 驗:istio-system 空、mesh-demo 沒有 ambient 標籤、也沒有 waypoint
$ kubectl -n istio-system get pods
No resources found in istio-system namespace.
```

## 步驟 2: WireGuard 傳輸加密——抓包看密文

Cilium 裝的時候開了 WireGuard。`cilium status` 看得到:

```text
Encryption:  Wireguard  [NodeEncryption: Disabled, cilium_wg0 (Port: 51871, Peers: 1)]
```

`NodeEncryption: Disabled` 這行要看清楚:加密的是 **pod 流量**,不是節點自身的流量。驗的方法是抓包——讓 client(在一個節點)打 web(在另一個節點)200 次,同時在節點的 eth0 上抓,看跨節點的 pod 流量長什麼樣:

```text
# client(node A, 10.224.0.4)→ web/nginx(node B, 10.224.0.5),跨節點,200 次全部 200
200 回應: 200 / 200

# 節點 eth0 的 pcap 分析
總封包                                  2734
WireGuard 密文 UDP 51871 ⇄ 10.224.0.5   2412
明文 HTTP TCP80 到 web pod 10.0.1.6         0
任何觸及 web pod IP 10.0.1.6 的明文封包       0

# 樣本(length 是密文長度)
IP 10.224.0.4.51871 > 10.224.0.5.51871: UDP, length 144
IP 10.224.0.5.51871 > 10.224.0.4.51871: UDP, length 144
```

200 次跨節點 HTTP 生出 **2412 個 WireGuard 密文封包**,而節點網卡上對 web pod 的明文是 **0**——pod 之間的 HTTP 全封在 WireGuard 隧道裡,eth0 上一個明文位元組都抓不到。這是傳輸加密的證據。但它只證明了「內容被藏起來」,沒有證明「兩端身分互相驗過」——那是下面 mutual-auth 的事。

## 步驟 3: 一條 Cilium L7 policy

Cilium 的 L7 授權透過它的 per-node Envoy 執行。套一條 `CiliumNetworkPolicy`,只允許 `app=client` 對 web 發 `GET /`:

```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: web-l7
  namespace: mesh-demo
spec:
  endpointSelector:
    matchLabels: { app: web }
  ingress:
  - fromEndpoints:
    - matchLabels: { app: client }
    toPorts:
    - ports:
      - { port: "80", protocol: TCP }
      rules:
        http:
        - { method: "GET", path: "^/$" }
```

`path` 是 regex,`^/$` 綁定完整的根路徑;method 與 path 不符的就被擋。從 client 打:

```text
GET  /       -> 200   (允許)
GET  /admin  -> 403   (path 不符)
POST /       -> 403   (method 不符)
```

`/admin` 的 403 由 Cilium 的 per-node Envoy 回,不是 nginx 的 404——L7 的判斷落在資料面,請求還沒到 nginx 就被擋掉了。

## 步驟 4: mutual-auth 現況——加密有了,身分綁定沒有

Cilium 的 mutual auth 在資料面已經啟用:

```text
cilium-config:
  mesh-auth-mutual-enabled = true
  mesh-auth-spire-agent-socket = /run/spire/sockets/agent/agent.sock
```

但它依賴的身分後端 SPIRE 在本課環境起不來:

```text
cilium-spire/spire-server-0   1/2  CrashLoopBackOff  (30 restarts)
cilium-spire/spire-agent-*    0/1  Init:Error        (25 restarts, ×2)
```

旗標開著、後端卻不 ready,身分綁定實際上沒有生效。所以今天誠實的結論是:**傳輸加密(步驟 2)有,工作負載身分綁定沒有**。這一塊 Cilium 官方標 **beta**(截至 2026-08),本課不宣稱每條 east-west 流量都有 mTLS 式的身分綁定——加密和身分是兩件事,這裡剛好各自落在天平的兩端。

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| WireGuard 傳輸加密 | 跨節點 pod 流量抓包看得到密文、看不到明文 | 200 次 HTTP → 2412 個 UDP 51871 密文封包;明文 HTTP = 0 |
| Cilium L7 policy | 一條 method/path 授權在資料面生效 | GET / 200、GET /admin 403、POST 403 |
| mutual-auth | 身分綁定可驗(或誠實標明缺口) | 旗標啟用但 SPIRE CrashLoop → 未 functional,記為 beta gap |

## 帶得走的東西

- **加密和身分是兩件事。** Istio 的 mTLS 把它們打包在一起,容易讓人以為「有加密就有身分」。Cilium 把兩層拆開,反而看得清楚:WireGuard 藏內容、SPIFFE 綁身分,今天前者過、後者還在 beta。
- **WireGuard 是節點資料面的透明加密。** pod 與應用不用改任何設定,加密發生在 eBPF 資料面;驗它要抓包看密文,不是看「有沒有開」的旗標。
- **eBPF 資料面做得到 L7 授權(method/path)。** 但那跟成熟的 traffic policy(斷路、重試)是兩回事,後者 Day 4 對比時會看到 Cilium 的侷限。
- **beta 功能就照 beta 寫。** 旗標開得起來不等於功能 ready;身分後端 CrashLoop,就誠實記成缺口,不補圓成「可用」。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[Cilium — Transparent Encryption(WireGuard/IPsec)](https://docs.cilium.io/en/stable/security/network/encryption/)** —— 官方文件講清楚 WireGuard 與 IPsec 兩種透明加密的差別、以及 `NodeEncryption` 開關的語意。
- **[Cilium — Mutual Authentication](https://docs.cilium.io/en/stable/network/servicemesh/mutual-authentication/mutual-authentication/)** —— 今天步驟 4 那條路的官方說明,含它為什麼是 SPIFFE/SPIRE out-of-band、以及目前的狀態界定。
- **[Cilium — Layer 7 Policy(HTTP)](https://docs.cilium.io/en/stable/security/policy/layer7/#http)** —— 步驟 3 的 `CiliumNetworkPolicy` L7 規則語法,含 HTTP method/path 的比對規則。

## 下一步

Istio 與 Cilium 兩條路的同一組驗收都跑完了:Day 2 的 Istio ambient(mTLS 與身分打包、斷路成熟)、今天的 Cilium(加密與 L7 落在 eBPF、身分綁定還在 beta)。[Day 4](sprint4-day4-istio-vs-cilium.md) 不動手,把兩邊的實測數字擺成一張決策表——資源、延遲、節點侵入度都在同一座叢集量,看這兩軸的不對稱怎麼變成選型依據。

---

!!! quote ""
    Cilium 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。WireGuard 抓包與 L7 驗證擷取自本課程實測。
