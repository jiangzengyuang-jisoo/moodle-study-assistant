"""Deterministic bilingual query expansion; locators, never learning judgments."""
import re
import unicodedata

# Related terminology only locates candidates. It never establishes equivalence
# of mathematical definitions, assessment scope, or a learner's mastery.
ALIASES = (
    ('FOB', 'free on board', '离岸价'),
    ('FOB shipping point', '装运点交货', '起运点交货'),
    ('FOB destination', '目的地交货'),
    ('FIFO', 'first in first out', '先进先出'),
    ('LIFO', 'last in first out', '后进先出'),
    ('gross profit margin', '毛利率'), ('gross profit', '毛利'),
    ('cost of goods sold', 'COGS', '销售成本'),
    ('bank reconciliation', '银行调节', '银行对账', '银行余额调节表'),
    ('accounts receivable', '应收账款'), ('bad debt', '坏账'),
    ('unearned revenue', '预收款', '预收收入'), ('prepaid', '预付'),
    ('adjusting entries', '调整分录'), ('closing entries', '结账分录'),
    ('perpetual inventory', '永续盘存'), ('periodic inventory', '定期盘存'),
    ('implicit second derivative', '隐函数二阶导数'),
    ('implicit differentiation', '隐函数求导'),
    ('slant asymptote', 'oblique asymptote', '斜渐近线'),
    ('critical point', 'critical number', '临界点'),
    ('linear approximation', 'linearization', '线性近似'),
    ('equivalent infinitesimal', '等价无穷小'),
    ('mean value theorem', 'MVT', '拉格朗日中值定理'),
    ('Taylor remainder', '泰勒余项', 'Taylor余项'),
    ('Taylor', '泰勒'), ('chain rule', '链式法则'),
    ('product rule', '乘积法则'), ('inverse function', '反函数'),
    ('complex numbers', 'complex number', '复数'),
    ('nth roots', 'n-th roots', '复数方根'),
    ('squeeze theorem', 'sandwich theorem', '夹逼定理'),
    ('comparative statics', '比较静态'),
    ('opportunity cost', '机会成本'), ('comparative advantage', '比较优势'),
    ('price ceiling', '价格上限'), ('price floor', '价格下限'),
    ('consumer surplus', '消费者剩余'), ('producer surplus', '生产者剩余'),
    ('gross domestic product', 'GDP', '国内生产总值'),
    ('inflation', '通货膨胀'), ('unemployment', '失业'),
    ('group submission', '小组提交', '代表全组提交'),
)
STOP = set('the a an and or is was were have has i we you my me did do does before about '
           'what why how please explain recall remember learned discussed'.split())


def norm(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value).casefold().replace('_', ' ')).strip()


def contains(text, term):
    # Latin abbreviations must not match substrings such as FOBish or metadata IDs.
    if re.fullmatch(r'[a-z0-9][a-z0-9 -]*', term):
        return bool(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text))
    return term in text


def plan(query, custom=()):
    if not isinstance(custom, (list, tuple)) or len(custom) > 50:
        raise ValueError('memory_aliases must be a list of at most 50 alias groups')
    for group in custom:
        if not isinstance(group, (list, tuple)) or not 2 <= len(group) <= 8 or any(
                not isinstance(term, str) or not term.strip() or len(term) > 80 for term in group):
            raise ValueError('Each memory alias group needs 2-8 nonempty terms, each at most 80 characters')
    query = norm(query)
    groups = [tuple(norm(t) for t in group) for group in (*ALIASES, *custom)]
    triggers = {term for group in groups for term in group if contains(query, term)}
    specific = {term for term in triggers if not any(term != other and term in other for other in triggers)}
    aliases = list(dict.fromkeys(term for group in groups if specific.intersection(group) for term in group))
    words = [word for word in re.findall(r'[a-z0-9]+|[\u3400-\u9fff]+', query)
             if len(word) > 1 and word not in STOP]
    # A recognized phrase should not also retrieve every record containing one
    # generic word (e.g. "value" from "mean value theorem").
    if aliases:
        words = []
    return {'phrase': query, 'aliases': aliases, 'words': list(dict.fromkeys(words)),
            'mode': 'bilingual_aliases' if aliases else 'literal_terms'}


def match(texts, query):
    texts = [norm(text) for text in texts]
    if any(contains(text, query['phrase']) for text in texts):
        return 100, 'query_phrase'
    if any(contains(text, term) for text in texts for term in query['aliases']):
        return 80, 'alias'
    hits = sum(any(contains(text, word) for text in texts) for word in query['words'])
    return (20 + min(hits, 10), 'query_terms') if hits else (0, None)
