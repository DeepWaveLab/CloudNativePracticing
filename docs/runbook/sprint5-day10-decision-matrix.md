# Day 10: 新遊戲需要哪些平台元件，哪些還不用？

![Agones 官方標誌](../assets/logos/agones-icon-color.svg){ align=right width="90" }

團隊準備把一款遊戲放進 Kubernetes，帳號服務已經存在。有人希望直接沿用這幾天的 Agones、Quilkin 與 Nakama；另一個需求卻只是讓玩家輪流出牌，每一步的狀態都能寫回資料庫。這兩種情況，需要同一套部署嗎？

先回答一個更具體的問題：一場對戰是否需要持續由某個程序保存狀態，玩家也必須一直連到它？本章用前面實際看到的分配、路由與失效行為，整理哪些需求對應哪些元件。最後應能為自己的遊戲選出起點，也能說出選擇前還欠哪些證據。

## 先畫出狀態放在哪裡

Day 5 的玩家資料放在 PostgreSQL，Nakama 接收登入與 storage 請求；Day 9 的 relay 則在記憶體裡保存目前有哪些玩家加入。這些資料的生命週期不同：帳號需要跨越多場對戰，relay 的連線名單只服務這一場。

本課的實作可以分成圖中的三段。先看中間的交接資料，再看右邊的遊戲程序；「拿到 assignment」與「能繼續原本對戰」並不是同一件事。

![玩家身分與配對交付 assignment，UDP 路由連到對戰程序，結束後釋放容量](../assets/diagrams/s5d10-boundaries.svg)

assignment 裡保存的是去哪裡連線，不是對戰狀態的備份。Day 8 關閉節點後，Fleet 補出新的 Ready，舊 token 的探測卻一直 timeout；這就是兩者的差別。若需求是「換一台 server 後仍接續對戰」，需要另外設計狀態保存與恢復，這幾天的 allocation 與 token 並沒有提供它。

## 判斷是否需要 Agones：看對戰生命週期

如果每次請求都能從外部資料庫取得完整狀態，任一副本都能接手下一次請求，那麼先用一般 Deployment 管理服務是可評估的起點。若一組玩家需要分配並持續使用同一個遊戲程序，還要區分「可以接新對戰」與「正在服務對戰」，就需要額外的生命週期管理。

Agones 在本課解決的是後者：`GameServerAllocation` 選取 Ready server 並轉為 Allocated，Fleet 維持伺服器數量，遊戲程式以 SDK 回報就緒、健康與結束。Day 8 把 Fleet replicas 調到 0 時，Allocated server 仍保留，正好說明為什麼一般副本數不足以描述一場進行中的對戰。

