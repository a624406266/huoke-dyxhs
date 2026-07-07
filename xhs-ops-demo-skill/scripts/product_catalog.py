from __future__ import annotations

from collections import Counter
from typing import Any


BUSINESS_DIRECTIONS: dict[str, dict[str, Any]] = {
    "cloud_ai": {
        "label": "开放需求获客（默认）",
        "keywords": ["求推荐方案", "预算报价", "成本太高", "怎么选服务商", "有没有靠谱", "想找供应商"],
    },
    "cloud_agent": {
        "label": "云服务成本优化",
        "keywords": ["云服务优惠", "云服务代理", "云服务器续费贵", "服务器到期", "企业上云优惠"],
    },
    "cloud_infra": {
        "label": "云服务器 / 数据库 / 存储",
        "keywords": ["云服务器配置", "云数据库迁移", "对象存储", "服务器带宽", "数据库上云"],
    },
    "migration": {
        "label": "迁移上云",
        "keywords": ["迁移上云", "本地服务器迁云", "数据库迁移", "企业上云方案", "业务上云"],
    },
    "meeting": {
        "label": "会议 / 企业协同",
        "keywords": ["企业会议系统", "远程会议系统", "视频会议采购", "会议安全", "在线协作方案"],
    },
    "security": {
        "label": "网络安全",
        "keywords": ["网站被攻击", "云防火墙", "等保测评", "DDoS防护", "Web安全防护"],
    },
    "ai_private_deploy": {
        "label": "大模型私有化部署",
        "keywords": ["DeepSeek私有化部署", "企业知识库AI", "本地部署大模型", "内网AI助手", "大模型私有化"],
    },
    "ai_app": {
        "label": "AI 应用开发",
        "keywords": ["AI客服", "智能体开发", "企业AI助手", "营销AI助手", "内容自动化"],
    },
    "gpu_compute": {
        "label": "GPU 算力",
        "keywords": ["GPU算力租用", "模型训练算力", "大模型训练平台", "AIGC算力", "GPU服务器"],
    },
}


PRODUCTS: list[dict[str, Any]] = [
    {
        "tag": "cloud_agent",
        "label": "云服务成本优化 / 采购咨询",
        "signals": ["腾讯云", "代理", "优惠", "续费", "首单", "采购", "折扣", "报价", "续费贵", "到期"],
        "pitch": "可先帮对方核对当前资源和续费成本，再给出采购优化与选型建议。",
    },
    {
        "tag": "cvm",
        "label": "云服务器 CVM",
        "signals": ["云服务器", "服务器", "CVM", "带宽", "配置", "主机", "部署", "算力", "内存", "CPU"],
        "pitch": "建议从配置、带宽、地域、预算四个维度切入，提供云服务器选型和成本优化建议。",
    },
    {
        "tag": "database_storage",
        "label": "云数据库 / 云存储",
        "signals": ["数据库", "MySQL", "Redis", "存储", "对象存储", "COS", "备份", "数据迁移", "分布式"],
        "pitch": "建议询问数据规模、可用性要求和迁移窗口，匹配云数据库与存储方案。",
    },
    {
        "tag": "security",
        "label": "网络安全 / 防护",
        "signals": ["安全", "防火墙", "等保", "攻击", "DDoS", "漏洞", "入侵", "WAF", "防护"],
        "pitch": "建议从被攻击场景、等保要求和业务影响切入，提供安全防护初诊。",
    },
    {
        "tag": "meeting",
        "label": "会议 / 协同",
        "signals": ["腾讯会议", "会议", "音视频", "远程会议", "协作", "录制", "会管", "教育", "医疗"],
        "pitch": "建议围绕会议规模、行业场景和安全协作要求，推荐合适的企业协同能力。",
    },
    {
        "tag": "ai_private_deploy",
        "label": "大模型私有化部署",
        "signals": ["DeepSeek", "私有化", "本地部署", "知识库", "内网", "大模型", "模型部署", "企业AI"],
        "pitch": "建议先确认数据是否能出公网、并发和知识库范围，再给出私有化部署路径。",
    },
    {
        "tag": "ai_data_training",
        "label": "AI 数据处理 / 训练微调",
        "signals": ["数据清洗", "标注", "训练", "微调", "评估", "数据集", "训练平台", "模型优化"],
        "pitch": "建议询问数据来源、标注标准和目标任务，匹配数据处理与训练平台服务。",
    },
    {
        "tag": "ai_app",
        "label": "AI 应用开发",
        "signals": ["AI客服", "智能体", "AI助手", "营销助手", "内容自动化", "工作流", "RAG", "客服机器人"],
        "pitch": "建议从现有流程和人力成本切入，给出低成本AI应用落地方案。",
    },
    {
        "tag": "gpu_compute",
        "label": "算力 / 训练环境",
        "signals": ["GPU", "算力", "显卡", "训练环境", "A100", "H800", "租用", "测试资源", "推理"],
        "pitch": "建议确认模型规模、训练/推理场景和预算，推荐算力测试资源。",
    },
]


NEGATIVE_TERMS = [
    "招聘", "求职", "简历", "课程", "培训", "论文", "作业", "考研", "背单词", "数学",
    "塔罗", "穿搭", "买手", "cp", "纯教程", "新闻", "资讯", "搬运", "广告位",
]


PURCHASE_TERMS = [
    "购买", "采购", "报价", "多少钱", "价格", "预算", "续费", "到期", "太贵", "降本",
    "咨询", "怎么选", "选型", "方案", "部署", "私有化", "迁移", "上云", "搭建", "急",
]


def business_direction_options() -> list[dict[str, str]]:
    return [{"value": key, "label": value["label"]} for key, value in BUSINESS_DIRECTIONS.items()]


def build_keywords(direction: str, industry: str = "", region: str = "", custom: str = "") -> list[str]:
    raw_keywords = [k.strip() for k in custom.replace("，", ",").split(",") if k.strip()]
    if not raw_keywords:
        raw_keywords = list(BUSINESS_DIRECTIONS.get(direction, BUSINESS_DIRECTIONS["cloud_ai"])["keywords"])
    qualifiers = [q for q in [region.strip(), industry.strip()] if q]
    if qualifiers:
        expanded = []
        for keyword in raw_keywords:
            expanded.append(keyword)
            expanded.append(" ".join([*qualifiers, keyword]))
        raw_keywords = expanded
    seen = set()
    result = []
    for keyword in raw_keywords:
        if keyword not in seen:
            seen.add(keyword)
            result.append(keyword)
    return result


def match_products(text: str) -> list[dict[str, Any]]:
    matches = []
    lowered = text.lower()
    for product in PRODUCTS:
        matched_terms = [term for term in product["signals"] if term.lower() in lowered or term in text]
        if matched_terms:
            matches.append({
                "tag": product["tag"],
                "label": product["label"],
                "matched_terms": matched_terms[:6],
                "pitch": product["pitch"],
            })
    return sorted(matches, key=lambda item: len(item["matched_terms"]), reverse=True)


def is_negative_text(text: str) -> bool:
    return any(term in text for term in NEGATIVE_TERMS)


def has_purchase_signal(text: str) -> bool:
    return any(term in text for term in PURCHASE_TERMS)


def product_distribution(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for lead in leads:
        for product in lead.get("matched_products") or []:
            counter[product["tag"]] += 1
            labels[product["tag"]] = product["label"]
    return [
        {"tag": tag, "label": labels.get(tag, tag), "count": count}
        for tag, count in counter.most_common()
    ]
