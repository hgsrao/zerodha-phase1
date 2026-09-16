from datetime import datetime, timedelta
from kiteconnect import KiteConnect
import pandas as pd

API_KEY = "f5qmn3ug0i6brql3"
API_SECRET = "jpbtt7omw83f3hhazprrzkaephz9pg5"

kite = KiteConnect(api_key=API_KEY)

print("1. Open this URL in your browser (preferably in an Incognito window):")
print(kite.login_url())
print("-" * 60)

request_token = input("2. Enter the fresh 'request_token' from the redirect URL: ").strip()

data = kite.generate_session(request_token, api_secret=API_SECRET)
access_token = data["access_token"]
kite.set_access_token(access_token)
print("Session generated successfully!")

instruments = kite.instruments("NSE")
nifty_token = None
for inst in instruments:
    if inst["tradingsymbol"] == "NIFTY 50" and inst["exchange"] == "NSE":
        nifty_token = inst["instrument_token"]
        break

if not nifty_token:
    raise ValueError("Nifty 50 instrument token not found!")

print(f"Nifty 50 Instrument Token: {nifty_token}")

to_date = datetime.now()
from_date = to_date - timedelta(days=365 * 3)

historical_data = kite.historical_data(
    instrument_token=nifty_token,
    from_date=from_date,
    to_date=to_date,
    interval="day"
)

df = pd.DataFrame(historical_data)
df.to_csv("nifty50_3years_kite.csv", index=False)
print("Successfully downloaded 3 years of Nifty 50 historical data and saved to 'nifty50_3years_kite.csv'.")
print(df.head())