不要只用「卡牌」或「射擊」決定。回合制遊戲也可能有綁定程序的房間狀態；即時遊戲也可能採用不同的分散方式。先追一場對戰的狀態在哪裡，以及移除程序會失去什麼，再選管理方式。[Fleet 規格](https://agones.dev/site/docs/reference/fleet/) 可以用來核對 replicas 與分配的關係。

回到開場的卡牌遊戲：若每次出牌後都把完整對戰狀態寫回資料庫，下一次請求也能由任一副本處理，最少可以先沿用帳號服務，以一般 Deployment 執行遊戲 API，搭配保存對戰狀態的資料庫。這個設計沒有必須分配給某場對戰的專用程序，可以先不導入 Agones；如果出牌也透過 API 完成，就沒有本課的 UDP 路由需求，也不用追加 Quilkin。這是依需求推導的起點，本課沒有實作這套卡牌服務。

若後來改成每個房間都要由固定程序持續保存狀態，才把 Agones 納入選項。此時要一起設計可分配容量、對戰結束回報，以及玩家未到或失聯時的處理；固定 replicas 不會因為分配而自動增加。若還要求程序失效後能接續原局，就得另做狀態保存與恢復。增加 GameServer 生命週期管理，並不會省掉這些遊戲設計工作。

## 判斷是否需要 Quilkin：看入口與路由需求

Day 1 的 client 直接連到節點位址與 GameServer port，路徑較短，也讓 client 必須知道每次分配的伺服器位置。Day 4、7 改成連到 proxy，再以 routing token 選擇 GameServer。這讓入口與後方 server 位址分開，但也增加 proxy、路由設定與觀測的工作。

若目前只需要驗證遊戲程序可以接收 UDP，直連足以開始；若需要統一入口與可設定的 UDP 路由，再評估 Quilkin。這是從本課兩種連線方式得到的設計判斷，並非效能比較：課程沒有測量加入 proxy 後的延遲分布、吞吐量或故障切換時間。

還有兩個實際限制。第一，Day 7 的三字元 token 只是練習配置，沒有碰撞處理、資格驗證與完整失效流程；錯誤 token 沒收到回應，不能據此認定已解決所有未授權流量。第二，Day 9 的 relay 看到的是 proxy 的 IP:port，並不是玩家的原始來源；如果遊戲需要原始位址或玩家身分，要另外設計如何可信地傳遞與驗證。Quilkin 的定位與可設定功能可查[官方儲存庫](https://github.com/EmbarkStudios/quilkin)。

## 判斷是否需要 Nakama：看既有後端缺哪一段

Day 5 用 Nakama 處理登入與 storage，Day 6 用 matchmaker 湊齊玩家，再由 hook 呼叫 Agones。這個接法說明：Agones 接收的是 allocation 請求，並不要求配對服務一定是 Nakama。已經有帳號、配對與授權系統的團隊，可以評估由既有後端完成同一段交接。

反過來，如果這些功能還沒建立，Nakama 提供了可一起使用的 API 與 server runtime；代價是需要理解它的資料模型、資料庫與維運方式。本課驗過 migration、單一玩家 storage，以及兩位玩家的配對，尚未涵蓋 PostgreSQL 高可用、備份還原、正式金鑰與多節點行為。選它之前，要把這些需求納入試作。[Nakama 官方文件](https://heroiclabs.com/docs/nakama/) 可用來查各項功能與部署條件。

開場只說帳號服務已存在，還不能據此決定是否採用 Nakama。先確認卡牌遊戲是否需要自動配對，以及既有後端能否提供它；若能提供，就沿用既有服務。只有在前面選了專用遊戲程序時，配對成立後才需要接上 allocation，並處理授權、重試不重複分配及失敗回報。若缺的是配對或 storage，就比較擴充既有後端與導入 Nakama 的工作量；採用 Nakama 仍須驗證權限拒絕、並行寫入、資料庫維運與對外入口，不能只因它提供登入功能就取代已有的帳號服務。

選了專用程序後，若還要讓漏接配對通知的玩家重新取得連線位置，可以保存 assignment 並提供查詢；同時要決定它何時失效，避免把玩家送回已失效的 server。

Day 7 已驗證 notification 與 RPC 回傳同一份 assignment，但沒有演練完整重連。`get_assignment` 只讀資料，不會檢查伺服器是否仍存活。需要重連的服務，不能把「查得到」當成「還能連」。

## 判斷遊戲程序做到哪裡：echo、relay 與權威狀態

Day 9 換成 Unity SDK 後，登入、配對與 UDP 的分工沒有改變。真正讓兩位玩家看見彼此的，是 GameServer 由 echo 改成 relay。這提供一個清楚的驗證順序：先測路徑能通，再測資料會送到其他玩家，最後才檢查遊戲規則與同步品質。

對開場的卡牌遊戲，如果勝負必須由後端判定，遊戲 API 就要檢查出牌是否合法並計算結果；即使沒有專用 GameServer，這項責任仍然存在。若改做持續傳送位置的遊戲，試作連線時可以先用 echo 檢查封包往返，再用 relay 檢查玩家是否收到彼此資料；做到這裡仍不能判斷位置是否合法或是否有人作弊，也不能保證網路零丟包。

當需求進一步要求 server 驗證輸入並計算遊戲狀態，就需要實作權威遊戲邏輯，由 server 決定有效結果。這會增加遊戲程式與同步機制的工作，無法只靠換上 Agones、Quilkin 或 Nakama 完成；本課的 relay 尚未實作這一段。

本章提到的 relay 是 Agones 上的獨立 UDP 程序。Nakama 另有透過自身 realtime socket 轉送的 [client relayed multiplayer](https://heroiclabs.com/docs/nakama/concepts/multiplayer/relayed/)，兩者的封包路徑與生命週期不同，不能只因名字相同就互換。

## 決定前，補足與故障有關的證據

Day 8 的時間線顯示：主動結束與程序崩潰後，很快就觀察到 GameServer 狀態改變；節點失效時，玩家探測先失敗，Node 與 GameServer 的狀態才陸續更新。它們測的是不同事件，也只有這組環境的觀察，不能排成通用效能排名，更不能當成玩家恢復時間。

這次真正能帶進設計的判斷是：主動回報結束、觀察程序失敗與偵測節點失聯，各依賴不同訊號；只看 GameServer 計數會漏掉玩家已收不到回應的時段。下一次試作應同時保存 client 探測、節點、GameServer 與 metrics 的時間線，並確認補位後是恢復容量，還是也恢復了對戰狀態。

以下幾項沒有在本課驗證，若需求包含它們，就應列入下一次試作：

- **不同玩家的網路條件**：跨網路的延遲、封包遺失與亂序。Day 9 的兩個 player 都在同一台 Mac 上，client 收到遠端位置便直接更新畫面，尚未實作下列同步機制。

    插值是在已收到的兩份狀態之間算出中間畫面，讓其他玩家的移動較平順；通常需要先暫存狀態，以增加一些顯示延遲換取平順度。可接著讀 Gaffer On Games 的 [Snapshot Interpolation](https://gafferongames.com/post/snapshot_interpolation/)。

    Client 預測是先在本機依玩家輸入計算動作，不必等 server 回覆才顯示移動；收到 server 的權威狀態後，還要修正預測誤差。Gaffer On Games 的 [Networked Physics（Client-Side Prediction）](https://gafferongames.com/post/networked_physics_2004/#client-side-prediction) 說明了輸入保存與修正方式。

    延遲補償處理的是操作送達時，server 狀態已與玩家操作當下所見不同的問題。以射擊命中判定為例，server 可依操作時間回查歷史位置，再判斷是否命中；這也可能讓已躲入掩體的玩家仍被判定中彈。Gabriel Gambetta 的 [Lag Compensation](https://www.gabrielgambetta.com/lag-compensation.html) 用射擊情境說明這項取捨。

- **不同中斷來源**：節點 drain、Spot 回收通知、更新與故障切換，不能直接沿用手動關機的結果。
- **多區域與入口**：區域選擇、跨區分配、Nakama 對外 TLS，以及 proxy 容量與可用性。
- **長期資料與身分**：資料庫還原、預設金鑰替換、配對資格驗證，以及憑證更新。

## 想一想

1. 團隊已有帳號與配對服務，還想使用 Agones，需要把整個後端換成 Nakama 嗎？請指出需要接上的介面。
2. Fleet 已補回 Ready，玩家仍帶舊 token timeout，應優先檢查哪個責任範圍？
3. 兩個同機 Unity player 的收發數量相符，還缺少哪些資料，才能評估正式玩家的網路體驗？

## 小結

是否採用整套平台，取決於對戰狀態、分配、封包入口與既有後端的需求。Agones 處理 GameServer 生命週期，Quilkin 處理本課的 UDP 路由，Nakama 提供玩家與配對 API；遊戲程式仍要負責規則、同步與結束條件。先選擇能回答目前問題的組合，再用實際失敗情境驗證缺少的部分，比一次採用全部元件更容易知道問題出在哪一段。

## 延伸閱讀

想再深入，可以從這幾份開始：

- [Agones overview](https://agones.dev/site/docs/overview/)：對照 GameServer、Fleet、allocation 與 SDK 的責任。
- [Agones Unity SDK](https://agones.dev/site/docs/guides/client-sdks/unity/)：使用 Unity 撰寫伺服器時的生命週期介面；本課只建置 macOS client，未驗證 dedicated server build。
- [Nakama 文件](https://heroiclabs.com/docs/nakama/)：查詢資料模型、配對與正式部署的要求。

## 下一步

Sprint 5 到這裡結束。回到[課程總覽](../index.md)，選擇下一個要深入驗證的主題。

---

!!! quote ""
    Agones 標誌為 CNCF（Linux Foundation）官方資產，此處作社群教學用途。
