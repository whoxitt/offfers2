OfferBot: referral system patch

Files to replace in your repository:
- bot.py
- database.py
- config.py

Referral behavior:
- Each user can get a personal link using /ref.
- New users open the bot with ?start=ref_<telegram_user_id>.
- The inviter gets 3 TON credited to their referral balance after the invited user makes their first successfully confirmed payment.
- Rewarding and payment confirmation/access grant happen in one PostgreSQL transaction.
- A unique constraint on referral_rewards.referred_id prevents duplicate rewards.
- The existing admin UI and reply_message(..., reply_markup=...) support are preserved.

Railway must have DATABASE_URL set to a PostgreSQL reference. Keep your existing secrets in Railway; do not add tokens to source files.

Validation performed: Python syntax compilation. No live Railway database or real TON payment was tested.
