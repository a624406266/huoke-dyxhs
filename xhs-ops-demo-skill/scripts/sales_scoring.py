from __future__ import annotations

import re
from typing import Any

from product_catalog import has_purchase_signal, is_negative_text, match_products


NEED_TERMS = ["购买", "采购", "报价", "多少钱", "价格", "预算", "续费", "到期", "咨询", "怎么选", "选型", "部署", "私有化", "迁移", "上云", "搭建", "方案"]
URGENT_TERMS = ["急", "马上", "现在", "近期", "这个月", "本周", "预算", "老板要", "项目要", "上线", "到期", "救命"]
BUSINESS_TERMS = ["公司", "企业", "客户", "项目", "团队", "业务", "门店", "老板", "甲方", "电商", "教育", "医疗", "工厂"]
CONTACT_TERMS = ["私信", "联系", "怎么联系", "加", "电话", "微信", "求推荐", "求方案", "有推荐", "想问", "问下"]


def redact_text(value: str, keep: int = 2) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= keep:
        return value[0] + "*"
    return value[:keep] + "*" * min(4, max(1, len(value) - keep))


def classify_stage(text: str) -> str:
    if any(term in text for term in ["报价", "多少钱", "价格", "预算", "采购", "购买", "续费"]):
        return "采购/报价阶段"
    if any(term in text for term in ["部署", "私有化", "迁移", "上云", "搭建"]):
        return "方案/部署阶段"
    if any(term in text for term in ["怎么选", "选型", "推荐", "咨询", "方案"]):
        return "选型/咨询阶段"
    return "需求观察阶段"


def priority_from_score(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def score_signal(text: str, note: dict[str, Any] | None = None) -> dict[str, Any]:
    note = note or {}
    full_text = " ".join([text, note.get("title", ""), note.get("desc", ""), " ".join(note.get("tags") or [])])
    products = match_products(full_text)

    need_hits = [term for term in NEED_TERMS if term in full_text]
    urgent_hits = [term for term in URGENT_TERMS if term in full_text]
    business_hits = [term for term in BUSINESS_TERMS if term in full_text]
    contact_hits = [term for term in CONTACT_TERMS if term in full_text]

    need_score = min(30, len(need_hits) * 7 + (10 if has_purchase_signal(full_text) else 0))
    product_score = min(25, len(products) * 9)
    urgent_score = min(20, len(urgent_hits) * 7)
    business_score = min(15, len(business_hits) * 5)
    contact_score = min(10, len(contact_hits) * 5)
    score = need_score + product_score + urgent_score + business_score + contact_score

    if is_negative_text(full_text):
        score = min(score, 35)
    if not products:
        score = min(score, 55)
    if not need_hits and not contact_hits:
        score = min(score, 50)
    score = max(0, min(100, score))

    reasons = []
    if need_hits:
        reasons.append("需求词：" + "、".join(need_hits[:5]))
    if products:
        reasons.append("匹配产品：" + "、".join(p["label"] for p in products[:3]))
    if urgent_hits:
        reasons.append("紧急信号：" + "、".join(urgent_hits[:4]))
    if business_hits:
        reasons.append("企业场景：" + "、".join(business_hits[:4]))
    if not reasons:
        reasons.append("缺少明确采购信号，仅作为市场观察")

    return {
        "score": score,
        "priority": priority_from_score(score),
        "stage": classify_stage(full_text),
        "matched_products": products[:4],
        "reason": "；".join(reasons),
        "risk": risk_label(full_text, score),
    }


def risk_label(text: str, score: int) -> str:
    if is_negative_text(text):
        return "可能是教程/招聘/泛资讯等非目标线索"
    if score >= 80:
        return "高意向，建议当天人工核验后跟进"
    if score >= 60:
        return "中意向，建议进入线索池二次筛选"
    if score >= 40:
        return "弱意向，建议作为市场洞察观察"
    return "低意向，不建议触达"


def make_demand_summary(text: str, products: list[dict[str, Any]]) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    clean = clean[:90] + ("..." if len(clean) > 90 else "")
    product_labels = "、".join(p["label"] for p in products[:2]) if products else "相关产品/服务"
    return f"疑似对「{product_labels}」有需求：{clean}"


def make_outreach_copy(lead: dict[str, Any]) -> str:
    products = lead.get("matched_products") or []
    product_label = products[0]["label"] if products else "对应产品/服务"
    summary = lead.get("demand_summary") or "看到你提到相关需求"
    return (
        f"你好，我看到你提到「{summary[:38]}」。如果你方便，我可以基于你公开提到的场景，"
        f"先帮你初步梳理一下「{product_label}」是否适合，以及后续需要重点确认哪些条件。"
    )
