#!/usr/bin/env python3
"""mkt-server —— 执行内核的 MCP 外壳（05 产品方案 §5.2）。

stdio 传输、JSON-RPC 2.0，零第三方依赖（不装 MCP SDK 也可被 Claude Desktop /
ZCode 等 MCP 客户端连接）。配法（客户端 mcp 配置）：
  {"mkt": {"command": "python3", "args": ["<本仓库绝对路径>/mcp_server.py"]}}

安全设计（把 449 教训做进 API 形状）：
  - 写操作只有两个入口：队列粒度（mkt_run_queue，全渠道）与单条动作（mkt_action，
    全渠道）——两者共用暂停/审批/账本，没有绕过留痕的路径
  - 写操作默认 dry-run；go=true 需显式传
  - UNVERIFIED 渠道（reddit/linkedin/hn/github 写）拒绝实跑；首跑校准（allow_unverified）
    是 CLI 人工通道专属参数，MCP 既不暴露也不接受，agent 不得自行解除渠道验证门禁
  - 实跑须绑定已人审 plan，或在工作区已有有效 routine policy 时显式 routine=true；
    无 plan 的 adhoc 放行**只在 CLI 人工通道（--adhoc）开放**，MCP 不暴露授权管理，
    agent 不得自我创建/撤销授权或绕过其他安全门
  - 工具执行期间 stdout 重定向到 stderr，stdout 只留 JSON-RPC（stdio 协议不可污染）
  - 凭据只在服务端进程环境变量，不经工具参数往返
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from channels import REGISTRY  # noqa: E402
from core import transports, workspace  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.planlint import PlanNotApproved, enforce_approval, lint_plan  # noqa: E402

DATA = workspace.resolve(base=BASE)
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "mkt_memory",
        "description": "只读运营经验：从真实操作回执汇总 channel/action 模式，不暴露目标或内容，不允许自动重放。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "campaign": {"type": "string"},
                "channel": {"type": "string"},
                "minimum_confirmed": {"type": "integer", "default": 3},
            },
        },
    },
    {
        "name": "mkt_scorecard",
        "description": "只读运营记分卡：执行回执的成功/未知/失败与业务结果计数；比率是描述口径，不是渠道效果。",
        "inputSchema": {
            "type": "object",
            "properties": {"campaign": {"type": "string"}, "since": {"type": "string"}},
        },
    },
    {
        "name": "mkt_optimize",
        "description": "只读优化建议：按记分卡与回执模式提出下一步；requires_human=true，不自动改预算或重放。",
        "inputSchema": {
            "type": "object",
            "properties": {"campaign": {"type": "string"}, "channel": {"type": "string"},
                           "minimum_confirmed": {"type": "integer", "default": 3}},
        },
    },
    {
        "name": "mkt_workflow",
        "description": "只读工作流视图：列表/详情/结构校验/运行记录；审批与重放只在 CLI 人工通道。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"enum": ["list", "show", "lint", "status"]},
                "workflow_id": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "mkt_orchestrate",
        "description": "只读编排：按产品/目标/任务选择最小岗位组合、交付物、交接和表达方式；不自动组队执行或发布。",
        "inputSchema": {"type": "object", "properties": {
            "product": {"type": "object"}, "goal": {"type": "string"}, "task": {"type": "string"},
            "audience": {"type": "string"}, "channel": {"type": "string"}, "motion": {"type": "string"}},
            "required": ["product", "goal", "task"]},
    },
    {
        "name": "mkt_gtm_design",
        "description": "只读 GTM 设计：把增长任务拆成对象×目标×机制×渠道，做约束检查并给出结果契约；不执行、不发送、不预测。",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string"}, "object": {"type": "string"}, "objective": {"type": "string"},
            "constraints": {"type": "object"}, "proposal": {"type": "object"}},
            "required": ["task"]},
    },
    {
        "name": "mkt_capabilities",
        "description": "只读 GTM 能力注册表：18 能力域、40 岗位族及其职责/KPI/SOP/交付物/审批映射。",
        "inputSchema": {"type": "object", "properties": {
            "capability_id": {"type": "string"}, "role_family": {"type": "string"},
            "lint": {"type": "boolean", "default": False}}},
    },
    {
        "name": "mkt_templates",
        "description": "只读 GTM 交付物模板：32 个模板的版本、必填字段与 QA；可校验实例，不写工作区。",
        "inputSchema": {"type": "object", "properties": {
            "template_id": {"type": "string"}, "version": {"type": "string"},
            "fields": {"type": "object"}, "lint_registry": {"type": "boolean", "default": False}}},
    },
    {
        "name": "mkt_work_order",
        "description": "只读工作操作系统：WorkOrder 列表/详情/下一步与关联 SOP run、交付物、交接；不创建、不审批。",
        "inputSchema": {"type": "object", "properties": {
            "action": {"enum": ["list", "show", "next"]}, "work_order_id": {"type": "string"}},
            "required": ["action"]},
    },
    {
        "name": "mkt_metric_measure",
        "description": "只读 KPI 计算：按版本化指标字典对提供的事件确定性测量；分母为 0 返回 null，不伪造业务结果。",
        "inputSchema": {"type": "object", "properties": {
            "metric_id": {"type": "string"}, "version": {"type": "string"},
            "events": {"type": "array", "items": {"type": "object"}},
            "scope": {"type": "object"}, "cohort_end": {"type": "string"}, "as_of": {"type": "string"}},
            "required": ["metric_id", "events"]},
    },
    {
        "name": "mkt_status",
        "description": "全局账本聚合：按 channel/campaign/result 计数。只读。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mkt_channels",
        "description": "渠道注册表：能力（六原语子集）、验证等级（PROVEN/PORTED/UNVERIFIED）、"
                       "通道矩阵（每个渠道×动作的首选通道与降级链）。只读。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mkt_plan_lint",
        "description": "校验营销计划 plan.json：schema 完整性、渠道能力矩阵、"
                       "波次规模不超内核日预算、失败判据预注册。返回错误清单，空=通过。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {"plan_path": {"type": "string", "description": "plan.json 路径"}},
            "required": ["plan_path"],
        },
    },
    {
        "name": "mkt_search",
        "description": "渠道只读搜索（客群/讨论/对标发现）。channel ∈ x|reddit|linkedin|hn|github。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["x", "reddit", "linkedin", "hn", "github"]},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["channel", "query"],
        },
    },
    {
        "name": "mkt_ledger_query",
        "description": "账本查询：按 channel/campaign/action/target/result 过滤。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "campaign": {"type": "string"},
                "result": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "mkt_inbox",
        "description": "只读收件箱/首响巡检：channel ∈ hn|github|email|x。"
                       "github 需 repo；hn 读 submitted 新回复；email 扫 Gmail；x 扫 vendor inbox。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["hn", "github", "email", "x"]},
                "repo": {"type": "string", "description": "github：owner/repo"},
            },
            "required": ["channel"],
        },
    },
    {
        "name": "mkt_audit",
        "description": "GitHub 仓库体检（基础/社区健康/releases/贡献者集中度/AI-readiness/Scorecard）。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
        },
    },
    {
        "name": "mkt_action",
        "description": "单条写动作（与队列同一套门禁/暂停/审批/账本）。channel ∈ x|reddit|linkedin|github|hn；"
                       "action ∈ publish|reply|message|engage。默认 dry-run；go=true 需已人审 plan 或有效 routine policy 且 routine=true；"
                       "UNVERIFIED 渠道实跑首跑校准仅限人工 CLI 通道，MCP 不提供绕过。email 只支持队列。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["x", "reddit", "linkedin", "github", "hn"]},
                "action": {"type": "string", "enum": ["publish", "reply", "message", "engage"]},
                "target": {"type": "string"},
                "text": {"type": "string"},
                "title": {"type": "string"},
                "url": {"type": "string"},
                "subreddit": {"type": "string"},
                "number": {"type": "integer"},
                "campaign": {"type": "string"},
                "account": {"type": "string", "description": "routine policy 精确匹配的执行账号"},
                "extra": {"type": "array", "items": {"type": "string"}},
                "go": {"type": "boolean", "default": False},
                "routine": {"type": "boolean", "default": False,
                            "description": "仅在工作区已有有效日常营销授权时使用；不能自我授权"},
            },
            "required": ["channel", "action"],
        },
    },
    {
        "name": "mkt_campaign",
        "description": "Campaign 视图：计划/人审状态/队列预检/账本/事件/实验/下一步。只读。"
                       "campaign 形如 <plan_id>-w1。",
        "inputSchema": {
            "type": "object",
            "properties": {"campaign": {"type": "string"}},
            "required": ["campaign"],
        },
    },
    {
        "name": "mkt_run_queue",
        "description": "执行一个队列（全渠道：email/hn/github/reddit/linkedin/x-reply）。"
                       "默认 dry-run；go=true 实跑；UNVERIFIED 渠道首跑校准仅限人工 CLI 通道；"
                       "实跑须已人审 plan，或显式 routine=true 且策略匹配（无 plan 的 adhoc 仅 CLI；授权管理不对 MCP 开放）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queue_path": {"type": "string", "description": "data/queues/<campaign>.json"},
                "account": {"type": "string", "description": "可选：覆盖队列顶层账号，供 routine policy 精确校验"},
                "go": {"type": "boolean", "default": False},
                "routine": {"type": "boolean", "default": False,
                            "description": "仅使用已存在的 routine policy；MCP 不能创建或撤销授权"},
            },
            "required": ["queue_path"],
        },
    },
    {
        "name": "mkt_retro_scan",
        "description": "只读扫描本地目录，统计可读文件与可提取的事实条数；不写盘、不上传。用于复盘前的体量确认。",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "description": "要扫描的目录"},
            "max_files": {"type": "integer", "default": 20000}}, "required": ["path"]},
    },
    {
        "name": "mkt_retro_analyze",
        "description": "只读复盘分析：渠道账、浪费、冲突、以及日志里被反复提到的那几句话。口径仍由人钉。",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"},
            "facts": {"type": "object", "description": "人工钉定的口径：{渠道:{动作,回应,结果}}，可省略"},
            "max_files": {"type": "integer", "default": 20000}}, "required": ["path"]},
    },
    {
        "name": "mkt_goals",
        "description": "只读目标选项：按产品类型/阶段列出声量、使用、商业三类目标，每个带指标、判据、证明/不证明和推荐平台。",
        "inputSchema": {"type": "object", "properties": {
            "facts": {"type": "object", "description": "产品事实包（同 mkt gtm diagnose）"}},
            "required": ["facts"]},
    },
    {
        "name": "mkt_channel_venues",
        "description": "只读渠道库：具体的地方（subreddit/Slack/newsletter/会议），带人群、能否发、信号密度、竞争与风险。",
        "inputSchema": {"type": "object", "properties": {
            "platform": {"type": "string"}}},
    },
    {
        "name": "mkt_channel_plan",
        "description": "只读渠道方案：目标→主/发射/辅助/旁听 + 经济学区间 + 容量 + 预注册实验。",
        "inputSchema": {"type": "object", "properties": {
            "facts": {"type": "object"}, "goal": {"type": "string"},
            "secondary": {"type": "string"},
            "platforms": {"type": "array", "items": {"type": "string"}}},
            "required": ["facts", "goal"]},
    },
    {
        "name": "mkt_deals",
        "description": "只读转化：阶段、漏斗、卡住的对象；读 DATA/deals.jsonl。",
        "inputSchema": {"type": "object", "properties": {
            "stage": {"type": "string"}}},
    },
    {
        "name": "mkt_deal_next",
        "description": "只读转化：今天该跟谁、下一步动作、为什么、几天内。",
        "inputSchema": {"type": "object", "properties": {"on": {"type": "string"}}},
    },
    {
        "name": "mkt_sprint_plan",
        "description": "只读冲刺方案：目标方案→N 天并行工作流、小时级排期、每日目标、加速杠杆、容量校验与红线。",
        "inputSchema": {"type": "object", "properties": {
            "facts": {"type": "object"}, "goal": {"type": "string"},
            "secondary": {"type": "string"},
            "platforms": {"type": "array", "items": {"type": "string"}},
            "target": {"type": "number"}, "days": {"type": "integer", "default": 3},
            "start": {"type": "string"}, "hours_per_day": {"type": "number"},
            "launch_hour": {"type": "string"}},
            "required": ["facts", "goal"]},
    },
    {
        "name": "mkt_goal_plan",
        "description": "只读目标方案：目标+平台→平台分工、前两周动作、里程碑、止损线；不写文件、不发消息。",
        "inputSchema": {"type": "object", "properties": {
            "facts": {"type": "object"}, "goal": {"type": "string"},
            "platforms": {"type": "array", "items": {"type": "string"}},
            "target": {"type": "number"}, "window_days": {"type": "integer"},
            "secondary": {"type": "string"}, "secondary_target": {"type": "number"}},
            "required": ["facts", "goal"]},
    },
    {
        "name": "mkt_strategy",
        "description": "只读策略推演：从产品事实包和复盘口径算出主渠道、保留清单、不做清单、冷启动五阶段、放大条件、风控映射。",
        "inputSchema": {"type": "object", "properties": {
            "facts": {"type": "object", "description": "产品事实包（同 mkt gtm diagnose）"},
            "retro_facts": {"type": "object"}}, "required": ["facts"]},
    },
    {
        "name": "mkt_cadence",
        "description": "只读落地设计：按四周切出动作表，每条带负责人、人工分钟与验收物。",
        "inputSchema": {"type": "object", "properties": {
            "facts": {"type": "object"}, "start": {"type": "string"},
            "actions_per_week": {"type": "integer", "default": 20}},
            "required": ["facts", "start"]},
    },
    {
        "name": "mkt_result",
        "description": "只读结果：平台动作/任务结果/业务结果三层分开算，并对照判据。取不到的指标报「未测」，不当 0。",
        "inputSchema": {"type": "object", "properties": {
            "criteria": {"type": "array", "items": {"type": "object"}}}},
    },
    {
        "name": "mkt_revise",
        "description": "只读回流：给出「改哪个参数、从多少到多少」并标参数级/策略级。样本不足会拒绝下结论。",
        "inputSchema": {"type": "object", "properties": {
            "result": {"type": "object"}, "params": {"type": "object"}}},
    },
    {
        "name": "mkt_followup_due",
        "description": "只读跟进：今天该跟谁、哪些已经跟丢超过 7 天。",
        "inputSchema": {"type": "object", "properties": {"on": {"type": "string"}}},
    },
    {
        "name": "mkt_progress",
        "description": "只读进度：日报问「今天有什么该动没动」，周报问「这周比上周好还是差」。",
        "inputSchema": {"type": "object", "properties": {
            "scope": {"type": "string", "enum": ["daily", "weekly"], "default": "weekly"},
            "on": {"type": "string"}}},
    },
    {
        "name": "mkt_voice",
        "description": "平台语气画像与开头骨架：这个平台怎么说话、开头怎么写、什么会被当成广告。",
        "inputSchema": {"type": "object", "properties": {
            "platform": {"type": "string"},
            "hook": {"type": "string"}, "what": {"type": "string"}},
            "required": ["platform"]},
    },
    {
        "name": "mkt_voice_lint",
        "description": "检查一段文字在指定平台会不会被当成广告；返回问题列表，空表示可用。",
        "inputSchema": {"type": "object", "properties": {
            "platform": {"type": "string"}, "text": {"type": "string"}},
            "required": ["platform", "text"]},
    },
    {
        "name": "mkt_experience",
        "description": "用户经验库：别人真跑过的平台做法，带出处与样本量；≥2 篇才算规律，1 篇只能当假设。",
        "inputSchema": {"type": "object", "properties": {"platform": {"type": "string"}},
                        "required": ["platform"]},
    },
    {
        "name": "mkt_playbooks",
        "description": "可用 playbook 清单：每个都带证据来源与风险提示，没有证据的会自己承认。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mkt_signal_interpretation",
        "description": "只读校验宿主结构化语义候选；技术讨论、自报购买意图和不可接触对象不进入可执行机会。",
        "inputSchema": {"type": "object", "properties": {
            "candidates": {"type": ["object", "array"]}},
            "required": ["candidates"]},
    },
    {
        "name": "mkt_opportunity",
        "description": "只读需求发现：semantic_candidates 先过语义校验再匹配产品事实；不证明购买意愿。",
        "inputSchema": {"type": "object", "properties": {
            "facts": {"type": "object"}, "signals": {"type": ["object", "array"]}},
            "required": ["facts", "signals"]},
    },
    {
        "name": "mkt_value_artifact",
        "description": "只读价值资产校验与业务渲染：教程/样例/测试/模板/对比/真实案例，action=lint|render。",
        "inputSchema": {"type": "object", "properties": {
            "action": {"enum": ["lint", "render"]}, "artifact": {"type": "object"}},
            "required": ["action", "artifact"]},
    },
    {
        "name": "mkt_journey",
        "description": "只读用户旅程：同 campaign/subject/time_window 过滤，返回漏斗、断点与修复建议。",
        "inputSchema": {"type": "object", "properties": {
            "events": {"type": "array", "items": {"type": "object"}},
            "campaign": {"type": "string"}, "subject": {"type": "string"},
            "time_window": {"type": "object"}, "identity_map": {"type": "object"}},
            "required": ["events", "campaign", "time_window"]},
    },
]


def _text(obj):
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=1)}]}


def _err(msg):
    return {"content": [{"type": "text", "text": str(msg)}], "isError": True}


AGENT_ESCALATION_PARAMS = ("allow_unverified",)


def _agent_escalation_error(args):
    """拒绝代理自行提升权限的参数：这些是 CLI 人工通道专属，schema 不暴露、传入即报错。"""
    for key in AGENT_ESCALATION_PARAMS:
        if args.get(key) is not None:
            return ("%s 不属于 MCP 参数面：UNVERIFIED 渠道首跑校准只能由人工通过 CLI 完成"
                    "（mkt.py --allow-unverified），agent 不得自行解除渠道验证门禁。" % key)
    return None


def _call_tool(name, args):
    if name == "mkt_memory":
        from core.operations_memory import summarize
        return _text(summarize(
            Ledger(DATA / "ledger.jsonl").all(),
            campaign=args.get("campaign"),
            channel=args.get("channel"),
            minimum_confirmed=args.get("minimum_confirmed", 3),
        ))

    if name == "mkt_scorecard":
        from core import scorecard
        return _text(scorecard.build(DATA, campaign=args.get("campaign"), since=args.get("since")))

    if name == "mkt_optimize":
        from core import optimization, scorecard
        from core.operations_memory import summarize
        card = scorecard.build(DATA, campaign=args.get("campaign"))
        memory = summarize(Ledger(DATA / "ledger.jsonl").all(), campaign=args.get("campaign"),
                           channel=args.get("channel"),
                           minimum_confirmed=args.get("minimum_confirmed", 3))
        return _text(optimization.propose(card, memory=memory))

    if name == "mkt_workflow":
        from core import workflows
        action = str(args.get("action") or "").strip()
        workflow_id = args.get("workflow_id")
        if action == "list":
            return _text([
                {"id": row["workflow"]["id"], "version": row["workflow"].get("version"),
                 "channel": row["workflow"].get("channel"), "action": row["workflow"].get("action"),
                 "approval": row["approval"]}
                for row in workflows.list_workflows(DATA)
            ])
        if action in ("show", "lint"):
            if not workflow_id:
                raise ValueError(f"mkt_workflow {action} requires workflow_id")
            workflow = workflows.find_workflow(DATA, workflow_id)
            errors = workflows.lint(workflow)
            if action == "lint":
                return _text({"workflow_id": workflow_id, "errors": errors,
                              "status": "pass" if not errors else "fail"})
            return _text({"workflow": workflow, "approval": workflows.approval(DATA, workflow),
                          "errors": errors})
        if action == "status":
            return _text({"runs": [
                {"run_id": run.get("run_id"), "workflow_id": run.get("workflow_id"),
                 "status": run.get("status"), "stop_reason": run.get("stop_reason")}
                for run in workflows.list_runs(DATA, workflow_id)
            ]})
        raise ValueError("unknown workflow action")

    if name == "mkt_orchestrate":
        from core import orchestration
        plan = orchestration.orchestrate(args["product"], args["goal"], args["task"],
                                         audience=args.get("audience", ""), channel=args.get("channel", ""),
                                         motion=args.get("motion", ""))
        errors = orchestration.lint_orchestration(plan)
        plan["validation"] = {"ok": not errors, "errors": errors}
        plan["rendered_markdown"] = orchestration.render_markdown(plan)
        return _text(plan)

    if name == "mkt_gtm_design":
        from core import gtm_design
        plan = gtm_design.design(
            args["task"],
            object_id=args.get("object") or None,
            objective_id=args.get("objective") or None,
            constraints=args.get("constraints"),
            proposal=args.get("proposal"),
        )
        problems = gtm_design.lint(plan)
        plan["validation"] = {"ok": not problems, "problems": problems}
        return _text(plan)

    if name == "mkt_capabilities":
        from core import capabilities
        if args.get("lint"):
            errors = capabilities.lint_registry()
            return _text({"ok": not errors, "errors": errors})
        if args.get("role_family"):
            return _text(capabilities.get_role_family(args["role_family"]))
        if args.get("capability_id"):
            return _text(capabilities.get_capability(args["capability_id"]))
        return _text({"capabilities": capabilities.list_capabilities(),
                      "role_families": capabilities.list_role_families()})

    if name == "mkt_templates":
        from core import templates
        if args.get("lint_registry"):
            errors = templates.lint_registry()
            return _text({"ok": not errors, "errors": errors})
        if args.get("fields") is not None:
            if not args.get("template_id") or not args.get("version"):
                raise ValueError("模板实例校验需要 template_id/version/fields")
            errors = templates.lint_instance(args["template_id"], args["version"], args["fields"])
            return _text({"ok": not errors, "errors": errors})
        if args.get("template_id"):
            return _text(templates.get_template(args["template_id"]))
        return _text({"templates": templates.list_templates()})

    if name == "mkt_work_order":
        from core.work import WorkStore
        store = WorkStore(DATA)
        if args["action"] == "next":
            return _text({"next": store.next_actions()})
        if args["action"] == "list":
            return _text({"work_orders": store.list()})
        order = store.get(args["work_order_id"])
        from core.sop import SOPRunStore
        from core.deliverables import DeliverableStore
        from core.handoffs import HandoffStore
        work_order_id = order["work_order_id"]
        return _text({
            "work_order": order,
            "sop_runs": [r for r in SOPRunStore(DATA).list() if r["work_order_id"] == work_order_id],
            "deliverables": [d for d in DeliverableStore(DATA).list() if d["work_order_id"] == work_order_id],
            "handoffs": [h for h in HandoffStore(DATA).list() if h["work_order_id"] == work_order_id],
        })

    if name == "mkt_metric_measure":
        from core import metrics
        metric = metrics.MetricRegistry(DATA).get(args["metric_id"], args.get("version") or None)
        return _text(metrics.measure(metric, args["events"], scope=args.get("scope") or {},
                                     cohort_end=args.get("cohort_end", ""), as_of=args.get("as_of", "")))

    if name == "mkt_status":
        return _text(Ledger(DATA / "ledger.jsonl").summary())

    if name == "mkt_channels":
        out = {}
        for ch, info in REGISTRY.items():
            out[ch] = {
                "status": info["status"], "note": info["note"],
                "actions": sorted(info["actions"]),
                "transport_chains": {
                    a: transports.chain(ch, a) for a in sorted(info["actions"])
                },
            }
        return _text(out)

    if name == "mkt_plan_lint":
        plan = json.loads(Path(args["plan_path"]).read_text(encoding="utf-8"))
        errs = lint_plan(plan)
        return _text({"ok": not errs, "errors": errs})

    if name == "mkt_search":
        ch = args["channel"]
        if ch == "x":
            from channels.x.adapter import run as x_run
            code = x_run("search", [args["query"]], go=True)  # search 只读，--go 仅表执行
            logs = sorted((BASE / "channels" / "x" / "vendor" / "logs").glob("reply-candidates-*.txt"),
                          key=lambda p: p.stat().st_mtime)
            if logs:
                lines = logs[-1].read_text(encoding="utf-8").splitlines()[:50]
                return _text({"done": code == 0, "file": str(logs[-1]), "lines": lines})
            return _text({"done": code == 0, "note": "结果落在 vendor logs/（reply-candidates-*.txt）"})
        if ch == "reddit":
            from channels import reddit
            rows = reddit.search(args["query"], limit=args.get("limit", 10),
                                 out=DATA / "mcp-reddit-search.json")
            return _text({"count": len(rows), "out": str(DATA / "mcp-reddit-search.json")})
        if ch == "linkedin":
            from channels import linkedin
            rows = linkedin.search(args["query"], limit=args.get("limit", 10),
                                   out=DATA / "mcp-linkedin-search.json")
            return _text({"count": len(rows), "out": str(DATA / "mcp-linkedin-search.json")})
        if ch == "hn":
            from channels import hn
            rows = hn.search(args["query"], limit=args.get("limit", 10),
                             out=DATA / "mcp-hn-search.json")
            return _text({"count": len(rows), "rows": rows})
        if ch == "github":
            from channels import github
            rows = github.search(args["query"], limit=args.get("limit", 10),
                                 out=DATA / "mcp-github-search.json")
            return _text({"count": len(rows), "rows": rows})

    if name == "mkt_ledger_query":
        led = Ledger(DATA / "ledger.jsonl")
        recs = led.results(channel=args.get("channel"), campaign=args.get("campaign"),
                           action=args.get("action"), target=args.get("target"))
        recs = [r for r in recs if not args.get("result") or r.get("result") == args.get("result")]
        limit = args.get("limit", 50)
        return _text({"count": len(recs), "records": recs[:limit]})

    if name == "mkt_campaign":
        from core import campaign as camp
        cid = args["campaign"]
        report = camp.collect(DATA, Ledger(DATA / "ledger.jsonl"), cid)
        report["next"] = camp.next_step(DATA, Ledger(DATA / "ledger.jsonl"), cid)
        return _text(report)

    if name == "mkt_run_queue":
        from core.queue_runner import QueueRunError, run_queue
        escalation = _agent_escalation_error(args)
        if escalation:
            return _err(escalation)
        try:
            results = run_queue(args["queue_path"], data_dir=DATA,
                                go=bool(args.get("go")),
                                routine=bool(args.get("routine")), account=args.get("account"))
        except QueueRunError as exc:
            return _err(f"队列拒跑: {exc}")
        tally = {}
        for r in results:
            tally[r.get("result", "?")] = tally.get(r.get("result", "?"), 0) + 1
        return _text({
            "queue": args["queue_path"],
            "dry_run": not args.get("go"),
            "summary": tally,
            "records": [{"id": r.get("ref") or r.get("target"),
                         "target": r.get("target"), "result": r.get("result")}
                        for r in results],
        })

    if name == "mkt_action":
        from core.action_runner import ActionError, run_action
        escalation = _agent_escalation_error(args)
        if escalation:
            return _err(escalation)
        try:
            rec = run_action(
                channel=args["channel"], action=args["action"], data_dir=DATA,
                campaign=args.get("campaign", ""), go=bool(args.get("go")),
                target=args.get("target"), text=args.get("text"),
                title=args.get("title"), url=args.get("url"),
                subreddit=args.get("subreddit"), number=args.get("number"),
                extra=args.get("extra"), routine=bool(args.get("routine")),
                account=args.get("account", ""))
        except ActionError as exc:
            return _err(f"动作拒跑: {exc}")
        return _text(rec)

    if name == "mkt_inbox":
        ch = args["channel"]
        if ch == "hn":
            from channels import hn
            return _text({"rows": hn.inbox()})
        if ch == "github":
            if not args.get("repo"):
                return _err("github inbox 需要 repo")
            from channels import github
            return _text({"rows": github.inbox(args["repo"])})
        if ch == "email":
            from channels import email_cdp
            rows = email_cdp.scan()
            return _text({"rows": rows if isinstance(rows, list) else [],
                          "note": "结果同时打印在服务端"})
        if ch == "x":
            from channels.x.adapter import run as x_run
            code = x_run("inbox", [], go=True)
            return _text({"done": code == 0, "note": "vendor 扫描结果见服务端日志"})

    if name == "mkt_audit":
        import contextlib
        import io
        from channels import github
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            github.audit(args["repo"])
        return _text({"repo": args["repo"], "markdown": buf.getvalue()})

    if name == "mkt_retro_scan":
        from core import logscan
        data = logscan.scan_memory(args["path"], max_files=args.get("max_files", 20000))
        return _text({"inventory": data["inventory"], "claims": len(data["claims"])})

    if name == "mkt_retro_analyze":
        from core import logscan, retro
        data = logscan.scan_memory(args["path"], max_files=args.get("max_files", 20000))
        facts = args.get("facts")
        rows = retro.rates_from_facts(facts) if facts else []
        analysis = {"channels": rows or [],
                    "waste": retro.waste(rows) if rows else [],
                    "conflicts": retro.conflicts(retro.consolidate(data["claims"]),
                                                 only_channels=list(facts) if facts else None),
                    "timeline": retro.timeline(data["claims"])}
        pool = retro.evidence_pool(data["claims"])
        return _text({"analysis": analysis,
                      "evidence": [{"channel": r["name"],
                                    "lines": [i["sentence"][:140] for i in r["items"][:3]]}
                                   for r in pool[:6]]})

    if name == "mkt_goals":
        from core import goals
        try:
            return _text(goals.list_goals(args["facts"]))
        except goals.GoalError as e:
            return _err(str(e))

    if name == "mkt_channel_venues":
        from core import channels
        rows = channels.VENUES
        if args.get("platform"):
            rows = [r for r in rows if r["platform"] == args["platform"]]
        return _text(rows)

    if name == "mkt_channel_plan":
        from core import channels, goals
        try:
            gp = goals.build_plan(
                args["facts"], args["goal"],
                platforms=args.get("platforms"),
                secondary_goal_id=args.get("secondary"))
            return _text(channels.plan(args["facts"], gp))
        except (goals.GoalError, ValueError) as e:
            return _err(str(e))

    if name == "mkt_deals":
        from core import conversion
        d = conversion.Deals(DATA / "deals.jsonl")
        rows = d.all()
        if args.get("stage"):
            rows = [r for r in rows if r["stage"] == args["stage"]]
        return _text({"deals": rows, "funnel": d.funnel()})

    if name == "mkt_deal_next":
        from core import conversion
        d = conversion.Deals(DATA / "deals.jsonl")
        on = args.get("on") or conversion._today()
        rows = []
        for row in d.all():
            if row["stage"] in ("lost", "repeat"):
                continue
            days = (conversion.date.fromisoformat(on)
                    - conversion.date.fromisoformat(row["updated"])).days
            nxt = conversion.next_action(row["stage"], days)
            if nxt["action"] == "等":
                continue
            item = dict(row)
            item["next"] = nxt
            rows.append(item)
        return _text({"date": on, "due": rows})

    if name == "mkt_sprint_plan":
        from core import goals, sprint
        try:
            gp = goals.build_plan(
                args["facts"], args["goal"],
                platforms=args.get("platforms"),
                target=args.get("target"),
                secondary_goal_id=args.get("secondary"))
            plan = sprint.build(
                gp, days=args.get("days", 3), start=args.get("start"),
                hours_per_day=args.get("hours_per_day", 8.0),
                launch_hour=args.get("launch_hour", "21:00"))
            return _text(plan)
        except (goals.GoalError, sprint.SprintError) as e:
            return _err(str(e))

    if name == "mkt_goal_plan":
        from core import goals
        try:
            return _text(goals.build_plan(
                args["facts"], args["goal"],
                platforms=args.get("platforms"),
                target=args.get("target"),
                window_days=args.get("window_days"),
                secondary_goal_id=args.get("secondary"),
                secondary_target=args.get("secondary_target")))
        except goals.GoalError as e:
            return _err(str(e))

    if name == "mkt_strategy":
        from core import gtm, strategy, retro
        payload = gtm.diagnose(args["facts"])
        analysis = None
        if args.get("retro_facts"):
            rows = retro.rates_from_facts(args["retro_facts"])
            analysis = {"channels": rows, "waste": retro.waste(rows)}
        st = strategy.build(args["facts"]["product"], focus=payload["focus"],
                            analysis=analysis)
        return _text({"strategy": st, "rendered": strategy.render(st)})

    if name == "mkt_cadence":
        from core import gtm, strategy, cadence
        payload = gtm.diagnose(args["facts"])
        st = strategy.build(args["facts"]["product"], focus=payload["focus"], analysis=None)
        plan = cadence.build(st, start=args["start"],
                             actions_per_week=args.get("actions_per_week", 20))
        return _text({"plan": plan, "rendered": cadence.render(plan)})

    if name == "mkt_result":
        from core import result
        from core.followups import Followups
        from core.task_outcome import read_records
        r = result.collect(ledger_entries=Ledger(DATA / "ledger.jsonl").all(),
                           followups=Followups(DATA / "followups.jsonl"),
                           criteria=args.get("criteria") or [],
                           task_outcome_records=read_records(DATA))
        return _text({"result": r, "rendered": result.render(r)})

    if name == "mkt_revise":
        from core import result, revise
        from core.followups import Followups
        from core.task_outcome import read_records
        r = args.get("result") or result.collect(
            ledger_entries=Ledger(DATA / "ledger.jsonl").all(),
            followups=Followups(DATA / "followups.jsonl"),
            task_outcome_records=read_records(DATA))
        diff = revise.propose(r, args.get("params") or {})
        return _text({"diff": diff, "rendered": revise.render(diff)})

    if name == "mkt_followup_due":
        from core.followups import Followups, describe
        fu = Followups(DATA / "followups.jsonl")
        on = args.get("on")
        return _text({"date": on, "due": [describe(r) for r in fu.due(on)],
                      "overdue": [describe(r) for r in fu.overdue(on)],
                      "stats": fu.stats(on)})

    if name == "mkt_progress":
        from core import progress
        from core.followups import Followups
        fu = Followups(DATA / "followups.jsonl")
        if args.get("scope", "weekly") == "daily":
            d = progress.daily(fu, on=args.get("on"))
            return _text({"daily": d, "rendered": progress.render_daily(d)})
        w = progress.weekly(fu, on=args.get("on"))
        return _text({"weekly": w, "rendered": progress.render_weekly(w)})

    if name == "mkt_voice":
        from core import voice
        d = voice.draft(args["platform"], hook=args.get("hook", ""), what=args.get("what", ""))
        return _text({"profile": voice.profile(args["platform"]), "drafts": d["drafts"],
                      "rendered": voice.render(args["platform"])})

    if name == "mkt_voice_lint":
        from core import voice
        problems = voice.lint(args["platform"], args.get("text", ""))
        return _text({"ok": not problems, "problems": problems})

    if name == "mkt_experience":
        from core import experience
        if hasattr(experience, "eligible"):
            rows = experience.eligible(args["platform"])
        else:
            rows = experience.graded(args["platform"])["rule"]
        return _text({"eligible": rows, "freshness": experience.freshness(args["platform"]),
                      "rendered": experience.render(args["platform"])})

    if name == "mkt_signal_interpretation":
        from core import signal_interpretation
        return _text(signal_interpretation.interpret_many(args["candidates"]))

    if name == "mkt_opportunity":
        from core import opportunity
        return _text(opportunity.discover(args["facts"], args["signals"]))

    if name == "mkt_value_artifact":
        from core import value_artifact
        if args["action"] == "lint":
            errors = value_artifact.lint(args["artifact"])
            return _text({"ok": not errors, "errors": errors})
        return _text({"rendered": value_artifact.render(args["artifact"])})

    if name == "mkt_journey":
        from core import journey
        return _text(journey.diagnose(
            args["events"], campaign=args["campaign"],
            subject=args.get("subject") or None, time_window=args["time_window"],
            identity_map=args.get("identity_map")))

    if name == "mkt_playbooks":
        from core import playbooks
        return _text(playbooks.list_playbooks())

    return _err(f"未知工具: {name}")


def call_tool(name, args):
    """所有工具输出走 stderr，stdout 只留给 JSON-RPC（stdio 协议不可污染）。"""
    import contextlib
    import sys
    with contextlib.redirect_stdout(sys.stderr):
        return _call_tool(name, args)


def _tally(records):
    out = {}
    for r in records:
        out[r["result"]] = out.get(r["result"], 0) + 1
    return out


def handle(msg):
    method = msg.get("method", "")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mkt-server", "version": "0.11.0"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            res = call_tool(msg["params"]["name"], msg["params"].get("arguments", {}))
            return {"jsonrpc": "2.0", "id": mid, "result": res}
        except PlanNotApproved as exc:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": _err(f"人审门禁: {exc}")}
        except (Exception, SystemExit) as exc:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": _err(f"{type(exc).__name__}: {exc}")}
    if mid is not None:  # 未知请求；notification 忽略
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
