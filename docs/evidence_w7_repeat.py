import sys, types, os, tempfile, pathlib, traceback
tree = sys.argv[1]; sys.path.insert(0, tree); os.chdir(tree)
pt = types.ModuleType("pytest"); pt.fixture = lambda f=None, **k: (f if callable(f) else (lambda g: g))
class R:
    def __init__(s,*a,**k): pass
    def __enter__(s): return s
    def __exit__(s,*a): return True
pt.raises = R
class M:
    def parametrize(s,*a,**k): return lambda f: f
    def __getattr__(s,n): return lambda *a,**k: (lambda f: f)
pt.mark = M(); sys.modules["pytest"] = pt
import importlib
m = importlib.import_module("tests.test_workflow")
res = []
for i in range(5):
    try:
        m.test_W7_split_transactions_still_hit_the_daily_budget(pathlib.Path(tempfile.mkdtemp()))
        res.append("pass")
    except Exception as e:
        tb = traceback.extract_tb(sys.exc_info()[2])[-1]
        res.append(f"FAIL {type(e).__name__} line {tb.lineno}: {tb.line}")
print(res)
