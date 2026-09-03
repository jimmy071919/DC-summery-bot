# DC Summary Bot

使用者可設定一個來源頻道與一個輸出頻道。機器人每天讀取來源頻道的前一天訊息，交給 OpenAI 依話題摘要，再發到輸出頻道。

## 設定

1. 建立 Discord Bot，開啟 `Message Content Intent`，並授予來源頻道 `View Channel`、`Read Message History`，以及輸出頻道 `View Channel`、`Send Messages` 權限。
2. 複製 `.env.example` 為 `.env`，填入：

   - `DISCORD_TOKEN`：Discord Bot Token
   - `OPENAI_API_KEY`：OpenAI API key
   - `TIMEZONE`、`SUMMARY_TIME`：每日摘要時間，預設為台北時間 00:05

3. 執行：

```bash
uv run main.py
```

## Discord 設定

在伺服器內使用斜線指令：

```text
/summary_setup source:#聊天 output:#每日摘要
```

只有具備「管理伺服器」權限的使用者可以設定。設定會保存於 `summary.db`，重啟機器人後仍然有效。
