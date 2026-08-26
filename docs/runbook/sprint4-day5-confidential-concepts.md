# Day 5: 機密運算的威脅模型——Kata 與 CoCo 的定位

![Kubernetes 官方標誌](../assets/logos/kubernetes-icon-color.svg){ align=right width="80" }

> Part 1 把流量邊界交給基礎設施:eBPF 在核心看封包、mesh 在 L7 管加密與路由。共同的前提是**主機看得到工作負載在做什麼**——愈看得清楚愈好治理。Part 2 換一個威脅模型:當節點本身、連 root、連雲廠商都不該被信任時,怎麼讓 pod 的記憶體**連主機都讀不到**。這一章不裝東西,先把「防的是誰」講清楚,因為它跟前四天的立場剛好相反。

!!! abstract "你在課程的哪裡"
    - **來時路**:Part 1(Day 0–4)——eBPF 拿下 L4、mesh 管 L7,基礎設施接管了「流量」這件事。
    - **今天**:純概念。機密運算(Confidential Computing)防的是誰、Kata 與 CoCo 的關係與治理邊界、以及機密運算跟 eBPF 在「主機該不該看得到工作負載」上正好相反的立場。
    - **接下來**:[Day 6](sprint4-day6-kata-sandboxing.md) 動手把 pod 跑進獨立 kernel 的輕量 VM(Kata),[Day 7](sprint4-day7-katacc.md) 再加上硬體記憶體加密(kata-cc)。

## 先分清楚兩種「隔離」在防誰

「把 pod 隔離起來」這句話,在 Part 2 有兩個完全不同的意思,而它們**保護的方向相反**。

### 一般 Kata:保護主機不被 guest 害

早年的容器共用主機 kernel;一個容器踩到 kernel 漏洞逃逸,就能碰到主機與其他容器。**Kata Containers 把每個 pod 裝進一台獨立 kernel 的輕量 VM**,逃逸要先突破 VM 邊界。它的威脅模型講得很直白——官方原文說 Kata 要「**prevent an untrusted container workload or user … to gain control of, obtain information from, or tamper with the host infrastructure**」。

看清楚方向:**攻擊者是容器裡的工作負載,被保護的是主機**。主機是被信任的一方;Kata 不隱藏 guest 的記憶體不讓主機看——那不是它的目標。

### 機密運算(CoCo):保護 guest 不被主機看

機密運算把箭頭反過來。它假設**主機不可信**——主機作業系統、hypervisor、kubelet、containerd,甚至雲端營運商,全部列為 untrusted。CoCo 的信任模型原文:「Everything on the host outside of the enclave is **untrusted**. This includes the Kubelet, CRI runtimes like containerd, **the host kernel**…」,並提供「**the untrusted host cannot access guest data**」的技術保證。

做到這件事靠的是 **CPU 的硬體記憶體加密**(AMD SEV-SNP 或 Intel TDX):guest 的記憶體內容在實體 RAM 上就是密文,主機拿得到那塊記憶體,但沒有金鑰、解不開。

一句話對照——

| | 一般 Kata | 機密運算(CoCo) |
|---|---|---|
| 攻擊者假設是誰 | 容器裡的工作負載 | **主機 / root / hypervisor / 雲營運商** |
| 被保護的是誰 | **主機**與其他 pod | **guest 的記憶體明文** |
| 主機被信任嗎 | 是 | **否** |
| 靠什麼 | 獨立 kernel 的 VM | VM + **CPU 硬體記憶體加密** |

!!! warning "措辭要準:是「讀不到明文」,不是「完全看不到」"
    硬體加密的是記憶體的**明文可讀性**。主機仍拿得到那塊記憶體的密文、也看得到 metadata——只是沒有金鑰、解不出內容。教材與對話都用「主機讀不到 guest 記憶體**明文**」,別講成「主機完全看不到」,那會被較真的人抓到。

## CoCo 站在 Kata 上,而它們分屬兩個基金會

機密容器不是從零長出來的,它疊在 Kata 上:Azure 官方講得很清楚——「With many TEE technologies requiring a boundary between the host and guest, **Kata Containers are the basis for the Kata CoCo initial work**」。Day 6 的 Kata 底座,Day 7 換一個 handler 就變成 kata-cc。

有一個治理上的張力值得記:

- **Confidential Containers(CoCo)** 是 **CNCF** 專案,2022-03-08 進 CNCF,**2026-07-08 升到 Incubating**、**2026-07-22 CNCF 發公告**(生效日與公告日不同,引用時分清楚)。
- **Kata Containers** 隸屬 **Open Infrastructure Foundation**(前身 OpenStack Foundation),2019 年成為它第一個從 pilot 畢業的頂層專案。

也就是說,**一個 CNCF 專案的執行底座,住在另一個基金會**。這不是問題,但評估長期依賴時要知道責任分屬兩邊。

## eBPF 與 CoCo:對主機可見性的相反立場

把 Part 1 和 Part 2 並排,會浮出一個乾淨的對立:

