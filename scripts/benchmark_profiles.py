"""Isolated synthetic category-profiling benchmark; no real catalogue or API."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=400000)
    parser.add_argument("--categories", type=int, default=2000)
    args = parser.parse_args()
    if args.rows < 1 or args.categories < 1:
        parser.error("Filas y categorías deben ser positivas.")
    with tempfile.TemporaryDirectory(prefix="talla-profile-benchmark-") as directory:
        os.environ["TALLA_DATA_DIR"] = directory
        import app
        from category_profiler import generate
        app.init_db()
        started = time.perf_counter()
        with closing(app.connect()) as c:
            chunk = []
            for i in range(args.rows):
                variant = i // args.categories
                raw = {"seller": f"DEMO-{i % 7}", "sku": f"SYNTH-{i}", "name": "Producto ficticio de benchmark",
                       "brand": "Marca sintética", "model": f"M{i}", "variant": "Única", "presentation": "1 unidad",
                       "packaging": "Caja de envío", "category": f"Categoría sintética {i % args.categories}",
                       "subcategory": "Rama sintética", "weight_g": str(1000 + variant * 5),
                       "height_cm": "40", "length_cm": "30", "width_cm": str(10 + variant % 20)}
                calc = app.calculate_record(raw, "strict")
                chunk.append((raw["seller"], raw["sku"], raw["name"], app.identity_key(raw), json.dumps(raw, ensure_ascii=False),
                              app.canonical_hash(raw), app.metrics_key(raw), json.dumps(calc), calc["status"], app.now()))
                if len(chunk) >= 2000:
                    c.executemany("INSERT INTO skus(seller,sku,name,identity_key,raw_json,raw_hash,metrics_key,calculated,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", chunk)
                    c.commit()
                    chunk.clear()
            if chunk:
                c.executemany("INSERT INTO skus(seller,sku,name,identity_key,raw_json,raw_hash,metrics_key,calculated,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", chunk)
            c.commit()
        seed_seconds = time.perf_counter() - started
        started = time.perf_counter()
        run_id = uuid.uuid4().hex
        with closing(app.connect()) as c:
            with c:
                summary = generate(c, run_id, {"min_count": 30, "min_sellers": 3})
            assert c.execute("SELECT COUNT(*) FROM skus").fetchone()[0] == args.rows
            assert c.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0] == 0
        result = {"rows": args.rows, "categories": args.categories, "seed_seconds": round(seed_seconds, 2),
                  "profile_seconds": round(time.perf_counter() - started, 2),
                  "ready": summary["ready"], "total_scopes": summary["total_scopes"], "physical_data_changed": False,
                  "rules_applied": 0, "external_requests": 0}
        print(json.dumps(result, ensure_ascii=False))
        if summary["total_scopes"] != min(args.categories, args.rows):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
