import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
from datetime import datetime, date

# =========================
# CONFIG - ISI BAGIAN INI
# =========================
LOGIN = 10009965823
PASSWORD = "123456789"
SERVER = "MetaQuotes-Demo"
MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

SYMBOL = "XAUUSD"      # contoh: XAUUSD, XAUUSDm, GOLD
LOT = 0.01

TIMEFRAME = mt5.TIMEFRAME_M1
BARS = 300

MAGIC = 20260424
DEVIATION = 30

MAX_SPREAD_POINTS = 80
MAX_POSITIONS = 1
COOLDOWN_SECONDS = 180
MAX_DAILY_LOSS = 30.0

ATR_SL_MULT = 1.5
ATR_TP_MULT = 2.2

LOOP_SECONDS = 10

# =========================
# INDIKATOR
# =========================
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def macd(series):
    fast = ema(series, 12)
    slow = ema(series, 26)
    macd_line = fast - slow
    signal = ema(macd_line, 9)
    hist = macd_line - signal
    return macd_line, signal, hist

def atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# =========================
# MT5
# =========================
def connect_mt5():
    if not mt5.initialize(path=MT5_PATH, login=LOGIN, password=PASSWORD, server=SERVER):
        raise RuntimeError(f"MT5 initialize gagal: {mt5.last_error()}")

    account = mt5.account_info()
    if account is None:
        raise RuntimeError(f"Account info gagal: {mt5.last_error()}")

    if not mt5.symbol_select(SYMBOL, True):
        raise RuntimeError(f"Symbol {SYMBOL} tidak bisa dipilih. Cek nama symbol broker.")

    print(f"Connected: {account.login} | Balance: {account.balance} | Equity: {account.equity}")

def get_data():
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, BARS)
    if rates is None or len(rates) < 100:
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")

    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["rsi"] = rsi(df["close"], 14)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(df["close"])
    df["atr"] = atr(df, 14)

    return df.dropna()

def get_spread_points():
    tick = mt5.symbol_info_tick(SYMBOL)
    info = mt5.symbol_info(SYMBOL)

    if tick is None or info is None:
        return None

    spread = (tick.ask - tick.bid) / info.point
    return spread

def current_positions():
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return []
    return [p for p in positions if p.magic == MAGIC]

def daily_profit():
    today = datetime.combine(date.today(), datetime.min.time())
    deals = mt5.history_deals_get(today, datetime.now())

    if deals is None:
        return 0.0

    total = 0.0
    for d in deals:
        if d.symbol == SYMBOL and d.magic == MAGIC:
            total += d.profit + d.swap + d.commission
    return total

# =========================
# SIGNAL
# =========================
def generate_signal(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    bullish_trend = last["close"] > last["ema50"] > last["ema200"]
    bearish_trend = last["close"] < last["ema50"] < last["ema200"]

    macd_buy = prev["macd_hist"] < last["macd_hist"] and last["macd_hist"] > 0
    macd_sell = prev["macd_hist"] > last["macd_hist"] and last["macd_hist"] < 0

    rsi_buy = 35 < last["rsi"] < 65 and last["rsi"] > prev["rsi"]
    rsi_sell = 35 < last["rsi"] < 65 and last["rsi"] < prev["rsi"]

    candle_buy = last["close"] > last["open"]
    candle_sell = last["close"] < last["open"]

    if bullish_trend and macd_buy and rsi_buy and candle_buy:
        return "BUY"

    if bearish_trend and macd_sell and rsi_sell and candle_sell:
        return "SELL"

    return "WAIT"

# =========================
# ORDER
# =========================
def send_order(signal, df):
    tick = mt5.symbol_info_tick(SYMBOL)
    info = mt5.symbol_info(SYMBOL)

    if tick is None or info is None:
        print("Tick/info tidak tersedia.")
        return

    last = df.iloc[-1]
    atr_value = float(last["atr"])

    if signal == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        sl = price - ATR_SL_MULT * atr_value
        tp = price + ATR_TP_MULT * atr_value

    elif signal == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        sl = price + ATR_SL_MULT * atr_value
        tp = price - ATR_TP_MULT * atr_value

    else:
        return

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": LOT,
        "type": order_type,
        "price": price,
        "sl": round(sl, info.digits),
        "tp": round(tp, info.digits),
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": f"XAU AUTO {signal}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result is None:
        print("Order gagal: result None")
        return

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Order gagal | retcode={result.retcode} | comment={result.comment}")
    else:
        print(f"ORDER {signal} BERHASIL | price={price} | SL={sl} | TP={tp}")

# =========================
# MAIN BOT
# =========================
def run_bot():
    connect_mt5()

    last_trade_time = 0

    print("XAU Auto Trading Bot aktif...")
    print("Gunakan akun DEMO dulu. Tekan CTRL+C untuk berhenti.")

    while True:
        try:
            df = get_data()

            if df is None or df.empty:
                print("Data belum cukup.")
                time.sleep(LOOP_SECONDS)
                continue

            spread = get_spread_points()
            if spread is None:
                print("Spread tidak tersedia.")
                time.sleep(LOOP_SECONDS)
                continue

            pnl_today = daily_profit()

            if pnl_today <= -abs(MAX_DAILY_LOSS):
                print(f"STOP: Daily loss limit tercapai: {pnl_today:.2f}")
                time.sleep(60)
                continue

            positions = current_positions()

            signal = generate_signal(df)
            last = df.iloc[-1]

            print(
                f"{datetime.now()} | {SYMBOL} | Signal={signal} | "
                f"Close={last['close']:.2f} | RSI={last['rsi']:.2f} | "
                f"MACD Hist={last['macd_hist']:.5f} | Spread={spread:.1f} | "
                f"Positions={len(positions)} | PnL Today={pnl_today:.2f}"
            )

            if spread > MAX_SPREAD_POINTS:
                print("Skip: spread terlalu besar.")
                time.sleep(LOOP_SECONDS)
                continue

            if len(positions) >= MAX_POSITIONS:
                print("Skip: posisi aktif sudah ada.")
                time.sleep(LOOP_SECONDS)
                continue

            if time.time() - last_trade_time < COOLDOWN_SECONDS:
                print("Skip: cooldown aktif.")
                time.sleep(LOOP_SECONDS)
                continue

            if signal in ["BUY", "SELL"]:
                send_order(signal, df)
                last_trade_time = time.time()

            time.sleep(LOOP_SECONDS)

        except KeyboardInterrupt:
            print("Bot dihentikan manual.")
            break

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(LOOP_SECONDS)

    mt5.shutdown()

if __name__ == "__main__":
    run_bot()
