import os
import signal
import subprocess
import sys
import time

STOP = False

SYMBOLS = (
    "BTCUSD,ETHUSD,SOLUSD,XRPUSD,BNBUSD,DOGEUSD,ADAUSD,AVAXUSD,"
    "LINKUSD,DOTUSD,TRXUSD,LTCUSD,BCHUSD,UNIUSD,NEARUSD,APTUSD,SUIUSD,"
    "FILUSD,ARBUSD,OPUSD,AAVEUSD,INJUSD,WIFUSD,TIAUSD,SEIUSD"
)

def stop_handler(signum, frame):
    global STOP
    STOP = True
    print("Stopping live stack...")

def main():
    data_dir = os.environ.get("TRADAI_DATA_DIR", "/data")

    collector_cmd = [
        sys.executable,
        "scripts/live_collector.py",
        "--symbols", SYMBOLS,
        "--interval", "10",
        "--data-dir", data_dir,
    ]

    worker_cmd = [
        sys.executable,
        "scripts/live_ingest_worker.py",
        "--data-dir", data_dir,
        "--db", f"{data_dir}/research/live_market.db",
        "--interval", "30",
    ]

    worker_env = os.environ.copy()
    worker_env["PYTHONPATH"] = os.getcwd() + os.pathsep + worker_env.get("PYTHONPATH", "")
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    print("Starting TradAI live stack...")
    print(f"data_dir={data_dir}")

    collector = subprocess.Popen(collector_cmd)
    worker = subprocess.Popen(worker_cmd, env=worker_env)

    try:
        while not STOP:
            if collector.poll() is not None:
                print(f"Collector stopped with code {collector.returncode}")
                break

            if worker.poll() is not None:
                print(f"Ingest worker stopped with code {worker.returncode}")
                break

            time.sleep(2)

    finally:
        for proc in (collector, worker):
            if proc.poll() is None:
                proc.terminate()

        for proc in (collector, worker):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("Live stack stopped.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
