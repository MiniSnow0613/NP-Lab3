# Network Programming Lab3 – Game Lobby System

## Environment
- Python 3.9+
- OS: Windows (Client), Linux (Server)

## Folder Structure
- `server/` : Lobby Server
- `Player/` : Player Client
- `Developer/` : Developer Client
- `common/` : Shared protocol utilities

## How to Run
- Developer :
    - python .\developer\developer_client.py
- Player :
    - python .\Player\lobby_client.py

### Server (Linux)
```bash
cd server
python server_main.py
```

## 一、角色說明與職責分工

###  Developer（開發者）

**功能**
- 註冊 / 登入開發者帳號
- 上架新遊戲
- 更新既有遊戲（新增版本）
- 軟下架 / 重新上架 / 永久刪除遊戲

**設計重點**
- 每款遊戲具有唯一的遊戲名稱（Game ID）
- 遊戲名稱一旦被某位開發者使用，其他開發者不可再使用
- 一款遊戲可包含多個版本（如 1.0、1.1）

### Player（一般玩家）

**功能**
- 註冊 / 登入玩家帳號
- 瀏覽遊戲商城
- 查看遊戲詳細資訊
- 下載指定版本遊戲
- 安裝（解壓）並啟動遊戲

**設計重點**
- Player 與 Developer 使用不同帳號資料庫
- 同名帳號可同時存在於 Developer 與 Player（如 emma）
- 玩家下載的遊戲內容為唯讀，不可任意修改

### Server（商城 / 大廳後端）

Server 邏輯上分為三個模組：

#### (1) Data / DB Server
- 管理帳號與遊戲資料
- 使用 JSON 檔案進行資料持久化

實際使用三個資料庫檔案：
### 3️⃣ Server（商城 / 大廳後端）

Server 邏輯上分為三個模組：

#### (1) Data / DB Server
- 管理帳號與遊戲資料
- 使用 JSON 檔案進行資料持久化

實際使用三個資料庫檔案：
- developers.json → 開發者帳號
- players.json → 玩家帳號
- games.json → 上架遊戲與版本資訊


#### (2) Developer Server
- 處理遊戲上架、更新、下架、刪除
- 接收並存放遊戲 zip 至 storage/
- 檢查遊戲名稱唯一性與權限

#### (3) Lobby / Store Server
- 提供玩家瀏覽商城
- 提供遊戲詳細資訊
- 提供下載指定版本遊戲

---

## 二、遊戲版本與檔案管理設計

同一款遊戲可能同時存在於三個位置：

| 位置 | 說明 |
|----|----|
| Developer 端 | 開發中版本，可修改 |
| Server storage | 上架版本，唯一可信來源 |
| Player downloads | 玩家下載版本，唯讀 |

由於上傳、更新與下載時間不同，三者之間不保證版本一致，此設計用於模擬真實世界中的版本差異情境。

---

## 三、為何每位玩家需要獨立下載資料夾？

### 實際結構
client/downloads/
├── PlayerA/
│ └── net_quiz/1.0/
├── PlayerB/
│ └── net_quiz/1.1/

### 設計原因
- 在同一台 Demo 機器上模擬多位玩家
- 不同玩家可能下載不同版本
- 避免版本互相覆蓋

此設計可自然產生「有人已更新、有人尚未更新」的測試情境。

---

## 四、已實作 Use Cases

### D1：開發者上架 / 更新遊戲
- 驗證 `game_config.json`
- 遊戲名稱唯一
- 同一遊戲可新增多版本

---

### D3：開發者下架 / 重新上架 / 永久刪除
- 軟下架：玩家端不可見、不可下載
- 重新上架：恢復可見
- 永久刪除：
  - DB 中刪除整款遊戲
  - storage 中所有版本 zip 一併刪除
  - 需 DELETE 二次確認

永久刪除以「整款遊戲」為單位，避免語意模糊。

---

### P1：玩家瀏覽遊戲商城與詳細資訊
- 僅顯示已上架（published = true）的遊戲
- 顯示最新版本
- 可查看所有版本與簡介
- 無遊戲時顯示明確提示

---

### 遊戲實作（關卡 C）
- 多人quiz遊戲

---

## 五、系統正確性與穩定性

- Server 使用 lock 保護 JSON 寫入
- 所有檔案操作限制於指定資料夾
- 防止路徑跳脫（path traversal）
- 所有錯誤皆回傳可理解的文字訊息
