import hashlib, json, os, sys, csv
M = sys.argv[1]; D = sys.argv[2]
m = json.load(open(M))
res = {"manifest_symbol_count": m["symbol_count"], "listed": len(m["files"]), "files": [], "extra_on_disk": []}
names = {f["filename"] for f in m["files"]}
for f in m["files"]:
    p = os.path.join(D, f["filename"]); r = {"symbol": f["symbol"], "filename": f["filename"]}
    if not os.path.isfile(p):
        r["status"] = "MISSING"; res["files"].append(r); continue
    h = hashlib.sha256(); rows = -1; first = last = None
    with open(p, "rb") as fh:
        hdr = fh.readline(); cols = hdr.decode().strip().split(",")
        ti = cols.index("timestamp"); rows = 0
        h.update(hdr)
        for line in fh:
            h.update(line); rows += 1
            if rows == 1: first = line.decode().split(",")[ti]
            last = line
    last = last.decode().split(",")[ti] if last else None
    sz = os.path.getsize(p)
    r.update(size=sz, size_ok=sz == f["size_bytes"], rows=rows, rows_ok=rows == f["row_count"],
             sha_ok=h.hexdigest() == f["sha256"], first_ok=first == f["first_timestamp"], last_ok=last == f["last_timestamp"],
             sha=h.hexdigest())
    r["status"] = "PASS" if all(r[k] for k in ("size_ok", "rows_ok", "sha_ok", "first_ok", "last_ok")) else "FAIL"
    res["files"].append(r)
for n in sorted(os.listdir(D)):
    if n not in names: res["extra_on_disk"].append(n)
# manifest_hash recompute (same payload as revision2/dataset_manifest.py)
recs = sorted(m["files"], key=lambda x: x["symbol"])
order = ["symbol","filename","sha256","size_bytes","row_count","first_timestamp","last_timestamp"]
payload = json.dumps([{k: x[k] for k in order} for x in recs], sort_keys=True, default=str)
res["manifest_hash_recomputed"] = hashlib.sha256(payload.encode()).hexdigest()
res["manifest_hash_ok"] = res["manifest_hash_recomputed"] == m["manifest_hash"]
res["summary"] = {s: sum(1 for r in res["files"] if r["status"] == s) for s in ("PASS", "FAIL", "MISSING")}
json.dump(res, open(sys.argv[3], "w"), indent=1)
print(res["summary"], "extra:", res["extra_on_disk"], "manifest_hash_ok:", res["manifest_hash_ok"], "listed", res["listed"])
for r in res["files"]:
    if r["status"] != "PASS": print(r)
