# Telegram Membership Bot

A clean starter implementation for the non-explicit digital membership/payment workflow discussed in chat.

## 1. Install
Python 3.10+ is recommended.

```bash
pip install -r requirements.txt
```

## 2. Configure
Copy `.env.example` to `.env` and set:
- `BOT_TOKEN`: the NEW token from BotFather
- `OWNER_ID`: your Telegram numeric user ID
- `QR_PATH`: path to your payment QR image

Do not commit `.env` or your bot token to GitHub.

## 3. Run
```bash
python bot.py
```

## 4. Admin
Send `/admin` from the owner account.

## Important
This starter uses manual payment proof + owner approval. It does not claim that a screenshot proves payment.
The plan data and admin sections can be expanded without changing the basic flow.

The timer shown in the UI is currently a display value; production use should enforce expiry server-side.
