# Sprint 5 · 遊戲伺服器平台

多人遊戲的 dedicated game server 和一般 pod 的管理方式不同：玩家連線綁在特定 pod，封包多半走 UDP，對戰中途被驅逐就是斷線。這個 sprint 在 AKS 上依序組出一套遊戲伺服器平台：用 **Agones** 管理 GameServer 的生命週期與分配，用 **Quilkin** 依 token 把 UDP 封包轉送到正確的 server，用 **Nakama** 與 PostgreSQL 處理登入、儲存與配對，最後用 Python 與 Unity client 走完從登入、配對到連進 GameServer 的流程。

<div style="text-align: center;" markdown>

[![Agones](../assets/logos/agones-icon-color.svg){ width="88" }](https://agones.dev/)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Quilkin](../assets/logos/quilkin-mascot.png){ width="88" }](https://github.com/EmbarkStudios/quilkin)
&nbsp;&nbsp;&nbsp;&nbsp;
[![Nakama](../assets/logos/nakama-icon-color.png){ width="72" }](https://heroiclabs.com/docs/nakama/)

*Agones 管理 game server 的生命週期與分配，Quilkin 把 UDP 封包送到正確的 server，Nakama 處理玩家身分與配對。*

</div>

一場對戰在這套平台上的流程是：玩家向 Nakama 登入，透過 WebSocket 排隊；配對成立時，Nakama 的 hook 向 Agones 要一顆 server 並產生 routing token；玩家拿到入口後，UDP 封包帶著 token 經 Quilkin 送進 GameServer；對戰結束時 server 呼叫 `Shutdown()`，Fleet 補上新的 server。Day 1 到 Day 9 把這條流程一段一段接起來。

Day 1 到 Day 3 只用 Agones，玩家直接連節點 IP；Day 4 起依序加上 Quilkin 與 Nakama。Day 9 需要在本機安裝 Unity 6000.3.19f1（Personal 授權即可）。

## 課程路線（Day 0–10）

<div class="grid cards" markdown>

-   ![Agones](../assets/logos/agones-icon-color.svg){ width="44" }

    **Day 0 · [遊戲伺服器平台概念](../runbook/sprint5-day0-game-server-concepts.md)**

    ---

    不需要安裝。說明 dedicated game server 為什麼不適合用一般的 Kubernetes 排程管理、Agones 的 `GameServer`、`Fleet`、`GameServerAllocation` 如何分工，以及玩家從登入到進入對戰會經過哪些服務與協定。

-   ![Agones](../assets/logos/agones-icon-color.svg){ width="44" }

    **Day 1 · [Agones 第一顆 GameServer](../runbook/sprint5-day1-agones-first-gameserver.md)**

    ---

    建立有節點公網 IP 的節點池並安裝 Agones，跑起第一顆 `GameServer`，再從叢集外用 UDP 直連節點 IP 與 hostPort，收到 server 的 echo 回應。

-   ![Agones](../assets/logos/agones-icon-color.svg){ width="44" }

    **Day 2 · [Fleet 生命週期](../runbook/sprint5-day2-fleet-lifecycle.md)**

    ---

    用 `Fleet` 常備一組 Ready 的 game server，觀察從 `Scheduled` 到 `Ready` 的狀態轉移；刪掉其中一顆後，確認 Fleet 會自動補上新的 server。

-   ![Agones](../assets/logos/agones-icon-color.svg){ width="44" }

    **Day 3 · [Allocation 與 autoscaler](../runbook/sprint5-day3-allocation-autoscaler.md)**

    ---

    用 `GameServerAllocation` 把一顆 Ready 的 server 分配給玩家並取得連線位址，再用 `FleetAutoscaler` 的 buffer policy 維持固定數量的閒置 server。

-   ![Quilkin](../assets/logos/quilkin-mascot.png){ width="48" }

    **Day 4 · [Quilkin UDP proxy](../runbook/sprint5-day4-quilkin-udp-proxy.md)**

    ---

    在 Agones 前面部署 Quilkin 的 xDS control plane 與 proxy，讓 client 改連同一個 UDP LoadBalancer，由 Quilkin 依封包裡的 token 轉送到被分配的 server；帶錯誤 token 的封包會被擋下。

-   ![Nakama](../assets/logos/nakama-icon-color.png){ width="40" }

    **Day 5 · [Nakama 與 PostgreSQL](../runbook/sprint5-day5-nakama-postgres.md)**

    ---

    部署 PostgreSQL 與 Nakama，完成資料庫 migration，用 REST device auth 取得 session token，並寫入與讀回 storage 資料。

-   ![Nakama](../assets/logos/nakama-icon-color.png){ width="40" }

    **Day 6 · [Matchmaking 串接](../runbook/sprint5-day6-matchmaking.md)**

    ---

    撰寫 Nakama 的 Lua runtime module 與對應的 RBAC，讓配對成立時由 `matchmaker_matched` hook 向 Kubernetes API 建立 `GameServerAllocation`，再把同一組 server 位址推送給配到的玩家。

-   ![Nakama](../assets/logos/nakama-icon-color.png){ width="40" }

    **Day 7 · [Python client 端到端](../runbook/sprint5-day7-python-client-e2e.md)**

    ---

    讓 hook 在 allocation 時產生 routing token，並提供查詢分配結果的 RPC；再用只依賴 Python 標準函式庫的 client，走完登入、配對、RPC 查詢，到 UDP 經 Quilkin 連進 GameServer 的整段流程。

-   ![Agones](../assets/logos/agones-icon-color.svg){ width="44" }

    **Day 8 · [觀測與韌性](../runbook/sprint5-day8-observability-resilience.md)**

    ---

    用 Prometheus 收集 Agones controller 與 Quilkin proxy 的 metrics，觀察 GameServer 正常關閉、程序崩潰與節點失效時的狀態轉移，以及 metrics 多久才反映；也確認 Fleet 縮容只刪 Ready 的 server，進行中的對戰不受影響。

-   ![Nakama](../assets/logos/nakama-icon-color.png){ width="40" }

    **Day 9 · [Unity client 與 relay server](../runbook/sprint5-day9-unity-client.md)**

    ---

    用 Unity 與官方 nakama-unity SDK 重做 Day 7 的流程，全程用 Unity 命令列建專案與 build；再部署一個用 Go 寫的 relay server，讓兩個 Unity 視窗互相看到對方的角色移動。

-   ![Agones](../assets/logos/agones-icon-color.svg){ width="44" }

    **Day 10 · [遊戲伺服器平台總結](../runbook/sprint5-day10-decision-matrix.md)**

    ---

    不動手。回顧一場對戰從登入到結束經過的元件，說明哪些類型的遊戲需要這套架構、元件可以怎麼分階段導入，以及這門課沒有涵蓋的主題，例如遊戲網路同步、權威 server 與多區域部署。

</div>

## 學完之後

- 能說明 dedicated game server 為什麼不能當一般 pod 管理，以及 Agones 用哪些物件處理這些需求。
- 能從 Agones 直連節點開始，依需求加上 Quilkin 的固定入口與 token 路由、Nakama 的登入與配對。
- 知道 server 正常關閉、崩潰與節點失效時平台各會怎麼反應，並據此決定監控要看哪些訊號。

從 [Day 0](../runbook/sprint5-day0-game-server-concepts.md) 開始。

---

!!! quote ""
    Agones 標誌為 CNCF（Linux Foundation）官方資產；Quilkin 吉祥物為 Embark Studios 之官方資產；Nakama 標誌為 Heroic Labs 之官方資產。此處皆作社群教學用途。
