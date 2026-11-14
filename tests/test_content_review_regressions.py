"""Regression cases observed during the DSH editorial handoff."""
import unittest
from collections import Counter
from core import content_ops


class ContentReviewRegressions(unittest.TestCase):
    def test_single_document_failure_reaches_summary(self):
        result = content_ops.audit_file('xiaohongshu', '# 标题\n\n这篇只回答一件事：测试。')
        self.assertFalse(result['ok'])
        self.assertFalse(result['documents'][0]['ok'])
        self.assertIn('有阻断项', content_ops.render_audit(result))

    def test_weekly_schedule_includes_all_three_mechanisms(self):
        rows = [r for r in content_ops.plan(days=14)['rows'] if r['channel'] == 'xiaohongshu']
        for start in (0, 14):
            self.assertEqual(Counter(r['cta'] for r in rows[start:start+14]), {'A': 4, 'B': 6, 'C': 4})

    def test_repeated_body_is_flagged_even_with_different_titles(self):
        body = '改价格时，画面字幕和声音需要逐项核对。不要用一份成片替代工程。记录输入输出和失败原因。' * 4
        result = content_ops.audit_file('xiaohongshu', '## D2-01｜标题甲\n'+body+'\n\n## D2-02｜标题乙\n'+body)
        self.assertEqual(result['batch_warnings'][0]['ids'], ['D2-01','D2-02'])
        self.assertIn('similar_posts', content_ops.render_audit(result))

    def test_common_cta_does_not_make_distinct_articles_duplicates(self):
        a = '硬件灯光语音设备动作功能参数错误对象温度亮度' * 10
        b = '商品视频价格字幕配音横屏竖屏镜头工程渲染颜色' * 10
        cta = '\n\n关注并私信我，我发完整方法。' * 20
        result = content_ops.audit_file('xiaohongshu','## D2-01｜硬件\n'+a+cta+'\n\n## D2-02｜视频\n'+b+cta)
        self.assertFalse(result['batch_warnings'])

    def test_duplicate_ids_reported(self):
        result = content_ops.audit_file('blog', '## D2-01｜甲\n模板\n\n## D2-01｜乙\n仓库')
        self.assertEqual(result['batch_warnings'][0]['code'],'duplicate_id')


if __name__ == '__main__':
    unittest.main()


class OrdinalReviewTests(unittest.TestCase):
    def test_ordinal_is_not_superiority_claim(self):
        for phrase in ('第一句', '第一组', '第一轮'):
            result = content_ops.audit_text('blog', '# 标题\n'+phrase+'先核实来源。')
            self.assertNotIn('absolute_soft', [w['code'] for w in result['warnings']])
    def test_unqualified_superiority_still_warns(self):
        result = content_ops.audit_text('blog', '# 标题\n这是行业第一的产品。')
        self.assertIn('absolute_soft', [w['code'] for w in result['warnings']])
