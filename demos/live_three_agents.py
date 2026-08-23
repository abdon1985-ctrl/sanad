# -*- coding: utf-8 -*-
"""عرض حي — ثلاثة وكلاء مربوطين على Stripe test mode.

كل ما سبق أُثبت بمزوّد وهمي داخل الاختبارات. هذا الملف يشغّل نفس
المسار على مزوّد حقيقي: كل EXECUTED هنا يقابله PaymentIntent فعلي
في حسابك التجريبي، وكل تسوية تسأل Stripe نفسه لا ذاكرةَ العملية.

قواعد السلامة المفروضة هنا:
- مفتاح `sk_live_` يُرفض رفضاً قاطعاً. العرض في وضع الاختبار أو لا يعمل.
- المبالغ صغيرة وثابتة، والحد اليومي مضبوط ليمنع أي انفلات.
- المشاهد التي تُرفض تُثبت أنها لم تلمس Stripe عبر عدّاد استدعاءات
  حقيقي، لا عبر ادعاء.

التشغيل:
    python demos/live_three_agents.py          # يسأل عن المفتاح
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sanad.binding import BoundGateway, PinnedPreAuthorization
from sanad.claims import ClaimStore
from sanad.identity import Signer
from sanad.ledger import Ledger
from sanad.providers import Provider
from sanad.providers.stripe import StripeProvider
from sanad.reconcile import recover_on_startup
from sanad.validity import TrustedKeys, timed_sign


class WatchedProvider(Provider):
    """يلفّ المزوّد الحقيقي: يعدّ الاستدعاءات، ويستطيع إسقاط الرد
    بعد نجاح الشحن فعلاً — فوضى حقيقية لا محاكاة، لأن المال يتحرك
    في الواقع ثم يُفقد الرد."""

    def __init__(self, inner):
        self.inner = inner
        self.charges = 0        # استدعاءات execute — تمسّ المال
        self.charged_minor = 0  # ما خرج فعلا، بغضّ النظر عن الرد
        self.lookups = 0        # استدعاءات القراءة — لا تمسّ المال
        self.drop_response = False

    def execute(self, amount_minor, currency, execution_id):
        self.charges += 1
        result = self.inner.execute(amount_minor, currency, execution_id)
        self.charged_minor += amount_minor
        if self.drop_response:
            raise TimeoutError("الشحن نجح في Stripe، والرد ضاع في الطريق")
        return result

    def find_by_execution_id(self, execution_id):
        self.lookups += 1
        return self.inner.find_by_execution_id(execution_id)

    def retrieve(self, receipt):
        self.lookups += 1
        return self.inner.retrieve(receipt)


def build(key, workdir):
    ledger = Ledger(os.path.join(workdir, "ledger.jsonl"))
    claims = ClaimStore(os.path.join(workdir, "claims.db"))
    doc = os.path.join(workdir, "pre_auth.json")
    with open(doc, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "auto_limit_minor": 5000,
            "daily_budget_minor": 8000,
            "currency": "USD",
            "blocked_categories": ["cash_advance"],
            "valid_days": 7,
        }))

    mohamed = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": mohamed.public_key_b64})
    boot = PinnedPreAuthorization(ledger, doc, keys, pre_auth_id=None)
    signed = timed_sign(boot, mohamed)
    pre = PinnedPreAuthorization(ledger, doc, keys,
                                 pre_auth_id=signed["pre_auth_id"])

    provider = WatchedProvider(StripeProvider(key))
    agents = {n: BoundGateway(ledger, claims, pre, provider, Signer(n))
              for n in ("buyer", "booker", "topup")}
    return ledger, claims, pre, provider, agents, mohamed, signed


def line(n, title, verdict, extra=""):
    print(f"{n}) {title:<34} -> {verdict}{extra}")


def main():
    key = os.environ.get("STRIPE_TEST_KEY", "").strip()
    if not key:
        from getpass import getpass
        key = getpass("مفتاح Stripe التجريبي (sk_test_...): ").strip()

    if key.startswith("sk_live_"):
        sys.exit("رُفض: هذا مفتاح حي. العرض لا يعمل إلا في وضع الاختبار.")
    if not key.startswith("sk_test_"):
        sys.exit("رُفض: المتوقع مفتاح يبدأ بـ sk_test_.")

    workdir = tempfile.mkdtemp(prefix="sanad_live_")
    ledger, claims, pre, provider, agents, mohamed, signed = build(key, workdir)
    print(f"\nالتفويض المثبَّت: {signed['pre_auth_id']}  |  السجل: {workdir}\n")
    print("--- ثلاثة وكلاء، تفويض واحد، Stripe حقيقي (وضع الاختبار) ---")

    # 1) تنفيذ مشروع
    ap1 = agents["buyer"].derive_approval("coffee", 3000, "USD")
    row1 = agents["buyer"].execute(ap1)
    line(1, "buyer ينفّذ موافقته", row1["state"],
         f" | إيصال: {row1.get('receipt')}")

    # 2) سرقة موافقة بين وكيلين — يجب ألا تلمس Stripe
    charges_before = provider.charges
    ap2 = agents["booker"].derive_approval("hotel", 4000, "USD")
    stolen = agents["topup"].execute(ap2)
    line(2, "topup يسرق موافقة booker", stolen["state"],
         f" | شحنات Stripe: {provider.charges - charges_before}")

    # 3) صاحبها ينفّذها — الرفض لم يحرقها
    row3 = agents["booker"].execute(ap2)
    line(3, "booker ينفّذ موافقته نفسها", row3["state"],
         f" | إيصال: {row3.get('receipt')}")

    # 4) توقيع مزوّر يُضاف للسجل — التفويض المثبَّت يتجاهله
    timed_sign(pre, Signer("rogue"))
    still = agents["buyer"].derive_approval("tea", 1000, "USD")
    line(4, "توقيع مزوّر يُضاف للسجل", "الوكلاء يعملون"
         if still is not None else "تجمّدوا")

    # 5) الفوضى الحقيقية: الشحن ينجح والرد يضيع، ثم Stripe يُسأل
    provider.drop_response = True
    row5 = agents["buyer"].execute(still)
    provider.drop_response = False
    line(5, "الرد ضاع بعد شحن حقيقي", row5["state"])

    charges_at_settle = provider.charges
    settled = recover_on_startup(ledger, claims, provider)
    verdict = settled[0][1] if settled else "لا شيء للتسوية"
    line(6, "Stripe يُسأل: ماذا حدث فعلا؟", verdict,
         f" | شحنات إضافية: {provider.charges - charges_at_settle}"
         f" | قراءات: {provider.lookups}")

    # 7) الحد اليومي — يُرفض قبل أي استدعاء
    charges_before = provider.charges
    over = agents["topup"].derive_approval("coffee", 3000, "USD")
    denied = [r for r in ledger.rows() if r["stage"] == "approval"][-1]
    line(7, "تجاوز الحد اليومي", denied["state"] if over is None else "مر!",
         f" | شحنات Stripe: {provider.charges - charges_before}")

    # ---- ملاحظة صادقة تخرج من الأرقام نفسها ----
    spent = ledger.spent_today_minor()
    print(f"\nالمصروف بحسب السجل: {spent} | ما خرج فعلا إلى Stripe: "
          f"{provider.charged_minor} (في {provider.charges} شحنات)")
    if provider.charged_minor != spent:
        print("لاحظ: عملية حُسمت بالتسوية (resolve) لا تدخل في حساب الحد "
              "اليومي، لأن spent_today_minor يقرأ سطور execute/EXECUTED "
              "فقط. المال خرج والميزانية لا تراه — ثغرة مسجَّلة، لا مخفية.")

    print(f"\nالسجل الكامل: {os.path.join(workdir, 'ledger.jsonl')}")


if __name__ == "__main__":
    main()
