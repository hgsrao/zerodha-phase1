import yfinance as yf
import pandas as pd

# Yahoo Finance ticker for Nifty 50 Index
ticker = "^NSEI"

print("Downloading 3 years of Nifty 50 data from public feeds...")

# Fetch the last 3 years of daily historical data
df = yf.download(ticker, period="3y", interval="1d")

# Save the dataset to a CSV file
df.to_csv("nifty50_3years_public.csv")

print("Successfully downloaded and saved to 'nifty50_3years_public.csv'.")
print(df.head())
