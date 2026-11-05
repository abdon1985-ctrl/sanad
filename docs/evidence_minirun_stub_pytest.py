import sys, types, os, importlib, inspect, tempfile, pathlib, traceback, re
tree = sys.argv[1]
sys.path.insert(0, tree); os.chdir(tree)

pt = types.ModuleType("pytest")
def fixture(fn=None, **kw):
    def deco(f): f._is_fixture = True; return f
    return deco(fn) if callable(fn) else deco
class _Raises:
    def __init__(s, exc, match=None): s.exc, s.match = exc, match
    def __enter__(s): return s
    def __exit__(s, t, v, tb):
        if t is None: raise AssertionError("DID NOT RAISE")
        if not issubclass(t, s.exc): return False
        if s.match and not re.search(s.match, str(v)): raise AssertionError("match failed: %s" % v)
        return True
class _Mark:
    def parametrize(s, names, values):
        names = [n.strip() for n in names.split(",")] if isinstance(names, str) else list(names)
        def deco(f): f._params = (names, list(values)); return f
        return deco
    def __getattr__(s, n): return lambda *a, **k: (lambda f: f)
pt.fixture, pt.raises, pt.mark = fixture, _Raises, _Mark()
sys.modules["pytest"] = pt

results = {"pass": [], "fail": [], "error": []}
tests_dir = pathlib.Path(tree) / "tests"
for f in sorted(tests_dir.glob("test_*.py")):
    mod = f"tests.{f.stem}"
    try:
        m = importlib.import_module(mod)
    except Exception as e:
        results["error"].append(f"{f.stem}: IMPORT {type(e).__name__}: {str(e)[:60]}"); continue
    fixtures = {n: o for n, o in vars(m).items() if callable(o) and getattr(o, "_is_fixture", False)}
    def resolve(name, extra):
        if name in extra: return extra[name]
        if name == "tmp_path": return pathlib.Path(tempfile.mkdtemp())
        if name in fixtures:
            fn = fixtures[name]
            return fn(*[resolve(p, extra) for p in inspect.signature(fn).parameters])
        raise LookupError(f"unsupported fixture {name}")
    for n, fn in list(vars(m).items()):
        if not (n.startswith("test_") and callable(fn)): continue
        cases = [({},)]
        if hasattr(fn, "_params"):
            names, vals = fn._params
            cases = [(dict(zip(names, v if len(names) > 1 else (v,))),) for v in vals]
        for (extra,) in cases:
            tid = f"{f.stem}::{n}" + (f"[{','.join(map(str, extra.values()))}]" if extra else "")
            try:
                fn(*[resolve(p, extra) for p in inspect.signature(fn).parameters])
                results["pass"].append(tid)
            except AssertionError as e:
                results["fail"].append(f"{tid}: {str(e)[:70]}")
            except LookupError as e:
                results["error"].append(f"{tid}: {e}")
            except Exception as e:
                results["fail"].append(f"{tid}: {type(e).__name__}: {str(e)[:70]}")
print(f"PASS={len(results['pass'])} FAIL={len(results['fail'])} ERROR={len(results['error'])}")
for k in ("fail", "error"):
    for x in results[k]: print(k.upper(), x)
