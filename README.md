# 教師個人資料與數位課程展示網站系統

給約 25 位老師使用的教師個人資料與數位課程展示平台。每位老師有獨立帳號，只能管理自己的資料；填寫完成、預覽、發布後，系統自動產生一頁式公開網站（`/teacher/001`）。管理員可管理所有帳號、查看完成度與發布狀態，並在必要時協助修改。

**登入、Session、資料庫、權限、上傳驗證全部由後端實作**，並附 65 項自動化測試驗證越權存取、CSRF、XSS、SQL Injection、上傳攻擊、草稿外洩、批次匯入、備份還原等情境。

## 第二版新增功能（v2）

| 類別 | 功能 |
|---|---|
| 填寫體驗 | 文字**自動儲存**（停止輸入 2 秒後存草稿，顯示「已自動儲存 ✓ 21:03」）、離開頁面前**未儲存提醒**、長文字**字數計數** |
| | **完成清單 Checklist**：分「必填／選填」兩組，✓ 已完成／! 必填未完成／○ 選填未填，每項可直接「前往填寫」；必填全部完成即可發布，選填不影響發布 |
| | **拖曳排序**（☰）：學歷、經歷、專長、獲獎、認證、課堂照片、課程；支援手機觸控與鍵盤 ↑↓ |
| | 個人照片**瀏覽器內裁切**（1:1／4:5）；課堂照片上傳前**預覽與自動壓縮** |
| 公開頁 | 全新 **Hero 區**（照片、姓名、學校、Slogan、專長標籤、成果數字）與成果展示版面 |
| | 照片 **Lightbox**（上一張／下一張、方向鍵、Esc、手機滑動、1 / 8 張數） |
| | **分享**（手機原生分享、複製連結、LINE、Facebook、QR Code）、**列印／存 PDF** |
| | **SEO／社群預覽**：每頁獨立 title、description、Open Graph（含照片），另有 sitemap.xml、robots.txt |
| | **WebP 縮圖**＋延遲載入，手機瀏覽更快 |
| QR Code | 老師後台顯示公開網址、QR Code、複製網址、下載 PNG／SVG（SVG 可放大印刷） |
| 管理員 | **批次匯入**教師帳號（Excel／CSV，含預覽、重複檢查、錯誤原因、臨時密碼 CSV、列印**帳號通知單**） |
| | **進度 Dashboard**：8 項統計、快速篩選、每位老師尚缺項目、最後登入、瀏覽數 |
| | **提醒名單**：匯出 CSV；設定 SMTP 後可一鍵寄送提醒信 |
| | **公告**（可設定起訖日、置頂）與**填寫截止日**（老師端顯示倒數） |
| | **完整 Excel 匯出**（外牆與一頁網、會場手冊、課程內容、學經歷與獲獎、聯繫與進度、瀏覽統計 6 個工作表） |
| | **帳號有效期限**（起訖日，到期後公開頁可選擇保留或隱藏） |
| | **備份**：後台一鍵備份與下載、顯示最近備份時間；指令列 `backup`／`restore-backup` |
| 其他 | **瀏覽統計**（7 天／30 天／總計，只存每日次數，不記錄 IP；老師只看得到自己的） |
| | **發布紀錄**：保留最近 10 次發布，可一鍵還原為先前的公開版本（不影響草稿） |
| | 老師可看到自己的上次登入時間；無障礙改善（鍵盤操作、焦點管理、對比度 AA） |

### 資料庫：PostgreSQL
- 資料存放在 PostgreSQL（連線字串以 `DATABASE_URL` 設定），網站與資料庫可以分開部署；上傳檔與備份仍放在 `INSTANCE_DIR`。
- 第一次啟動會自動建立所有資料表，不需要另外執行建表指令。
- 舊版使用 SQLite（`instance/app.db`）。本版**不會**自動匯入舊的 SQLite 資料，舊版的備份 zip 也無法還原到 PostgreSQL。

- 設計文件（功能架構、頁面架構、流程、ERD、欄位、API、前端設計、權限設計、資料夾結構）：[`docs/DESIGN.md`](docs/DESIGN.md)
- 技術：Python 3.10+、Flask 3、PostgreSQL（psycopg 3）、Jinja2、Pillow；前端為原生 CSS/JS，無需 Node.js 或建置步驟

---

## 11. 安裝與啟動（開發 / 本機試用）

### macOS / Linux
```bash
cd teacher-showcase
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

createdb teacher_showcase       # 需要一個 PostgreSQL 資料庫（本機或遠端皆可）
cp env.example.json env.json    # 修改 SECRET_KEY、DATABASE_URL（本機例：postgresql://localhost/teacher_showcase）
flask --app wsgi seed-demo         # 建立示範帳號與資料（只能在空資料庫執行）
python run.py                      # 開啟 http://127.0.0.1:5000
```

