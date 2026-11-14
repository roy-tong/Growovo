"""Read-only whole-article review protocol; validates records, not semantic truth.

A regex audit cannot judge narrative flow or infer causality. This module gives
an editor/agent a source-bound review packet and checks that the returned review
covers the whole article, cites actual passages, and doesn't reuse stale work.
"""
from __future__ import annotations
import hashlib
import re
from typing import Any, Dict

VERSION = '1.0'
CRITERIA = {
    'reader_promise': '读者是谁、要解决什么；标题和摘要的承诺是否由正文兑现，噱头有没有事实支撑？',
    'argument_flow': '逐段写出作用；转话题、品类或时间时，是否先交代共同问题与转场理由？',
    'information_gain': '每节消除了哪个不同疑问、改变哪个决定；是否只是换说法重复同一结论？',
    'evidence_inference': '分清提及、意向、行为、持续使用；分母、标签、时间与人群是否一致，推论有没有越级？',
    'task_mechanism': '谁在什么场景，用什么替代方案；痛点如何影响任务、功能与产品取舍？通用产品要看有边界的任务组合。',
    'context_hook': '热点具体改变了哪个前提？融资/发布/连接不等于需求/效果/本地运行；无因果关系就删除。',
    'product_entry': '产品为何出场、是什么、作者关系、实际入口与能力边界是否清楚；免费也能用真实输入端到端完成？',
    'reader_language': '普通目标读者能否顺着读；术语先解释；内部编码、采集质量门和运行日志是否挤占正文？',
    'channel_and_ending': '小红书聚焦一个任务；公众号用故事推进判断；博客也面向读者，复现放附录；结尾给具体决定或行动。',
    'version_and_reuse': '是否基于最新源稿；已发布/发布中稿件是否保护；同题是否新增场景、证据、反例或阶段？',
}

def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def packet(channel: str, text: str, title: str = '') -> Dict[str, Any]:
    if channel not in ('xiaohongshu', 'wechat', 'blog'):
        raise ValueError('未知渠道')
    if not text.strip():
        raise ValueError('正文为空，无法创建整篇审稿记录')
    sections = re.split(r'(?m)(?=^#{1,3} )', text)
    return {
        'schema_version': VERSION, 'channel': channel, 'title': title,
        'source_sha256': digest(text),
        'purpose': '逐篇整读后填写审稿记录；空模板不代表完成审稿。',
        'outline': [{'opening': s.strip().split('\n')[0][:100], 'role': ''}
                    for s in sections if s.strip()],
        'checks': [{'id': cid, 'question': question, 'verdict': 'pending',
                    'quote': '', 'reason': '', 'revision': ''}
                   for cid, question in CRITERIA.items()],
        'claims': [],
        'claim_schema': {
            'quote': '正文原句', 'kind': 'descriptive|conversion|trend|causal|hypothesis',
            'source': '实际证据文件或URL', 'inference': '证据到结论的推导与替代解释',
            'numerator_population': '转化分子人群', 'denominator_population': '转化分母人群',
            'linked_cohort': '转化是否有同一批人前后关联，布尔值',
            'comparable_windows': '趋势是否同口径时间窗口，布尔值',
            'causal_design': '因果识别或实验设计，没有就降为假设',
        },
        'claim_coverage_note': '',
        'limits': '只校验填写完整性、版本与声明的一致性；不独立验证事实、不预测涨粉、不授权发布。',
    }


def validate(channel: str, text: str, record: Dict[str, Any]) -> Dict[str, Any]:
    expected = packet(channel, text)
    errors = []
    def fail(code, detail): errors.append({'code': code, 'message': detail})
    if record.get('source_sha256') != expected['source_sha256']:
        fail('stale_review', '正文已变化或未绑定版本；必须整篇重新审查，不能沿用旧通过。')
    if record.get('channel') != channel:
        fail('wrong_channel', '审稿渠道与源稿不一致。')
    checks = record.get('checks', [])
    if not isinstance(checks, list): checks = []
    ids = [c.get('id') for c in checks if isinstance(c, dict)]
    if len(ids) != len(set(ids)):
        fail('duplicate_check', '审稿维度重复。')
    for cid in CRITERIA:
        c = next((x for x in checks if isinstance(x, dict) and x.get('id') == cid), {})
        verdict = c.get('verdict')
        if verdict not in ('pass', 'revise', 'not_applicable'):
            fail('review_incomplete', cid + ' 尚未完成。'); continue
        if not str(c.get('reason', '')).strip():
            fail('missing_reason', cid + ' 需要解释判断理由。')
        quote = c.get('quote', '')
        if verdict != 'not_applicable' and (not isinstance(quote, str) or not quote or quote not in text):
            fail('untraceable_quote', cid + ' 必须引用当前正文中的片段。')
        if verdict == 'revise':
            fail('revision_open', cid + ': ' + str(c.get('revision') or '尚无修改方案'))
    outline = record.get('outline', [])
    if not isinstance(outline, list) or len(outline) != len(expected['outline']):
        fail('outline_coverage', '须逐节解释整篇的推进作用。')
    else:
        for actual, target in zip(outline, expected['outline']):
            if not isinstance(actual, dict) or actual.get('opening') != target['opening'] or not actual.get('role', '').strip():
                fail('outline_coverage', '段落顺序或作用记录不完整。'); break
    if not str(record.get('claim_coverage_note', '')).strip():
        fail('claims_unreviewed', '需说明检查了哪些关键断言；无定量/事实断言也要说明原因。')
    claims = record.get('claims', [])
    if not isinstance(claims, list):
        fail('invalid_claims', 'claims 必须为列表。'); claims = []
    for c in claims:
        if not isinstance(c, dict): fail('invalid_claim', '断言必须为对象。'); continue
        if not c.get('quote') or c['quote'] not in text:
            fail('claim_quote', '关键断言无法定位到当前正文。')
        kind = c.get('kind')
        if kind not in ('descriptive', 'conversion', 'trend', 'causal', 'hypothesis'):
            fail('claim_kind', '断言类型无效。')
        if not c.get('inference') or (kind != 'hypothesis' and not c.get('source')):
            fail('claim_evidence', '事实断言需来源和推导；假设需解释验证方式。')
        if kind == 'conversion' and (c.get('linked_cohort') is not True or
            not c.get('numerator_population') or c.get('numerator_population') != c.get('denominator_population')):
            fail('cross_cohort_conversion', '不同记录/人群不能拼成前后转化率。')
        if kind == 'trend' and c.get('comparable_windows') is not True:
            fail('incomparable_trend', '趋势需可比时间窗口及采样口径。')
        if kind == 'causal' and not c.get('causal_design'):
            fail('unsupported_causality', '相关、融资或发布不能自动证明因果；补设计或降为假设。')
    return {'ok': not errors, 'record_complete': not errors,
            'semantic_truth_verified': False, 'publish_authorized': False,
            'source_sha256': expected['source_sha256'], 'errors': errors,
            'note': '完整记录不等于事实已独立核实；审稿者仍需对推理与来源负责。'}
