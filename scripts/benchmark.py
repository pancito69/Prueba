"""Isolated, synthetic ingestion benchmark. Never uses the operational database."""
import argparse
import csv
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=400000)
    args = parser.parse_args()
    if args.rows < 1:
        parser.error("--rows debe ser positivo")
    with tempfile.TemporaryDirectory(prefix="talla-benchmark-") as temp:
        os.environ["TALLA_DATA_DIR"] = temp
        import app
        app.init_db()
        path = Path(temp) / "benchmark.csv"
        columns = ["seller", "sku", "weight_g", "height_cm", "length_cm", "width_cm"]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for i in range(args.rows):
                writer.writerow([f"DEMO-{i % 100}", f"SYNTH-{i}", 1200, 40, 30, 20])
        with app.connect() as c:
            c.execute("INSERT INTO uploads VALUES ('bench','Sintético.csv',?,?)", (str(path), app.now()))
            c.execute("INSERT INTO jobs(id,upload_id,filename,status,mapping,created_at) VALUES ('bench','bench','Sintético.csv','queued',?,?)", (json.dumps(dict(zip(columns, columns))), app.now()))
        start = time.perf_counter()
        app.process_job("bench")
        elapsed = time.perf_counter() - start
        with app.connect() as c:
            result = dict(c.execute("SELECT status,processed,inserted,errors,error FROM jobs WHERE id='bench'").fetchone())
            result["persisted"] = c.execute("SELECT COUNT(*) FROM skus").fetchone()[0]
            result["classified_S"] = c.execute("SELECT COUNT(*) FROM skus WHERE json_extract(calculated,'$.size')='S'").fetchone()[0]
        result["seconds"] = round(elapsed, 2)
        start = time.perf_counter()
        summary = app.summary()
        result["summary_seconds"] = round(time.perf_counter() - start, 2)
        start = time.perf_counter()
        diagnostic = app.diagnostics()
        result["diagnostics_seconds"] = round(time.perf_counter() - start, 2)
        start = time.perf_counter()
        page = app.skus(page=1, page_size=25)
        result["page_seconds"] = round(time.perf_counter() - start, 2)
        assert summary["total"] == args.rows and diagnostic["total"] == args.rows and page["total"] == args.rows
        print(json.dumps(result, ensure_ascii=False))
        if result["status"] != "completed" or result["persisted"] != args.rows or result["classified_S"] != args.rows or result["errors"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
