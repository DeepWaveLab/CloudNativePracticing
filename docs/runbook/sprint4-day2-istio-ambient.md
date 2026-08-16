# Day 2: Istio ambient——服務之間自動 mTLS,再對一個服務設斷路

![Istio 官方標誌](../assets/logos/istio-icon-color.svg){ align=right width="72" }

> [Day 1](sprint4-day1-envoy-gateway.md) 做完了入口(north-south)。今天換服務**之間**(east-west):讓兩個服務互打時流量自動加密、而且各自帶身分,再對其中一個服務設斷路,壓到門檻時看它把多的連線擋掉。用的是 Istio 的 **ambient 模式**——不塞 sidecar,疊在 Day 1 裝的 Cilium 上。

!!! abstract "你在課程的哪裡"
    - **[Day 1](sprint4-day1-envoy-gateway.md)**:north-south 那層——Envoy Gateway 收入口流量、開 HTTP/3。
    - **今天**:east-west 那層——Istio ambient 給服務之間裝上 mTLS 與斷路。
    - **[Day 3](sprint4-day3-cilium-mesh.md)**:換 Cilium 的 mesh 做**同一組**驗收,Day 4 兩邊橫向對比。

環境續用 Day 1 的 `cnp-mesh`,先 `az aks start` 把它從停機叫回來——一啟動就撞到一顆跟 AKS 停開機有關的雷,見[地雷 1](#mine-1)。

## 今天要走的路

| 步驟 | 做什麼 | 對應 |
|---|---|---|
| 1 | 在 Cilium 上裝 Istio ambient | 收 Day 1 `cni.exclusive=false` 的伏筆 |
| 2 | 把 namespace 標成 ambient,驗服務間 mTLS + 身分 | gate:流量加密且帶 SPIFFE 身分 |
| 3 | 佈 waypoint、對 web 設斷路、壓到跳開 | gate:斷路 trip |

先講幾個今天會一直用到的詞:

- **ambient**:Istio 的無 sidecar 模式。舊的 sidecar 模式是每個 pod 旁邊塞一個 Envoy(Day 0 講過為什麼退場);ambient 改成每個**節點**一個 **ztunnel**(輕量 L4 元件,負責加密),要動到 L7 的時候才另外起一個 **waypoint**(Envoy proxy)。
- **mTLS**(mutual TLS):雙向 TLS——不只客戶端驗伺服器,伺服器也驗客戶端,兩邊都拿憑證證明自己是誰。
- **SPIFFE 身分**:一種標準化的工作負載身分格式,長得像 `spiffe://cluster.local/ns/<命名空間>/sa/<service account>`。mTLS 兩端互驗的就是這個身分。
- **斷路**(circuit breaking):一個服務被太多並發請求壓住、或開始出錯時,mesh 直接把多出來的請求擋掉(回 503),而不是讓它們全部塞在那裡把整條鏈拖垮。就像電路過載時保險絲跳開。

## 步驟 1: 在 Cilium 上裝 Istio ambient

Day 1 裝 Cilium 時設了 `cni.exclusive=false`,當時說是「留位子給 Istio」——今天就是用它的時候。istio-cni(ambient 需要的一個元件)要掛在 Cilium 的 CNI 鏈上,`cni.exclusive=false` 讓 Cilium 不獨佔、容得下它。

```console
$ istioctl install --set profile=ambient -y
✔ Istio core installed
✔ CNI installed
✔ Istiod installed
✔ Ztunnel installed
```

裝完 istio-cni 跟 Cilium 兩個 CNI 的 DaemonSet 並存,誰也沒踩誰:

```text
istio-cni-node   1/1   Running   (istio-system)
cilium           1/1   Running   (kube-system)
```

這一步就是開工前的 spike A3:Istio ambient 疊在 Cilium 上會不會搶節點資料面。這座叢集上一次裝成、不用退回 sidecar——關鍵是 Day 1 那三個 Cilium 設定(`cni.exclusive=false`、`socketLB.hostNamespaceOnly=true`、保持 iptables masquerade 不開 `bpf.masquerade`)。

## 步驟 2: 標成 ambient,驗服務間 mTLS + 身分

把 Day 1 那個 `mesh-demo` namespace 標成 ambient,再放一個帶專屬 service account 的 client 去打 web:

```console
$ kubectl label ns mesh-demo istio.io/dataplane-mode=ambient
$ client → web:  req1 200  req2 200  req3 200
```

三個 200 只證明打得通。要證明它**走了 mTLS 且帶身分**,去看 ztunnel 的連線紀錄:

```text
src.workload="client-..."  src.identity="spiffe://cluster.local/ns/mesh-demo/sa/client"
dst.addr=10.0.0.235:15008  dst.hbone_addr=10.0.0.235:80
dst.service="web.mesh-demo.svc.cluster.local"
dst.identity="spiffe://cluster.local/ns/mesh-demo/sa/default"
```

兩個地方是關鍵:

- `dst.addr=…:15008` 配上 `dst.hbone_addr=…:80`——應用打的是 `web:80`,但兩個節點之間實際走的是 **15008 這個 HBONE 埠**(ambient 的 mTLS 隧道)。也就是說,原本明文的 `client→web` 被 ztunnel 換成了節點間的加密隧道,應用完全沒改。
- `src.identity` 與 `dst.identity` 是兩個不同的 SPIFFE 身分(`sa/client` 對 `sa/default`)——mTLS 兩端各自證明了自己是誰。

還有一個細節:client 跟 web 都是 Day 1 就在跑的 pod,標成 ambient 之後**沒有重啟**就被納管(ztunnel 在節點層攔截,不像 sidecar 要重建 pod)。這是 ambient 跟 sidecar 一個實際的差別。

## 步驟 3: waypoint + 斷路

斷路是 L7 的事(要看得懂 HTTP 才能算並發),所以要先給 web 佈一個 **waypoint**——ambient 裡負責 L7 的 Envoy:

```console
$ istioctl waypoint apply -n mesh-demo --enroll-namespace
✅ waypoint applied; namespace labeled istio.io/use-waypoint: waypoint
```

(這個指令**沒有 `-y` 旗標**,照 sidecar 的習慣加會報錯。)

再套一條把並發壓到極低的 `DestinationRule`(`maxConnections: 1`、`http1MaxPendingRequests: 1`),然後從 client 一次丟 50 個並發請求:

```text
  22 200
  28 503        ← 斷路 trip
```

一半以上被擋。看被擋的那些回應標頭:

```text
x-envoy-overloaded: true
x-envoy-decorator-operation: web.mesh-demo.svc.cluster.local:80/*
```

`x-envoy-overloaded: true` 是 Envoy 明確說「這個請求是被斷路擋掉的」(連線池溢出);`x-envoy-decorator-operation` 那行證明流量經過了 waypoint 的 Envoy 做 L7 判斷。斷路成立。

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| 服務間 mTLS + 身分 | 流量走加密隧道、兩端帶 SPIFFE 身分 | ztunnel 紀錄:`dst 15008`(HBONE)、`sa/client` ↔ `sa/default` |
| 斷路 trip | 壓過門檻時多的連線被擋、有明確訊號 | 50 並發 → 28 筆 503,帶 `x-envoy-overloaded: true` |
| ambient 疊 Cilium | 兩個 CNI 並存、ambient 起得來 | istio-cni 與 cilium DaemonSet 並存,一次裝成 |

## 地雷記錄

### 地雷 1:`az aks start` 之後叢集連不上,其實是本機 DNS 的負快取 {#mine-1}

**症狀**:`az aks start` 回報成功、`az aks show` 顯示 `Running`,但 `kubectl` 一律 `Unable to connect to the server: … no such host`。

**根因**:`az aks stop` 會把 API server 的 DNS 記錄**刪掉**,`start` 之後要幾分鐘才重建、對外發布。在這段空窗裡,本機(或家用網路)的 DNS resolver 查過一次、把「查無此名」的結果快取了下來(負快取);等記錄真的上線,本機還在回舊的 `NXDOMAIN`。拿公用 DNS 驗一下就露餡——同一個名字在 `8.8.8.8` 解得到 IP,本機 resolver 卻回 `NXDOMAIN`。

**判斷準則**:**`az aks show` 說 `Running`、`kubectl` 卻 `no such host` 時,用 `nslookup <FQDN> 8.8.8.8` 對照。** 公用 DNS 解得到就是本機負快取,不是叢集壞了——別急著重建。

**修法**:等負快取的 TTL 過;或繞過本機 DNS——把 kubeconfig 的該叢集 `server` 指向已解析的 IP、用 FQDN 當 TLS 名稱:

```sh
kubectl config set-cluster <cluster> \
  --server=https://<已解析的 IP>:443 \
  --tls-server-name=<FQDN>
```

收工再 `az aks get-credentials --overwrite` 還原成 FQDN。

## 誠實的差距

- **斷路是在 waypoint(L7)上做的**——這是 Istio ambient 成熟的一面。Day 3 換 Cilium 做同一組驗收時,Cilium 的斷路等價物是低階的 `CiliumEnvoyConfig`,兩邊在「L7 韌性」這一軸不對等;那個不對等本身就是 Day 4 對比的結論之一,今天先把 Istio 這邊的基準立好。
- **單節點、單次量測**。整座叢集一個 D4s_v3 節點,斷路的 200/503 比例是這台這一次的結果;要拿它比效能(Day 4),得先擴到兩個節點,免得單節點的排程壓力污染數字。
- **kube-proxy 仍與 Cilium 併存**(Day 1 記過)。ambient 這天沒受影響,但 Day 3 純用 Cilium 的 L7 時要再確認這件事。
- **沒量 ambient 的資源開銷**。ztunnel 與 waypoint 佔多少 CPU/記憶體,今天沒量,留到 Day 4 的橫向對比。

## 帶得走的東西

- **ambient 把 mTLS 的位置從「每個 pod」搬到「每個節點」**。ztunnel 在節點層把明文連線換成節點間的 HBONE 加密隧道,應用不用改、pod 不用重啟——這是它跟 sidecar 最實際的差別。
- **「加密」和「身分」是兩件事,mTLS 一次給你兩件**。ztunnel 紀錄裡的 `15008` 是加密(HBONE 隧道),`spiffe://…` 是身分(誰對誰)。看 mesh 的流量,這兩個要分開看。
- **L4 的事 ztunnel 就夠,L7 的事要 waypoint**。自動 mTLS 是 L4,ztunnel 獨力完成;斷路、依 HTTP 內容路由是 L7,要多佈一個 waypoint。分不分得出來,決定你需不需要 waypoint。
- **斷路要有明確訊號才算數**。「有些請求 503」不夠——`x-envoy-overloaded: true` 才證明是斷路擋的、不是後端自己掛的。驗收一個機制,要抓到它專屬的訊號。

## 延伸閱讀

- **[Istio Ambient 總覽](https://istio.io/latest/docs/ambient/overview/)** —— ztunnel(L4)與 waypoint(L7)的分工、HBONE 隧道是什麼,官方的一手說明。
- **[Istio Ambient 與 Cilium 的共存設定](https://istio.io/latest/docs/ambient/install/platform-prerequisites/)** —— 步驟 1 那組讓 ambient 疊在 Cilium 上的前置(`cni.exclusive`、socket LB、BPF masquerade),踩之前先讀。
- **[Istio circuit breaking 任務頁](https://istio.io/latest/docs/tasks/traffic-management/circuit-breaking/)** —— 斷路用的 `DestinationRule` 連線池與 outlier detection 欄位;`x-envoy-overloaded` 這個訊號的出處。

## 下一步

east-west 這層,Istio 這條路走完了:mTLS 自動、斷路可設,而且不用 sidecar。[Day 3](sprint4-day3-cilium-mesh.md) 換 **Cilium 自己的 mesh** 做**同一組驗收**——服務間加密、身分、一個 L7 介入——看它怎麼用 eBPF 做到、哪裡跟 Istio 不一樣。兩邊都跑完,Day 4 才有得比。

開工前得先把 Istio 乾淨拆掉,不然殘留的 ztunnel/waypoint 會污染 Cilium 的量測——那是 Day 3 開頭第一件事。

---

!!! quote ""
    Istio 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
