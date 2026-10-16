OfferBot — referral button update

Included files:
- bot.py: adds a "🎁 Реферальная программа" button to the main menu and opens the referral details screen.
- database.py: PostgreSQL database layer with referral tracking/reward logic.
- config.py: configuration for PostgreSQL and Railway environment variables.
- requirements.txt: Python dependencies, including psycopg2-binary.

How to deploy:
1. Replace these files in the same GitHub repository/branch deployed by Railway.
2. Keep your Railway environment variables (especially DATABASE_URL and BOT_TOKEN).
3. Commit and push the changes.
4. Trigger/review the Railway deployment and check build logs.
5. Open the bot in Telegram, send /start, and press "🎁 Реферальная программа".

Referral reward: 3 TON after the invited user’s first successful payment. The reward is credited to the internal referral balance; this code does not automatically transfer TON to a wallet.
