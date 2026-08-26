# Day 7: kata-cc——SEV-SNP 節點、安全政策與平台證據

![Kubernetes 官方標誌](../assets/logos/kubernetes-icon-color.svg){ align=right width="80" }

> [Day 6](sprint4-day6-kata-sandboxing.md) 的 Kata pod 有自己的 kernel,但那台 VM 是一般 VM——主機讀得到它的記憶體明文。今天把 handler 換成 `kata-cc`,節點換成 **AMD SEV-SNP 的 `_cc_v5` 機密硬體**,讓 pod 跑進一台**記憶體被 CPU 硬體加密**的 VM。機制還是那條 RuntimeClass 鏈,但這一天真正的內容是:換到機密硬體之後,一連串「這個區沒賣、這個組合起不來、這個工具在 macOS 不能跑」的現實,才是要學的東西。

!!! abstract "你在課程的哪裡"
    - **[Day 6](sprint4-day6-kata-sandboxing.md)**:Kata pod 跑進獨立 kernel 的輕量 VM(一般 VM,主機讀得到記憶體)。
    - **今天**:換 `kata-cc` handler + SEV-SNP `_cc_v5` 硬體,證明 pod 跑在硬體記憶體加密的機密 VM 裡,並取得平台層的證據。
    - **接下來**:[Day 8](sprint4-day8-attestation.md) 做真正重要的一步——遠端證明,讓祕密只在通過證明的 pod 裡解開。

## 一樣是換 handler,但硬體會挑你

kata-cc 的機制跟 Day 6 一樣是換一個 handler(`kata-cc-isolation` → `io.containerd.kata-cc.v2` shim);差別在硬體:kata-cc 的 pod 是跑在**巢狀的 SEV-SNP 機密 child VM** 裡,節點必須是「能起這種 child VM」的 **`_cc_v5`** 系列(Azure 叫 DCa_cc / ECa_cc,例如 `Standard_DC4as_cc_v5`)。這跟 Day 5 講的「整段記憶體 CPU 硬體加密」對得起來。

聽起來只要換個 SKU,但這一天卡點特別多,而每個卡點都是教材:

| 卡點 | 一句話 | 地雷 |
|---|---|---|
| 區域 | 東京(japaneast)根本沒賣 `_cc_v5` | [1](#mine-1) |
| 叢集結構 | kata-cc 當「系統池」節點 bootstrap 直接 404 | [2](#mine-2) |
| pod 起動 | kata-cc pod 沒有安全政策就起不來 | [3](#mine-3) |
| 產政策 | 產政策的工具在 macOS 沒實作 | [4](#mine-4) |
| 觀測 | 預設政策擋 `exec`/`logs`,進不去看 | [5](#mine-5) |

## 步驟 1: 先找哪個區有貨

Part 1 的叢集在東京。直覺是直接在它上面加 kata-cc 節點池——結果第一步就撞牆([地雷 1](#mine-1)):**東京這個訂閱完全沒有 `_cc_v5`**,只有一般的機密 VM(`DCas_v5`,整台當機密 VM,不是 kata-cc 要的可巢狀型)。

查哪個區有,別用 `az vm list-skus --all`(全列舉會慢到逾時);用 `--size` 前綴針對單一 SKU 問:

```console
$ az vm list-skus -l westeurope --size Standard_DC4as_cc_v5 \
    --query "[?name=='Standard_DC4as_cc_v5'].locationInfo[0].zones" -o tsv
3	1	2                     ← westeurope 有,還有三個可用區
```

所以 kata-cc 這段只能在有貨的區做,本課搬到 **westeurope 另建一座叢集**(Part 1 那座東京叢集沒有 `_cc_v5`)。**指定機型的教學,天生綁區域**——而區域供貨會變,截至 **2026-08**,`_cc_v5` 在東京查無、西歐有。

## 步驟 2: 標準系統池 + kata-cc 當 user 池

直覺會想「一座叢集,系統池直接開成 kata-cc 不就好了」。實測會拿到節點 bootstrap 的 **404**([地雷 2](#mine-2))。能穩定起來的做法是官方「既有叢集」那條路:**先建一座標準系統池的叢集(啟用 OIDC 與 workload identity,Day 8 要用),再把 kata-cc 加成 user 池**,並釘 K8s 1.34(避開 1.35.6 的 kata-cc 系統池 404):

```bash
# 1) 標準系統池(Ubuntu 小節點)+ OIDC + workload identity
az aks create -g <rg> -n <cluster> --location westeurope --tier free \
  --kubernetes-version 1.34 \
  --os-sku Ubuntu --node-vm-size Standard_D2as_v5 --node-count 1 \
  --enable-oidc-issuer --enable-workload-identity

# 2) kata-cc 當 user 池(AzureLinux + KataCcIsolation + DC4as_cc_v5)
az aks nodepool add -g <rg> --cluster-name <cluster> --name katacc --mode User \
  --os-sku AzureLinux --workload-runtime KataCcIsolation --node-vm-size Standard_DC4as_cc_v5

# 3) 官方要求:加池後跑一次 update 才會啟用 CC 功能(RuntimeClass 這時才出現)
az aks update -g <rg> -n <cluster>
```

節點起來後,kata-cc 節點是真的 SEV-SNP 硬體、`kata-cc-isolation` RuntimeClass 就位:

```console
$ kubectl get node -l agentpool=katacc -o wide
aks-katacc-…   Ready   CBL-Mariner/Linux   5.15.157.mshv1-2.cm2   containerd://1.7.7

$ kubectl get runtimeclass | grep kata-cc
kata-cc-isolation   kata-cc    ← az aks update 之後才出現
```

## 步驟 3: pod 要有安全政策才起得來

丟一個 `runtimeClassName: kata-cc-isolation` 的 pod 下去,卡在 `ContainerCreating`:

```console
Failed to create pod sandbox: … UpdateInterfaceRequest: internal error query did not produce any values
```

這不是網路壞了,是**缺安全政策**([地雷 3](#mine-3))。kata-cc 的 guest agent 用一份 OPA/rego 政策決定允許哪些請求;沒有政策,連設定網路介面都被擋。政策要用 `az confcom katapolicygen` 產、注入 pod 的 `io.katacontainers.config.agent.policy` annotation。

而產政策的工具**在 macOS 沒實作**([地雷 4](#mine-4)):

```console
$ az confcom katapolicygen -y pod.yaml
ERROR: The katapolicygen subcommand for MacOS has not been implemented.
```

繞法是用 Linux 產。本課用 **podman 的 `--platform linux/amd64`** 跑一個 `mcr.microsoft.com/azure-cli` 容器,把 manifest 從 stdin 餵進去、政策化後的 YAML 從 stdout 拿出來:

```bash
podman run --rm --platform linux/amd64 -i mcr.microsoft.com/azure-cli sh -c '
  az extension add -n confcom -y
  cat > /tmp/pod.yaml
  az confcom katapolicygen -y /tmp/pod.yaml
  cat /tmp/pod.yaml
' < pod.yaml > pod-policy.yaml
```

注入政策後再套用,pod 就起得來:

```console
$ kubectl -n cc-lab get pod cc-pod -o wide
cc-pod   1/1   Running   …   runtimeClass=kata-cc-isolation   node=aks-katacc-…
```

**帶政策 17 秒內 Running,無政策卡 sandbox——政策是起不起得來的開關。** 但預設產出的政策把 `ExecProcessRequest` 設成 `false`,所以 `kubectl exec`、`kubectl logs` 都進不去這個 pod([地雷 5](#mine-5))。今天不需要進去——Day 7 的證據在平台層。

## 步驟 4: 平台層的機密證據

計畫刻意不押 guest 內部的 SNP report(AKS 未必拿得到);Day 7 的證據是「這是真的 SEV-SNP 執行路徑」:

```console
# 節點:AzureLinux/Mariner、SEV-SNP DC4as_cc_v5
# containerd 設定裡,kata-cc handler 指向 Cloud Hypervisor + SEV-SNP 的設定
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-cc]
  snapshotter = "tardev"
  runtime_type = "io.containerd.kata-cc.v2"
  [.kata-cc.options] ConfigPath = "…/configuration-clh-snp.toml"
                                                        ↑ clh = Cloud Hypervisor,snp = SEV-SNP
```

pod 跑在 `kata-cc-isolation`、節點是 `DC4as_cc_v5`、shim 走 `clh-snp` 設定——**平台證據齊了**。密碼學層級的「證明它真的加密、而且沒被竄改」,是 [Day 8](sprint4-day8-attestation.md) 的 MAA/SKR。

## 換到別家雲,硬體條件長怎樣

kata-cc 綁 Azure 的 `_cc_v5`,但機密運算不是 Azure 專屬。同一個需求(pod 記憶體對主機加密)在各家的硬體與服務前提:

| 平台 | 硬體 / 機型 | 前提 |
|---|---|---|
| **Azure**(本課) | `DCa_cc` / `ECa_cc`(`DCasccv5`/`DCadsccv5`,如 `DC4as_cc_v5`),AMD **SEV-SNP** 可巢狀 child VM | AKS kata-cc 需這系列;`DCsv2`(Intel SGX,行程層)**不算**;`DCas_v5`(整台 CVM)也不是 kata-cc 要的可巢狀型 |
| **GCP** | Confidential VM(AMD SEV / SEV-SNP、Intel TDX);GKE Confidential Nodes | GKE 開機時選 confidential;kata 型機密容器與 Azure 路徑不同 |
| **AWS** | Nitro Enclaves(隔離的 enclave,非整 VM 加密的相同模型);部分實例支援 AMD SEV-SNP | Nitro Enclaves 的信任模型與 SEV-SNP CVM 不完全等價,對照時要分清 |
| **OpenStack / 地端裸機** | 需 CPU 本身支援 SEV-SNP 或 TDX 的實體機;搭 vTPM 做度量與證明 | 自己備硬體與韌體;沒有雲廠商的受管 attestation,要自架 KBS/驗證方 |

**要點是:機密運算永遠綁「CPU 有沒有那個硬體功能」,而各家把它包成不同的機型與服務名。** 挑平台前先確認你要的區域、實例、以及可不可以巢狀(kata 型機密容器需要巢狀 child VM)。本課只在 Azure 實測,跨雲僅供對應。

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| kata-cc pod 跑起來 | pod 在 `kata-cc-isolation` 上 Running | 帶政策 17 秒 Running(無政策卡 sandbox) |
| 平台是真 SEV-SNP | 節點 SKU、RuntimeClass、shim 設定 | `DC4as_cc_v5` + `kata-cc-isolation` + `clh-snp` 設定 |

## 地雷記錄

### 地雷 1:kata-cc 要 `_cc_v5`,而不是每個區都賣 {#mine-1}

**症狀**:`az aks nodepool add --node-vm-size Standard_DC4as_cc_v5` 回 `VMSizeNotSupported … in location japaneast`。
**根因**:kata-cc 需要「能起巢狀 SEV-SNP child VM」的 `_cc_v5`;東京只有一般 `DCas_v5`(整台 CVM,不可巢狀)。
**查法**:`az vm list-skus -l <region> --size Standard_DC4as_cc_v5`(**別用 `--all`**——全列舉會逾時,而且它對 preview SKU 本來就不可靠)。截至 2026-08,westeurope、northeurope 有。
**修法**:在有貨的區另建叢集。指定機型的教學綁區域,這是硬成本。

### 地雷 2:kata-cc 當系統池,節點 bootstrap 直接 404 {#mine-2}

**症狀**:`az aks create … --workload-runtime KataCcIsolation`(kata-cc 當初始/系統池)→ `VMExtensionProvisioningError: CSE … K8SDownloadTimeout … curl 404`,節點沒 join。
**修法**:**標準系統池 + kata-cc 當 user 池**、K8s 釘 1.34。對比 Day 6 的 `KataVmIsolation` 在 1.35.6 是好的——`kata-cc` 這條 preview 路徑對版本/池角色更挑。

### 地雷 3:kata-cc pod 沒有安全政策就起不來 {#mine-3}

**症狀**:`FailedCreatePodSandBox … UpdateInterfaceRequest: internal error query did not produce any values`,永遠 `ContainerCreating`。
**根因**:kata-cc 的 guest agent 用 rego 政策決定允許哪些 agent 請求;沒政策=預設拒絕,連網路介面設定都擋。
**修法**:`az confcom katapolicygen` 產政策注入 annotation。

### 地雷 4:`katapolicygen` 在 macOS 沒實作 {#mine-4}

**症狀**:macOS 跑 → `The katapolicygen subcommand for MacOS has not been implemented.`
**修法**:用 Linux 產。本課用 podman `--platform linux/amd64` 跑 azure-cli 容器(manifest 走 stdin、政策化 YAML 走 stdout)。沒有 podman 就用叢集裡的 Linux 節點跑一個 azure-cli pod。

### 地雷 5:預設政策擋 `exec` 與 `logs` {#mine-5}

**症狀**:政策化的 pod 起得來,但 `kubectl exec`、`kubectl logs` 都被擋。
**根因**:預設產出的政策 `ExecProcessRequest`、`ReadStreamRequest` 都是 `false`(官方明載,是刻意的安全預設)。
**判斷準則**:要進去看或讀 log,得在產政策時開這些請求再重產。Day 7 靠平台證據,不需要進去;Day 8 的觀測改走 pod 自己對外服務一個 TCP 埠。

## 帶得走的東西

- **指定機型的教學綁區域。** kata-cc 要 `_cc_v5`,而它不是每個區都賣——東京沒有、西歐有。挑機密硬體之前先確認區域、實例、可不可巢狀。
- **preview 路徑對「池角色」和「版本」很挑。** 同樣是 Kata,`KataVmIsolation` 在 1.35.6 當任意池都好;`KataCcIsolation` 當系統池 + 新版就 404。穩的做法是標準系統池 + kata-cc user 池。
- **kata-cc pod 的「開關」是安全政策,不是設定檔。** 沒政策連 sandbox 都建不起來;政策同時是 Day 8 那個「竄改就拿不到祕密」的根據。
- **控制機器的 OS 會擋你。** `katapolicygen` 在 macOS 直接不給跑——但用一個 Linux 容器就繞過去了。碰到「這工具只有 Linux」時,podman/一個 Linux pod 常常就是解法。
- **平台證據夠證明「跑在機密硬體上」,不需要鑽進 guest。** SKU + RuntimeClass + shim 設定就是路徑證據;密碼學層級的證明留給遠端證明。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[AKS Confidential Containers 部署](https://learn.microsoft.com/en-us/azure/aks/deploy-confidential-containers-default-policy)** —— 今天的一手配方:`KataCcIsolation`、`--os-sku AzureLinux`、`_cc_v5` VM、`az aks update` 啟用、`kata-cc-isolation` RuntimeClass。
- **[DCas_cc_v5 機型系列](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dcasccv5-series)** —— `_cc_v5` 的規格與「可巢狀 SEV-SNP child VM」的定義,地雷 1 的一手來源。
- **[confcom / katapolicygen](https://learn.microsoft.com/en-us/azure/confidential-computing/confidential-containers-aks-security-policy)** —— 安全政策是什麼、怎麼產、為什麼 kata-cc pod 一定要它。

## 下一步

今天證明了 pod 跑在真的 SEV-SNP 硬體上,但那還只是「平台這麼說」。機密運算真正的價值不是「跑起來」,是**證明給遠端看它值得信任,再把祕密交給它**。[Day 8](sprint4-day8-attestation.md) 接上 MAA 遠端證明與 SKR sidecar:一個 pod 只有在硬體證明「我跑著預期的程式碼」時才拿得到金鑰,而**改一個環境變數就會讓它拿不到**。

---

!!! quote ""
    Kubernetes 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