- **eBPF(Part 1 / Sprint 2)讓主機看清楚工作負載**——主機核心觀測、治理 guest 的 syscall、封包、流量。看得愈清楚,治理愈有力。
- **CoCo(Part 2)要「讓主機看不到 guest」**——主機被列為 untrusted,連記憶體明文都不給。

**兩者對「主機該不該看得到工作負載」的立場正好相反。** 一個把治理能力建立在可見性上,一個把安全保證建立在不可見性上。這正是把「服務網格(流量邊界)」和「機密運算(記憶體邊界)」放進同一個 sprint 的回報——它們是同一個問題「基礎設施跟工作負載的信任關係」的兩個極端。

## 兩個硬體名詞,一個流程名詞

後面幾天會一直用到,先立起來:

- **SEV-SNP(AMD)與 TDX(Intel)**:都是 **VM 層級的 TEE**,用 CPU 硬體加密整台 guest VM 的記憶體、把 hypervisor 排除在信任邊界外。差別在實作——SEV-SNP 用 AMD Secure Processor 管的 per-VM 金鑰加密,SNP 擴充再加記憶體完整性(擋 hypervisor 的 page remap/replay);TDX 建一個隔離的 Trust Domain,用硬體擴充加密與檢查它的記憶體。本課的硬體走 **AMD SEV-SNP**。
- **遠端證明(remote attestation)**:向遠端第三方**以密碼學證明**這個工作負載跑在真的 TEE 裡、而且組態符合預期,通過了才把祕密交給它。CoCo 的原文:「Before a confidential workload is granted access to sensitive data, it should be **attested**」——證明先行,拿祕密在後。這是 [Day 8](sprint4-day8-attestation.md) 的主題,也是機密運算真正有用的一步:不是「跑起來」,是「證明給遠端看它值得信任」。

## 驗收 checkpoint

這一章沒有指令可跑,驗收是三個你要能自己答出來的問題——每題都要命中關鍵字才算過。

| 驗證 | 判準(答案要點) |
|---|---|
| **機密運算防的是誰** | 主機 / root / hypervisor / 雲廠商**讀不到 guest 記憶體明文**;並能跟一般 Kata sandboxing 的「防逃逸、保護主機、不防主機窺視」分清楚 |
| **Kata 與 CoCo 的關係與治理** | **CoCo 以 Kata 為底座**;**CoCo = CNCF、Kata = OpenInfra**,執行底座在另一個基金會 |
| **eBPF 與 CoCo 的對立** | eBPF 讓主機看清楚 guest、CoCo 讓主機讀不到 guest 記憶體,兩者對主機可見性**立場相反** |

三題答得出來,表示這一章的主要內容吸收了。

## 帶得走的東西

- **「隔離」在 Part 2 有兩個相反的方向。** 一般 Kata 保護主機不被 guest 害(主機被信任);機密運算保護 guest 不被主機看(主機不被信任)。看到「confidential」要先問一句:防的是逃逸,還是防主機窺視?
- **機密運算加密的是明文可讀性,不是「消失」。** 主機拿得到密文與 metadata,只是沒金鑰解不開。講「讀不到明文」比講「看不到」準。
- **CoCo 疊在 Kata 上,而兩者分屬 CNCF 與 OpenInfra。** 執行底座跟專案在不同基金會,是評估長期依賴時該知道的事。
- **可見性是能力也是風險,看你站哪一邊。** eBPF 把治理建立在看得清楚;CoCo 把安全建立在看不到。同一個 sprint 收了這兩端,不是湊在一起,是刻意對照。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[CoCo 的信任模型](https://confidentialcontainers.org/docs/architecture/trust-model/trust-model/)** —— 「防的是誰」最硬的一手依據:host kernel、hypervisor、kubelet、containerd 全被列為 untrusted,以及「untrusted host cannot access guest data」的保證。
- **[Kata Containers 的威脅模型](https://github.com/kata-containers/kata-containers/blob/main/docs/threat-model/threat-model.md)** —— 對照組。它保護的是主機不被 guest 危害,跟 CoCo 的方向正好相反,兩份並讀最清楚。
- **[AKS Confidential Containers 總覽](https://learn.microsoft.com/en-us/azure/aks/confidential-containers-overview)** —— 本課實作的一手來源:kata-cc 的透明度/可稽核/完整證明/隔離設計,以及「只支援 Azure Linux」等限制。
- **[CoCo 升 CNCF Incubating 的公告](https://www.cncf.io/blog/2026/07/22/confidential-containers-becomes-a-cncf-incubating-project/)** —— 治理現況;注意 7/8 是生效日、7/22 是公告日。

## 下一步

概念立好了,從最基本的隔離開始動手。[Day 6](sprint4-day6-kata-sandboxing.md) 用 **Kata Pod Sandboxing** 把一個 pod 跑進獨立 kernel 的輕量 VM——先證明「不共用主機 kernel」是什麼感覺,而它走的還是 Sprint 3 那條 RuntimeClass → handler → shim 的老路,只是換一個 handler。硬體記憶體加密留到 [Day 7](sprint4-day7-katacc.md)。

---

!!! quote ""
    Kubernetes 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
