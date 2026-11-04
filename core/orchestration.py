#!/usr/bin/env python3
"""Select the minimum GTM team and the right delivery expression for a task.

This is deterministic orchestration, not a claim that every role is needed.
The output is a candidate Workstream and content plan; external execution still
uses the separately authorized execution path.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .capabilities import get_capability, get_role_family
from .templates import get_template


class OrchestrationError(ValueError):
    pass


def _step(role: str, capability: str, template: str, deliverable: str,
          purpose: str, reason: str) -> Dict[str, Any]:
    return {"role_family": role, "capability_id": capability, "template_id": template,
            "deliverable_type": deliverable, "purpose": purpose, "reason": reason}


# Each recipe starts small. Optional steps are added only when the task actually
# contains their trigger; there is deliberately no "whole marketing department" recipe.
RECIPES: Dict[str, Dict[str, Any]] = {
    "developer_adoption": {
        "required": [
            _step("46", "C18", "T30", "developer_tutorial", "让开发者独立完成真实技术任务", "开发者采用需要可复现技术资产"),
            _step("24", "C10", "T15", "activation_plan", "定义首次价值与 onboarding 承接", "采用目标必须绑定激活而非下载或 Star"),
            _step("44", "C16", "T28", "measurement_plan", "核验首次成功与持续使用", "需要独立事件和可复核口径"),
        ],
        "optional": [
            (("发布", "分发", "社媒", "github", "linkedin", "x", "小红书"),
             _step("12", "C03", "T07", "distribution_pack", "把教程适配到指定渠道并承接讨论", "任务明确要求渠道分发")),
            (("活动", "webinar", " workshop", "发布会"),
             _step("19", "C08", "T13", "developer_event_runbook", "组织开发者活动与后续跟进", "任务包含活动形式")),
        ],
    },
    "content_awareness": {
        "required": [
            _step("09", "C02", "T05", "content_plan", "确定受众任务、选题与内容日历", "内容任务需要选题与事实组织"),
            _step("10", "C02", "T05", "editorial_asset", "形成准确可读的渠道源稿", "需要文案与事实编辑"),
        ],
        "optional": [
            (("社媒", "发布", "小红书", "抖音", "linkedin", "x", "公众号", "微博", "b站"),
             _step("12", "C03", "T07", "channel_distribution_pack", "做平台原生发布与互动承接", "任务包含社媒或发布渠道")),
            (("视频", "视觉", "海报", "素材", "脚本", "创意"),
             _step("11", "C02", "T06", "creative_brief", "制作视觉或视频资产", "任务明确需要创意制作")),
            (("seo", "搜索", "aeo", "geo", "aso"),
             _step("14", "C05", "T09", "discovery_plan", "承接搜索或答案发现", "任务包含搜索发现目标")),
            (("实验", "a/b", "增量"),
             _step("44", "C16", "T28", "experiment_plan", "验证内容带来的真实变化", "任务明确要求实验或增量")),
        ],
    },
    "organic_discovery": {
        "required": [
            _step("14", "C05", "T09", "discovery_plan", "建立查询—页面—行动映射", "搜索任务需要技术与内容共同承接"),
            _step("09", "C02", "T05", "search_content_pack", "生产满足查询意图的内容", "自然发现需要可用内容资产"),
        ],
        "optional": [
            (("转化", "落地页", "cro"),
             _step("25", "C10", "T15", "landing_experiment", "优化搜索到访后的行动", "任务包含转化优化")),
            (("开发者", "api", "sdk", "技术"),
             _step("46", "C18", "T30", "technical_discovery_asset", "保证技术内容可复现", "技术搜索资产需要技术验收")),
            (("社媒", "发布", "小红书", "抖音", "linkedin", "x", "公众号"),
             _step("12", "C03", "T07", "organic_social_distribution", "把搜索内容做渠道原生分发", "任务明确包含社媒分发")),
        ],
    },
    "b2b_pipeline": {
        "required": [
            _step("18", "C07", "T12", "account_demand_plan", "确定目标账户、购买组与培育方式", "B2B 获客需要账户和需求编排"),
            _step("26", "C11", "T16", "qualification_handoff", "核验触发、角色、问题与销售接收", "线索必须经资格和销售交接"),
            _step("27", "C11", "T16", "opportunity_plan", "推进发现、商业论证与成交", "销售接受后需要机会推进"),
            _step("42", "C15", "T26", "sales_stage_dictionary", "统一销售阶段、字段与证据", "跨岗位机会需要统一口径"),
            _step("43", "C16", "T27", "revenue_measurement", "对齐 pipeline、收入与归因边界", "需要收入事实而非 MQL 数量"),
        ],
        "optional": [
            (("poc", "售前", "demo", "技术验证", "方案"),
             _step("28", "C11", "T17", "poc_charter", "限定技术验证范围与成功标准", "任务包含售前或 POC")),
            (("成交后", "客户成功", "续约", "交付", "增购"),
             _step("38", "C14", "T22", "customer_success_plan", "承接采用、价值与续约", "任务包含成交后客户经营")),
            (("活动", "webinar", "展会"),
             _step("19", "C08", "T13", "event_demand_runbook", "用活动形成合格账户进展", "任务包含活动获客")),
            (("广告", "投放", "付费"),
             _step("17", "C06", "T11", "paid_account_plan", "以受控预算覆盖目标账户", "任务明确包含付费媒体")),
        ],
    },
    "sales_development": {
        "required": [
            _step("26", "C11", "T16", "account_research_and_qualification", "研究账户、触达并完成资格交接", "任务聚焦销售开发而非完整市场链"),
            _step("27", "C11", "T16", "receiving_opportunity_plan", "让 AE 接收并推进合格机会", "SDR 结果需要销售接收"),
            _step("42", "C15", "T26", "routing_and_stage_rules", "统一路由、接收与拒收原因", "需要销售运营规则"),
        ],
        "optional": [
            (("邮件", "email"), _step("35", "C13", "T21", "nurture_journey", "未就绪对象回到合规培育", "任务包含邮件培育")),
            (("poc", "售前", "demo"), _step("28", "C11", "T17", "poc_charter", "接入住前技术验证", "任务包含 POC")),
        ],
    },
    "retention": {
        "required": [
            _step("34", "C13", "T21", "segment_retention_plan", "按任务和风险分群并定义持续价值行为", "留存任务需要分群与行为窗口"),
        ],
        "optional": [
            (("邮件", "email", "push", "短信", "crm", "旅程", "触达", "召回"),
             _step("35", "C13", "T21", "lifecycle_journey", "设计有同意、频控和退出的生命周期旅程", "任务包含自动化触达")),
            (("客户成功", "csm", "续约", "qbr", "增购", "b2b"),
             _step("38", "C14", "T22", "success_or_renewal_plan", "管理采用、风险与续约", "任务包含客户成功或续约")),
            (("客服", "工单", "投诉", "服务", "问题解决"),
             _step("40", "C14", "T24", "service_recovery", "先解决阻碍使用的问题", "任务包含服务恢复")),
            (("会员", "积分", "订阅", "权益"),
             _step("37", "C13", "T21", "subscription_loyalty_plan", "处理会员权益与续费成本", "任务包含会员或订阅")),
            (("私域", "社群", "微信群", "企业微信"),
             _step("36", "C13", "T21", "community_retention_plan", "通过成员互助与私域承接留存", "任务包含私域或社群")),
        ],
    },
    "paid_acquisition": {
        "required": [
            _step("17", "C06", "T11", "paid_media_plan", "控制预算、受众、素材与边际成本", "付费获客需要媒体与预算契约"),
        ],
        "optional": [
            (("素材", "创意", "视频", "视觉"), _step("11", "C02", "T06", "ad_creative_pack", "提供合规广告素材", "任务包含素材制作")),
            (("落地页", "转化", "cro"), _step("25", "C10", "T15", "conversion_experiment", "核验广告到访后的转化", "任务包含落地页或 CRO")),
            (("实验", "增量", "a/b"), _step("44", "C16", "T28", "incrementality_plan", "区分平台归因与真实增量", "任务包含实验或增量")),
            (("b2b", "账户", "线索", "商机"), _step("18", "C07", "T12", "lead_quality_handoff", "核验付费线索的销售质量", "B2B 付费获客需要销售质量承接")),
        ],
    },
    "launch_campaign": {
        "required": [
            _step("19", "C08", "T13", "campaign_runbook", "组织活动、现场与会后承接", "活动需要分钟级 runbook 与跟进"),
        ],
        "optional": [
            (("内容", "文章", "视频", "传播"), _step("09", "C02", "T05", "launch_content_pack", "准备活动内容资产", "任务包含传播内容")),
            (("社媒", "小红书", "linkedin", "x", "公众号"), _step("12", "C03", "T07", "launch_social_pack", "做渠道原生发布和互动", "任务包含社媒")),
            (("开发者", "api", "开源", "sdk"), _step("46", "C18", "T30", "developer_launch_asset", "用可运行示例支撑发布", "开发者发布需要技术资产")),
            (("b2b", "线索", "商机", "销售"), _step("18", "C07", "T12", "event_sales_handoff", "把活动参与交给销售承接", "B2B 活动需要需求与销售交接")),
        ],
    },
    "commerce": {
        "required": [
            _step("29", "C12", "T18", "commerce_plan", "组织商品、库存、促销、订单与净收入", "交易任务需要商品和订单口径"),
        ],
        "optional": [
            (("直播", "主播"), _step("30", "C12", "T19", "live_runbook", "组织直播货盘、脚本与实时异常", "任务包含直播")),
            (("商家", "供应商", "招商"), _step("31", "C12", "T20", "merchant_plan", "组织有效供给与履约", "任务包含商家供给")),
            (("投放", "广告"), _step("17", "C06", "T11", "commerce_media_plan", "控制付费流量与贡献", "任务包含付费投放")),
            (("售后", "退款", "客服"), _step("40", "C14", "T24", "commerce_service_recovery", "处理订单售后与服务恢复", "任务包含售后")),
        ],
    },
    "partner_growth": {
        "required": [
            _step("32", "C09", "T14", "partner_business_plan", "设计伙伴价值、职责、报备与收入", "渠道合作需要伙伴经营计划"),
        ],
        "optional": [
            (("流量", "联合推广"), _step("20", "C09", "T14", "traffic_partner_plan", "管理联合流量质量与对账", "任务包含流量合作")),
            (("联盟", "推荐", "返佣", "affiliate", "referral"), _step("21", "C09", "T14", "referral_program", "管理推荐激励、反作弊与结算", "任务包含联盟或推荐")),
            (("区域", "海外", "本地化"), _step("23", "C09", "T14", "regional_execution_plan", "处理区域语言、合规与承接", "任务包含区域执行")),
        ],
    },
    "app_growth": {
        "required": [
            _step("22", "C06", "T10", "app_growth_plan", "组织商店、安装归因、激活与版本质量", "App 增长需要商店与版本口径"),
            _step("24", "C10", "T15", "app_activation_plan", "承接安装后的首次价值", "安装不是激活"),
        ],
        "optional": [
            (("投放", "广告", "cpi"), _step("17", "C06", "T11", "app_paid_plan", "管理安装广告与激活成本", "任务包含付费安装")),
            (("push", "邮件", "留存", "召回"), _step("35", "C13", "T21", "app_lifecycle_plan", "管理应用内与生命周期触达", "任务包含留存触达")),
        ],
    },
    "brand_campaign": {
        "required": [
            _step("07", "C01", "T03", "brand_campaign_plan", "在批准定位下组织品牌消息与资产", "品牌任务需要一致的消息与测量"),
        ],
        "optional": [
            (("公关", "媒体", "危机", "舆情"), _step("08", "C01", "T04", "media_response_plan", "管理媒体、发言人与危机边界", "任务包含公关或舆情")),
            (("创意", "视觉", "视频", "素材"), _step("11", "C02", "T06", "brand_creative_pack", "制作品牌创意资产", "任务包含创意制作")),
            (("社媒", "发布", "小红书", "linkedin", "x"), _step("12", "C03", "T07", "brand_social_pack", "做品牌渠道适配与互动", "任务包含社媒传播")),
        ],
    },
}


AUDIENCES = {
    "executive": {
        "format": "decision_memo", "length": "1 页结论 + 必要附录",
        "tone": "结论先行、写清取舍，不堆执行细节",
        "sections": ["结论与本轮目标", "基线和关键假设", "最小工作方案", "结果/护栏与资源", "风险和不做什么", "需要决策的事项"],
    },
    "operator": {
        "format": "operator_runbook", "length": "完整可执行正文 + 表格/清单",
        "tone": "具体、可分工、可验收，少用口号",
        "sections": ["目标与范围", "输入和事实来源", "岗位与 RACI", "步骤与日程", "交付物和 DoD", "异常/停止/回滚", "KPI 与复盘"],
    },
    "developer": {
        "format": "technical_tutorial", "length": "以可复现为限，不设营销字数",
        "tone": "技术准确、少形容词、明确版本和限制",
        "sections": ["开发者任务", "环境与前置", "最小示例", "命令与预期输出", "常见错误", "首次成功事件", "限制与支持"],
    },
    "sales": {
        "format": "sales_handoff", "length": "一页可接手正文 + 证据附件",
        "tone": "事实、原话、承诺和下一步，不写泛泛客户画像",
        "sections": ["账户与触发", "对方原话/行为", "资格与缺口", "已做动作", "承诺与未决风险", "下一动作/Owner/时间"],
    },
    "customer": {
        "format": "customer_ready_plan", "length": "客户可读的范围与行动计划",
        "tone": "清楚、克制、无内部编码，不夸大结果",
        "sections": ["客户目标", "范围/非目标", "双方责任", "里程碑", "验收证据", "风险与下一步"],
    },
    "partner": {
        "format": "partner_onepager", "length": "一页共同计划 + 条款附件",
        "tone": "共同价值、双方责任、可核算",
        "sections": ["共同目标", "各自投入", "客户/区域边界", "线索与收入归属", "测量与结算", "退出条件"],
    },
}


CHANNELS = {
    "email": {"format": "personalized_email", "rules": ["具体称呼与触发", "一个价值点", "一个 CTA", "主题与正文一致", "无模板占位符"]},
    "github": {"format": "technical_issue_or_comment", "rules": ["先给可复现结果", "引用 issue/版本", "命令和预期输出", "不做营销式求 Star"]},
    "x": {"format": "short_post_or_thread", "rules": ["首条独立成立", "短句", "一条主线", "链接后置", "不批量灌水"]},
    "linkedin": {"format": "professional_narrative", "rules": ["场景洞察", "事实或案例", "专业判断", "一个讨论问题"]},
    "xiaohongshu": {"format": "scenario_card_or_note", "rules": ["具体场景标题", "短段落", "经验/步骤卡片", "避免硬广和绝对化承诺"]},
    "wechat": {"format": "structured_longform", "rules": ["摘要先行", "小标题分节", "完整论证", "结尾一个行动建议"]},
    "landing": {"format": "landing_page", "rules": ["单一价值主张", "证据", "适用/不适用", "一个主 CTA", "异议与 FAQ"]},
    "presentation": {"format": "slide_outline", "rules": ["一页一个结论", "标题即判断", "证据入图", "最后决策请求"]},
}


def _contains(text: str, keyword: str) -> bool:
    if keyword == "x":
        return re.search(r"(?:在|于|到)\s*x\b|\bx\s*(?:上|发布|发帖|运营)|twitter", text, re.IGNORECASE) is not None
    return keyword in text


def infer_motion(product: Dict[str, Any], goal: str, task: str) -> str:
    text = f"{goal} {task}".lower()
    # More specific task shapes precede broad product defaults.
    if any(k in text for k in ("sdr", "bdr", "陌生开发", "销售开发")) and not any(
            k in text for k in ("abm", "pipeline", "全链路", "poc", "需求增长")):
        return "sales_development"
    for motion, keywords in (
        ("commerce", ("电商", "直播", "商品", "店铺", "gmv")),
        ("app_growth", ("aso", "应用商店", "app 增长", "安装量", "cpi")),
        ("partner_growth", ("联盟", "返佣", "渠道伙伴", "bd", "联合推广", "referral", "affiliate")),
        ("paid_acquisition", ("广告投放", "付费获客", "效果广告", "media buyer", "信息流")),
        ("retention", ("留存", "召回", "续费", "复购", "流失", "客户成功", "会员", "私域")),
        ("b2b_pipeline", ("abm", "b2b", "企业客户", "商机", "pipeline", "销售线索")),
        ("organic_discovery", ("seo", "aeo", "geo", "搜索流量", "自然搜索")),
        ("launch_campaign", ("活动", "webinar", "展会", "launch", "发布会")),
        ("brand_campaign", ("品牌", "公关", "媒体关系", "危机")),
        ("developer_adoption", ("开发者", "api", "sdk", "devrel", "开源", "技术教程")),
        ("content_awareness", ("内容", "社媒", "小红书", "公众号", "发帖", "文案")),
    ):
        if any(_contains(text, keyword) for keyword in keywords):
            return motion
    product_type = str(product.get("product_type") or product.get("type") or "").lower()
    return {
        "devtool": "developer_adoption", "developer": "developer_adoption",
        "b2b_saas": "b2b_pipeline", "b2b": "b2b_pipeline",
        "ecommerce": "commerce", "dtc": "commerce", "mobile": "app_growth", "app": "app_growth",
    }.get(product_type, "content_awareness")


def _optional_steps(motion: str, task: str, channel: str = "") -> List[Dict[str, Any]]:
    text = task.lower()
    return [step for keywords, step in RECIPES[motion]["optional"]
            if any(_contains(text, k) or (k == "x" and channel == "x") for k in keywords)]


def _audience(value: str, task: str) -> str:
    if value:
        aliases = {"高管": "executive", "老板": "executive", "运营": "operator", "开发者": "developer",
                   "销售": "sales", "客户": "customer", "伙伴": "partner"}
        return aliases.get(value.lower(), aliases.get(value, value))
    text = task.lower()
    if any(k in text for k in ("高管", "老板", "决策人", "ceo", "cfo")):
        return "executive"
    if any(k in text for k in ("开发者", "程序员", "工程师")):
        return "developer"
    if any(k in text for k in ("销售交接", "ae 接收", "销售接收")):
        return "sales"
    return "operator"


def _channel(value: str) -> str:
    aliases = {"邮件": "email", "小红书": "xiaohongshu", "公众号": "wechat", "微信": "wechat",
               "推特": "x", "twitter": "x", "落地页": "landing", "ppt": "presentation"}
    return aliases.get((value or "").lower(), aliases.get(value, value.lower() if value else ""))


def _cta(goal: str, motion: str) -> str:
    text = goal.lower()
    if any(k in text for k in ("留存", "续费", "复购", "召回")) or motion == "retention":
        return "恢复并持续完成定义的价值行为；续费需另看合同/回款"
    if any(k in text for k in ("付费", "收入", "商机", "销售", "commercial")) or motion in ("b2b_pipeline", "sales_development"):
        return "确认资格与下一承诺；付款/合同由有权人员处理"
    if any(k in text for k in ("声量", "曝光", "品牌", "awareness")):
        return "让目标受众理解并回应核心信息，不把曝光当业务转化"
    return "完成一个可核验的目标任务或首次价值事件"


ARTIFACT_PRESENTATION = {
    "T03": "brand_campaign_brief",
    "T04": "approved_media_and_crisis_card",
    "T05": "editorial_brief_and_source_draft",
    "T06": "production_ready_creative_brief",
    "T07": "channel_calendar_and_interaction_runbook",
    "T08": "creator_brief_disclosure_rights_and_settlement",
    "T09": "query_page_action_map",
    "T10": "growth_loop_and_experiment_card",
    "T11": "budget_media_and_guardrail_table",
    "T12": "account_buying_group_and_pipeline_plan",
    "T13": "minute_by_minute_runbook",
    "T14": "mutual_partner_business_plan",
    "T15": "activation_or_cro_experiment_card",
    "T16": "sales_handoff_and_mutual_action_plan",
    "T17": "customer_approved_poc_charter",
    "T18": "sku_order_margin_operating_sheet",
    "T19": "live_minute_sku_and_exception_runbook",
    "T20": "merchant_or_store_operating_sheet",
    "T21": "lifecycle_journey_and_frequency_spec",
    "T22": "customer_success_and_renewal_plan",
    "T23": "customer_approved_evidence_story",
    "T24": "support_resolution_and_service_recovery_record",
    "T25": "campaign_configuration_and_test_sheet",
    "T26": "sales_stage_exit_and_forecast_dictionary",
    "T27": "metric_contract_and_operating_review",
    "T28": "reproducible_experiment_decision_memo",
    "T29": "automation_contract_and_rollback_runbook",
    "T30": "reproducible_developer_tutorial",
}


def _deliverable_expression(step: Dict[str, Any], expression: Dict[str, Any]) -> Dict[str, Any]:
    template = get_template(step["template_id"]) or {}
    presentation = ARTIFACT_PRESENTATION.get(step["template_id"], expression["format"])
    # Channel style applies to distribution/public assets, not to internal POC,
    # revenue dictionaries, support records or technical source documentation.
    public_templates = {"T03", "T04", "T05", "T06", "T07", "T08", "T13", "T23", "T30"}
    result = {
        "presentation": presentation,
        "required_content": copy.deepcopy(template.get("professional_fields") or []),
        "tone": expression["tone"],
        "channel": expression["channel"] if step["template_id"] in public_templates else "artifact_default",
        "channel_rules": copy.deepcopy(expression["channel_rules"]) if step["template_id"] in public_templates else [],
    }
    if step["template_id"] == "T30":
        result["tone"] = "技术准确、命令可运行、预期输出明确，不使用社媒营销口吻"
    elif step["template_id"] in ("T16", "T17", "T22", "T24"):
        result["tone"] = "事实、范围、责任、证据和下一步，不做渠道化改写"
    return result


def orchestrate(product: Dict[str, Any], goal: str, task: str, *,
                audience: str = "", channel: str = "", motion: str = "") -> Dict[str, Any]:
    if not task.strip():
        raise OrchestrationError("task 不能为空，无法判断需要哪些岗位")
    if not goal.strip():
        raise OrchestrationError("goal 不能为空，无法决定交付结果和 CTA")
    motion = motion or infer_motion(product, goal, task)
    if motion not in RECIPES:
        raise OrchestrationError(f"未知 motion: {motion}；可选 {sorted(RECIPES)}")
    audience = _audience(audience, task)
    if audience not in AUDIENCES:
        raise OrchestrationError(f"未知 audience: {audience}；可选 {sorted(AUDIENCES)}")
    channel = _channel(channel)
    if channel and channel not in CHANNELS:
        raise OrchestrationError(f"未知 channel: {channel}；可选 {sorted(CHANNELS)}")
    steps = copy.deepcopy(RECIPES[motion]["required"]) + copy.deepcopy(_optional_steps(motion, f"{goal} {task}", channel))
    if not steps:
        raise OrchestrationError("没有匹配到必要岗位")
    work_orders = []
    handoffs = []
    previous = None
    for index, step in enumerate(steps, 1):
        local_id = f"wo-{index:02d}-{step['deliverable_type']}"
        work_orders.append({
            "local_id": local_id,
            "title": step["purpose"],
            "role_family": step["role_family"],
            "capability_id": step["capability_id"],
            "depends_on": [previous] if previous else [],
            "deliverable_specs": [{
                "template_id": step["template_id"], "template_version": "1.0.0",
                "deliverable_type": step["deliverable_type"], "required": True,
            }],
            "selection_reason": step["reason"],
            "sop_status": "full" if motion == "developer_adoption" and step["role_family"] == "46" else "requires_expansion",
        })
        if previous:
            handoffs.append({"from_work_order": previous, "to_work_order": local_id,
                             "artifact_type": work_orders[index - 2]["deliverable_specs"][0]["deliverable_type"],
                             "receiver_role": step["role_family"],
                             "acceptance": "接收方确认输入版本、事实、未决项与下一步后才 accepted"})
        previous = local_id
    expression = copy.deepcopy(AUDIENCES[audience])
    expression.update({
        "audience": audience,
        "channel": channel or "artifact_default",
        "cta": _cta(goal, motion),
        "evidence_rules": ["事实与数字有 source_ref", "用户/客户原话保留上下文", "近似与未核验数据单列", "动作完成不替代业务结果"],
        "exclusions": ["不加入未选岗位的长篇组织图", "不编造行业基准", "不把付款/合同/危机决定包装成自动动作"],
    })
    if channel:
        channel_rule = CHANNELS[channel]
        expression["format"] = channel_rule["format"]
        expression["channel_rules"] = copy.deepcopy(channel_rule["rules"])
    else:
        expression["channel_rules"] = []
    deliverables = []
    for work_order, step in zip(work_orders, steps):
        deliverables.append({
            "work_order_id": work_order["local_id"],
            **copy.deepcopy(work_order["deliverable_specs"][0]),
            "purpose": work_order["title"],
            "expression": _deliverable_expression(step, expression),
        })
    signature = json.dumps({"product": product, "goal": goal, "task": task, "motion": motion,
                            "audience": audience, "channel": channel}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": "1.0",
        "kind": "growovo.workstream_candidate",
        "orchestration_id": f"orch-{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:12]}",
        "status": "candidate_not_execution_approval",
        "product": copy.deepcopy(product),
        "goal": goal,
        "task": task,
        "motion": motion,
        "selection": {
            "mode": "minimum_required_roles",
            "reason": "按产品/目标/任务选择必要岗位，不默认使用全部 40 岗位",
            "role_families": [s["role_family"] for s in steps],
            "capability_ids": sorted({s["capability_id"] for s in steps}),
        },
        "workstream": {"work_orders": work_orders, "handoffs": handoffs},
        "content_plan": {
            "deliverables": deliverables,
            "expression": expression,
        },
        "execution_boundary": "该输出是工作编排与内容表达方案，不是付款、合同或平台发布批准。",
    }


def lint_orchestration(plan: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    orders = plan.get("workstream", {}).get("work_orders", [])
    if not orders:
        errors.append("Workstream 至少一个 WorkOrder")
        return errors
    ids = set()
    for order in orders:
        if order["local_id"] in ids:
            errors.append(f"重复 WorkOrder: {order['local_id']}")
        ids.add(order["local_id"])
        if get_role_family(order["role_family"]) is None:
            errors.append(f"未知岗位: {order['role_family']}")
        if get_capability(order["capability_id"]) is None:
            errors.append(f"未知能力域: {order['capability_id']}")
        for spec in order["deliverable_specs"]:
            if get_template(spec["template_id"]) is None:
                errors.append(f"未知模板: {spec['template_id']}")
        for dep in order["depends_on"]:
            if dep not in ids:
                errors.append(f"依赖必须指向已定义前置工作: {dep}")
    return errors


def render_markdown(plan: Dict[str, Any]) -> str:
    """Render the selected work and expression as an actionable planning brief."""
    lines = [f"# GTM 工作编排 · {plan['motion']}", "",
             f"- 产品：{plan['product'].get('name') or plan['product'].get('product_type') or '未命名'}",
             f"- 目标：{plan['goal']}", f"- 任务：{plan['task']}",
             "- 组队原则：只选任务必需岗位，不默认使用全部岗位。", "",
             "## 最小 Workstream", "",
             "| 工作 | 岗位 | 交付物 | 模板 | 前置 | 为什么需要 |", "|---|---|---|---|---|---|"]
    for order in plan["workstream"]["work_orders"]:
        spec = order["deliverable_specs"][0]
        lines.append("| {title} | {role} | {artifact} | {template} | {deps} | {reason} |".format(
            title=order["title"], role=order["role_family"], artifact=spec["deliverable_type"],
            template=spec["template_id"], deps=", ".join(order["depends_on"]) or "—",
            reason=order["selection_reason"]))
    expression = plan["content_plan"]["expression"]
    lines += ["", "## 整体表达方式", "",
              f"- 形式：{expression['format']}", f"- 长度：{expression['length']}",
              f"- 语气：{expression['tone']}", f"- 主行动：{expression['cta']}", "",
              "### 方案结构", ""]
    lines.extend(f"{index}. {section}" for index, section in enumerate(expression["sections"], 1))
    if expression["channel_rules"]:
        lines += ["", "### 渠道表达规则", ""]
        lines.extend(f"- {rule}" for rule in expression["channel_rules"])
    lines += ["", "## 每份交付物怎么写", ""]
    for item in plan["content_plan"]["deliverables"]:
        exp = item["expression"]
        lines += [f"### {item['work_order_id']} · {item['deliverable_type']}", "",
                  f"- 表达：{exp['presentation']}", f"- 语气：{exp['tone']}"]
        if exp["required_content"]:
            lines.append("- 必写内容：" + "；".join(field["label"] for field in exp["required_content"]))
        if exp["channel_rules"]:
            lines.append("- 渠道规则：" + "；".join(exp["channel_rules"]))
        lines.append("")
    lines += ["## 验收与边界", ""]
    lines.extend(f"- {rule}" for rule in expression["evidence_rules"])
    lines.extend(f"- {rule}" for rule in expression["exclusions"])
    lines += ["", f"> {plan['execution_boundary']}"]
    return "\n".join(lines)