### Windows（PowerShell）
```powershell
cd teacher-showcase
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy env.example.json env.json
flask --app wsgi seed-demo
python run.py
```

### 正式使用（不要示範資料）
```bash
flask --app wsgi create-admin --username admin --name "系統管理員"
```
接著登入 `/admin/` 逐一新增 25 位老師帳號，系統會產生臨時密碼（只顯示一次，可列印交給老師）。

### 執行測試
測試需要一個 PostgreSQL 測試資料庫；每個測試會在裡面建立專屬的 schema，結束後自動刪除。
```bash
createdb teacher_showcase_test
# 預設連 postgresql://localhost/teacher_showcase_test，可用 TEST_DATABASE_URL 指定其他資料庫
python -m unittest discover -s tests -t . -v
```

### 其他指令（Windows 請在前面加 `python -m`，例如 `python -m flask --app wsgi backup`）
| 指令 | 說明 |
|---|---|
| `flask --app wsgi create-admin` | 新增管理員 |
| `flask --app wsgi seed-demo` | 建立示範資料 |
| `flask --app wsgi purge-sessions` | 清除過期 Session（可放 cron） |
| `flask --app wsgi backup` | 建立備份（存在 `instance/backups/`，保留最近 14 份） |
| `flask --app wsgi restore-backup <zip>` | 從備份還原（請先停止網站；目前資料會先自動另存） |
| `flask --app wsgi fill-account-emails` | 帳號 Email 空白的老師，以其自填的聯繫 Email 補上（可重複執行） |

忘記密碼：未設定 SMTP 時，重設連結會印在伺服器終端機的 log 中，方便測試。

---

## 13. 部署方式

### 方案 A：Linux 主機 + Nginx + systemd（建議）
```bash
# 1. 建立系統帳號與程式目錄
sudo useradd --system --create-home teacher
sudo mkdir -p /opt/teacher-showcase && sudo chown teacher: /opt/teacher-showcase
# 上傳專案到 /opt/teacher-showcase 後：
sudo -u teacher bash -c 'cd /opt/teacher-showcase && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'

# 2. 設定 env.json（重要）
#    格式：[{"name": "SECRET_KEY", "value": "...", "slotSetting": false}, ...]（同 Azure App Service 進階編輯）
#    SECRET_KEY 以 python -c "import secrets;print(secrets.token_hex(32))" 產生
#    COOKIE_SECURE=1、TRUST_PROXY=1、BASE_URL=https://你的網域、DATABASE_URL、SMTP_*
sudo -u teacher cp env.example.json env.json && sudo -u teacher nano env.json
sudo chmod 600 /opt/teacher-showcase/env.json

# 3. 建立管理員
sudo -u teacher bash -c 'cd /opt/teacher-showcase && .venv/bin/flask --app wsgi create-admin --username admin'

# 4. 服務
sudo cp deploy/teacher-showcase.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now teacher-showcase

# 5. Nginx + HTTPS
sudo cp deploy/nginx.conf /etc/nginx/sites-available/teacher-showcase
sudo ln -s /etc/nginx/sites-available/teacher-showcase /etc/nginx/sites-enabled/
sudo certbot --nginx -d teacher.example.edu.tw
sudo nginx -t && sudo systemctl reload nginx

# 6. 每日備份（存到 instance/backups/，建議再同步到其他主機或雲端）
sudo -u teacher crontab -e    # 15 2 * * * /opt/teacher-showcase/deploy/backup.sh
```

### 方案 B：Docker
```bash
cp env.example.json env.json     # 設定 SECRET_KEY、COOKIE_SECURE、BASE_URL；容器以 uid 10001 讀取，需 chmod 644 或 chown 10001
docker compose up -d --build
docker compose exec web flask --app wsgi create-admin --username admin
```
上傳檔與備份存在 `./data/`；資料庫是 `env.json` 的 `DATABASE_URL` 所指的 PostgreSQL（容器必須連得到）。前面同樣接 Nginx（`deploy/nginx.conf`）提供 HTTPS。

### 方案 C：Windows Server（學校機房常見）
```powershell
pip install -r requirements.txt      # Windows 會自動安裝 waitress
waitress-serve --host 127.0.0.1 --port 8000 wsgi:app
```
前面以 IIS（ARR 反向代理）或 Nginx for Windows 提供 HTTPS，`env.json` 設 `TRUST_PROXY=1`、`COOKIE_SECURE=1`，並以「工作排程器」設成開機自動啟動。

### 方案 D：Azure App Service + Azure Database for PostgreSQL
網站與資料庫分開：App Service 跑網站，資料放在 Azure Database for PostgreSQL（Flexible Server）。

