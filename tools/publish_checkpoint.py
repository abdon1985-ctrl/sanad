# -*- coding: utf-8 -*-
"""نشر بصمة السجل — يحوّل السلسلة من إجراء إلى دليل."""
import json, os, sys
sys.path.insert(0, "/content/repo" if os.path.isdir("/content/repo") else ".")
from sanad.chain import ChainedLedger, seal, verify_chain, verify_against_published
from sanad.identity import Signer

LEDGER = "CHECKPOINT_LEDGER.jsonl"
lg = ChainedLedger(LEDGER)
lg.append("release", "TAGGED", "EXP-010 — first published root",
          experiments="EXP-000..EXP-010", tests=70)
m = Signer("sanad-checkpoint")
cp = seal(lg, m)

v = verify_chain(lg)
assert v["ok"], v
out = {"height": cp["height"], "root": cp["root"],
       "sealed_by": cp["sealed_by"], "public_key": m.public_key_b64,
       "ledger_file": LEDGER,
       "note": ("Verify with sanad.chain.verify_against_published(ledger, "
                "height, root). This root is evidence only because it is "
                "published here, outside the ledger holder's control. "
                "The sealing key is ephemeral: this proves the root, "
                "not a durable identity. Key management remains an open gap.")}
open("CHECKPOINT.json", "w", encoding="utf-8").write(
    json.dumps(out, indent=2, ensure_ascii=False) + "\n")

print(json.dumps({k: out[k] for k in ("height", "root", "sealed_by")},
                 indent=2))
print("\nتحقق فوري:", verify_against_published(lg, out["height"], out["root"]))
