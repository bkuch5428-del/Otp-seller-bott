# Running the bot

This project is a Telegram background bot. Replit runs it with:

```text
python james.py
```

Set the environment variables listed in `.env.example` in Replit Secrets or
environment variables before starting the workflow. The same command is used
as the Render background-worker start command.

The bot keeps its existing SQLite database file, session files, and screenshots
directories unchanged. MongoDB is required at startup and is used for users,
settings, admin permissions, custom countries, custom payment definitions,
inventory, auto prices, user balances, deposits, UPI orders, and runtime orders.
The SQLite file remains available for the staged migration and legacy data only.

## Required environment variable names

`BOT_TOKEN`, `API_ID`, `API_HASH`, `ADMIN_ID`, `MONGODB_URI`, `LOG_CHANNEL_ID`,
`CHECK_CHANNELS`, `JOIN_URLS`, `TERMS_URL`, `UPI_ID`, `UPI_QR`, `CWALLET_QR`,
`CWALLET_ID`, `SUPPORT_USERNAME_1`, `SUPPORT_USERNAME_2`, `OTP_REGEX`,
`AUTO_CANCEL_SECONDS`, `DEFAULT_USDT_RATE`, and `DEFAULT_SUPPORT_URL`.

When `USE_PREMIUM_EMOJIS` is enabled, also provide all
`PREMIUM_EMOJI_*` variables listed in `.env.example`.