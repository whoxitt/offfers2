OfferBot — referral update + PostgreSQL dependency fix

Files:
- bot.py
- database.py
- config.py
- requirements.txt

Fix for ModuleNotFoundError: No module named 'psycopg2':
requirements.txt now includes psycopg2-binary, which provides the psycopg2 import.

How to deploy:
1. Replace the listed files in the same GitHub repository/branch deployed by Railway.
2. Commit and push the changes.
3. In Railway, trigger a new deployment and check the build logs to confirm dependencies install.
4. Keep the existing Railway environment variables, especially DATABASE_URL, BOT_TOKEN and OWNER_ID.

The referral reward is credited to the referrer's internal TON balance after the invited user's first successful payment. It does not automatically transfer TON to a wallet.

Local Python syntax compilation was checked previously. A live Railway/PostgreSQL/TON payment test has not been performed.