1. **資料庫**：建立 Flexible Server（Burstable B1ms 即可）與資料庫 `teacher_showcase`；「網路」允許 App Service 連入（勾選允許 Azure 服務存取，或使用 VNet 整合）。
2. **App Service**：建立 Linux、Python 3.12 的 Web App 並部署程式碼（`az webapp up`、GitHub Actions 或 zip 部署皆可）。
3. **環境變數**（設定 → 環境變數；不需要 `env.json`，環境變數優先）：

   | 名稱 | 值 |
   |---|---|
   | `DATABASE_URL` | `postgresql://帳號:密碼@伺服器名稱.postgres.database.azure.com:5432/teacher_showcase?sslmode=require`（密碼含 `@ : / #` 等字元需做 URL 編碼） |
   | `SECRET_KEY` | 長隨機字串 |
   | `INSTANCE_DIR` | `/home/data`（`/home` 是 App Service 的持久儲存；放在別處的上傳檔會在重啟或重新部署後消失） |
   | `AZURE_STORAGE_CONNECTION_STRING` | 儲存體帳戶 →「安全性 + 網路」→「存取金鑰」的**連線字串**。設定後老師上傳的圖片與 PDF 都存到 Blob Storage |
   | `AZURE_STORAGE_CONTAINER` | 容器名稱，預設 `uploads`（不存在時自動建立為私人容器，不需開放匿名存取；檔案仍由網站檢查權限後提供） |
   | `COOKIE_SECURE`、`TRUST_PROXY` | `1` |
   | `BASE_URL` | 正式網址 |
   | `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true`（部署時自動 `pip install -r requirements.txt`） |

4. **啟動命令**（設定 → 組態 → 啟動命令）：
   ```bash
   gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8000 --access-logfile - wsgi:app
   ```
5. **建立管理員**：在自己的電腦執行，直接連到 Azure 的資料庫（資料庫防火牆需暫時加入你的 IP）：
   ```bash
   DATABASE_URL="postgresql://…?sslmode=require" flask --app wsgi create-admin --username admin
   ```

- 改用 `Dockerfile` 以容器部署時，另外設定 `WEBSITES_PORT=8000` 與 `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`（後者讓 `/home` 持久化）。
- 後台的「備份」會存到 `/home/data/backups/`，內容包含資料庫與上傳檔；建議定期下載一份到其他地方。
- 原本已存在 `/home/data/uploads` 的檔案，設定好儲存體連線字串後在 App Service 的 SSH 執行一次 `flask --app wsgi upload-to-azure` 即可搬到 Blob Storage（已存在的會略過，可重複執行）。
- 老師帳號 Email 空白時，老師在基本資料按「儲存草稿」或發布後會自動以自填的 Email 補上（忘記密碼用）。此功能上線前已填好 Email 的老師，在 SSH 執行一次 `flask --app wsgi fill-account-emails` 即可補齊。
- 未設定 `AZURE_STORAGE_CONNECTION_STRING` 時（本機開發、測試），上傳檔照舊存在 `UPLOAD_FOLDER`。

### 上線檢查清單
- [ ] `SECRET_KEY` 為長隨機字串（未設定時每次重啟所有人都會被登出）
- [ ] 全站 HTTPS，`COOKIE_SECURE=1`、`TRUST_PROXY=1`
- [ ] 未執行 `seed-demo`，或已更換所有示範密碼
- [ ] SMTP 已設定（否則老師忘記密碼需由管理員重設）
- [ ] Nginx `client_max_body_size` ≥ PDF 上限 + 2 MB
- [ ] 已設定每日備份（`deploy/backup.sh` 或 Windows 工作排程器執行 `python -m flask --app wsgi backup`），並定期把備份檔複製到其他地方
- [ ] `env.json` 的 `BASE_URL` 已設為正式網址（QR Code、分享連結、社群預覽圖都會用到）
- [ ] gunicorn 維持單一 worker（登入限流的計數存在程序記憶體內），25 位老師的流量綽綽有餘
- [ ] `DATABASE_URL` 指向正式的 PostgreSQL，且上傳檔位置（`INSTANCE_DIR`）在重啟後不會遺失

### 規模與擴充說明
- 目前每個請求各開一條資料庫連線，對 25 位老師、數百位訪客完全足夠；若未來擴大到數百位老師，可改用連線池（`psycopg_pool`），登入限流改用 Redis 後即可增加 worker 數。
- 調查表中的「3. 數位教學平臺與工具」「4. 人數與餐食」目前未納入，可比照 `courses` 新增資料表與表單；`/admin/export.xlsx` 已依運用處分工作表匯出（外牆、一頁網、會場手冊等）。
- 尚未實作：需求清單第 19 項「多年度／多活動分類」。這會改變資料結構（每位老師每年度一份成果），建議確認活動規劃後再做。
