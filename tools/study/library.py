"""Local, rebuildable course source library. Never writes learning state or originals."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / 'Study/_System/state.json'
SCHEMA = 1

# Search vocabulary is a locator, never authority for an answer or exam scope.
ALIASES = [
    ['反函数', 'inverse function', 'inverse functions', '单射', 'one-to-one'],
    ['反三角', '反余弦', '反正弦', '选支', 'arccos', 'arcsin', 'inverse trigonometric'],
    ['定义域', 'domain'], ['值域', 'range'], ['复合函数', 'composite function', 'composition'],
    ['复数', 'complex number', 'complex numbers'], ['极形式', 'polar form'],
    ['复数方根', 'nth roots', 'n-th roots', 'nth root', 'n-th root', 'roots of complex', 'roots of unity'],
    ['极限', 'limit', 'limits'], ['夹逼', 'squeeze', 'sandwich'],
    ['连续', 'continuity', 'continuous'], ['介值', 'intermediate value'],
    ['最值', '极值定理', 'extreme value'], ['二分法', 'bisection'],
    ['导数', 'derivative', 'differentiation'], ['积分', 'integral', 'integration'],
    ['银行调节', '银行对账', 'bank reconciliation'], ['坏账', 'bad debt', 'uncollectible'],
    ['应收账款', 'accounts receivable'], ['备抵', 'allowance'],
    ['预收', 'unearned revenue'], ['预付', 'prepaid'], ['应计', 'accrual', 'accrued'],
    ['调整分录', 'adjusting entries'], ['结账', 'closing entries'],
    ['供给', 'supply'], ['需求', 'demand'], ['弹性', 'elasticity'],
    ['机会成本', 'opportunity cost'], ['比较优势', 'comparative advantage'],
    ['消费者剩余', 'consumer surplus'], ['生产者剩余', 'producer surplus'],
    ['国内生产总值', 'gdp', 'gross domestic product'], ['通货膨胀', 'inflation'],
    ['失业', 'unemployment'], ['乘数', 'multiplier'], ['货币', 'money', 'monetary'],
]
STOP = {'the','and','for','what','why','how','this','that','with','from','explain','please','about',
        '给我','为什么','怎么','如何','讲解','复习','这个','一下','什么','请问','学习','课程'}


def now():
    # Provenance timestamps are UTC; course schedules retain state.timezone.
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        read_json(name)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_source(study, relative):
    path = (study / relative).resolve()
    if not path.is_relative_to(study.resolve()):
        raise ValueError('Material path escapes the study directory')
    parts = [p.casefold() for p in path.relative_to(study.resolve()).parts]
    if any(p in {'_system','library','backups','.git','.tools'} for p in parts) or path.suffix.casefold() in {'.bak','.tmp'}:
        raise ValueError('Derived files and backups cannot be used as original materials')
    return path


@contextlib.contextmanager
def build_lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/'.build.lock').open('a+b') as f:
        if f.tell() == 0:
            f.write(b'0'); f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


class Library:
    def __init__(self, state_path=DEFAULT_STATE, cache_dir=None):
        self.state_path = Path(state_path).resolve()
        self.study = self.state_path.parent.parent
        self.folder = Path(cache_dir).resolve() if cache_dir else self.state_path.parent/'Library'
        expected = self.state_path.parent/'Library'
        if expected.resolve() != expected or self.folder.resolve() != self.folder or not self.folder.is_relative_to(expected):
            raise ValueError('Cache directory must be inside the dedicated _System/Library directory')

    def cache_path(self, relative):
        declared = self.folder/relative
        path = declared.resolve()
        if path != declared or not path.is_relative_to(self.folder/'cache') or path.suffix != '.json':
            raise ValueError('Invalid cache path; rebuild the catalog')
        return path

    def state(self):
        return read_json(self.state_path)

    def catalog(self):
        p = self.folder/'catalog.json'
        return read_json(p) if p.exists() else {'schema':SCHEMA,'materials':[],'state_revision':None}

    def sync(self, course=None, force=False):
        from learning_library.extract import EXTRACTOR_VERSION, extract_document
        start = time.perf_counter()
        state = self.state()
        rows = state.get('materials', [])
        if len({m['id'] for m in rows}) != len(rows):
            raise ValueError('Duplicate material IDs in state; library not rebuilt')
        if course and course not in {c['id'] for c in state.get('courses', [])}:
            raise ValueError('Unknown course: '+course)
        extracted = reused = 0
        with build_lock(self.folder):
            previous = {m['id']:m for m in self.catalog()['materials']}
            trusted_cache = {m['cache']:m['cache_sha256'] for m in previous.values()
                             if m.get('cache') and m.get('cache_sha256')}
            result = []
            for m in rows:
                if course and m.get('course_id') != course:
                    if m['id'] in previous:
                        result.append(previous[m['id']])
                    continue
                row = {k:m.get(k) for k in ['id','course_id','title','filename','relative_path','kind','category','page_map','contains_solutions']}
                row.update(registry_sha256=m.get('sha256'), indexed_at=now(), warnings=[], status='pending')
                try:
                    source = safe_source(self.study, m['relative_path'])
                    if not source.is_file():
                        row['status'] = 'missing_source'; result.append(row); continue
                    sha = digest(source)
                    row.update(sha256=sha, bytes=source.stat().st_size)
                    if m.get('sha256') and sha != m['sha256']:
                        row['warnings'].append('source_differs_from_registered_hash_recheck_source_mapping')
                    key = hashlib.sha256((EXTRACTOR_VERSION+'\0'+sha+'\0'+source.suffix.lower()).encode()).hexdigest()
                    cache = self.cache_path(str(Path('cache')/f'{key}.json'))
                    data = None
                    if cache.exists() and not force:
                        try:
                            candidate = read_json(cache)
                            if candidate.get('source_sha256') == sha and candidate.get('extractor_version') == EXTRACTOR_VERSION:
                                if digest(cache) == trusted_cache.get(str(cache.relative_to(self.folder))):
                                    data = candidate
                        except (ValueError, OSError):
                            pass
                    if data is None:
                        data = extract_document(source)
                        if digest(source) != sha:
                            row['status'] = 'source_changed_during_extraction'; result.append(row); continue
                        data['source_sha256'] = sha
                        atomic_json(cache, data)
                        extracted += 1
                    else:
                        reused += 1
                    row.update(cache=str(cache.relative_to(self.folder)), cache_sha256=digest(cache),
                               extractor_version=EXTRACTOR_VERSION, unit_kind=data['unit_kind'],
                               units=len(data['pages']), text_units=sum(bool(p.get('text','').strip()) for p in data['pages']))
                    trusted_cache[row['cache']] = row['cache_sha256']
                    row['warnings'] += data.get('warnings',[])
                    row['status'] = 'indexed' if data['pages'] else 'not_text_indexed'
                    row['visual_review_required'] = True
                except Exception as exc:
                    row['status'] = 'extraction_error'
                    row['warnings'].append(type(exc).__name__+': '+str(exc)[:240])
                result.append(row)
            catalog = {'schema':SCHEMA,'state_revision':state.get('revision'),'built_at':now(),
                       'extractor_version':EXTRACTOR_VERSION,'materials':result,
                       'quality_policy':'Extracted text and headings are navigation aids, not verified teaching evidence.'}
            atomic_json(self.folder/'catalog.json',catalog)
            report = self.summary(catalog)
            report.update(extracted=extracted,reused=reused,elapsed_seconds=round(time.perf_counter()-start,3))
            atomic_json(self.folder/'last-build.json',report)
        return report

    def summary(self, catalog=None):
        catalog = catalog or self.catalog()
        rows = catalog['materials']
        return {'state_revision':catalog.get('state_revision'),'materials':len(rows),
                'units':sum(m.get('units',0) for m in rows),'text_units':sum(m.get('text_units',0) for m in rows),
                'status_counts':{s:sum(m['status']==s for m in rows) for s in sorted({m['status'] for m in rows})},
                'attention':[{'id':m['id'],'status':m['status'], 'units':m.get('units',0),
                              'text_units':m.get('text_units',0),
                              'warnings':[w for w in m.get('warnings',[]) if w!='text_extraction_is_not_source_verification']}
                             for m in rows if m['status']!='indexed' or m.get('text_units',0)<m.get('units',0)
                             or any(w!='text_extraction_is_not_source_verification' for w in m.get('warnings',[]))],
                'mastery_updated':False,'state_written':False}

    def fresh_rows(self, course=None, mid=None):
        state = self.state()
        registered = {m['id']:m for m in state.get('materials',[])}
        catalog = self.catalog()
        from learning_library.extract import EXTRACTOR_VERSION
        good, issues = [], []
        indexed_ids = set()
        for row in catalog['materials']:
            material = registered.get(row['id'])
            if not material or (course and material.get('course_id') != course) or (mid and row['id']!=mid):
                continue
            indexed_ids.add(row['id'])
            reason = None
            try:
                source = safe_source(self.study,material['relative_path'])
                if row.get('relative_path') != material.get('relative_path') or row.get('course_id') != material.get('course_id'):
                    reason = 'registry_mapping_changed'
                elif not source.is_file(): reason = 'missing_source'
                elif digest(source) != row.get('sha256'): reason = 'source_changed_rebuild_required'
                elif row.get('extractor_version') != EXTRACTOR_VERSION: reason = 'extractor_changed_rebuild_required'
                elif not row.get('cache'): reason = row.get('status','not_indexed')
                elif not self.cache_path(row['cache']).is_file(): reason = 'cache_missing'
                elif digest(self.cache_path(row['cache'])) != row.get('cache_sha256'): reason = 'cache_corrupt_rebuild_required'
            except (OSError, ValueError) as exc:
                reason = 'unavailable:'+type(exc).__name__
            if reason:
                issues.append({'id':row['id'],'reason':reason})
            else:
                # Semantic labels always come from the latest state, not an old catalog.
                current = dict(row)
                for key in ['title','filename','page_map','contains_solutions','kind','category','visually_reviewed_pages','inspection_scope']:
                    current[key] = material.get(key)
                current['registry_sha256'] = material.get('sha256')
                good.append(current)
        for material_id,m in registered.items():
            if material_id not in indexed_ids and (not course or m.get('course_id')==course) and (not mid or material_id==mid):
                issues.append({'id':material_id,'reason':'new_material_not_indexed'})
        return state, good, issues

    def data(self,row):
        path = self.cache_path(row['cache'])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['cache_sha256']:
            raise ValueError('Cache changed during read; retry or rebuild')
        data = json.loads(raw.decode('utf-8-sig'))
        if data.get('source_sha256') != row['sha256'] or data.get('extractor_version') != row['extractor_version']:
            raise ValueError('Cache provenance mismatch; rebuild')
        if digest(safe_source(self.study,row['relative_path'])) != row['sha256']:
            raise ValueError('Source changed during read; rebuild')
        return data

    def ocr_candidates(self,row,page):
        if page.get('text','').strip() or row.get('unit_kind') not in {'pdf_page','image'}:
            return []
        from learning_library.ocr import load_ocr
        return [candidate for language in ('en-US','zh-Hans-CN')
                if (candidate := load_ocr(self,row,page['page'],language)) is not None]

    def search(self, query, course=None, limit=8, include_solutions=False):
        state, rows, issues = self.fresh_rows(course)
        terms = query_terms(query)
        original_terms = [t for t in re.findall(r'[a-z0-9]+(?:[-\'][a-z0-9]+)*|[\u3400-\u9fff]+',norm(query))
                          if len(t)>1 and t not in STOP]
        if not terms: raise ValueError('Supply a topic, term, or question number')
        found, excluded = [], []
        for row in rows:
            if is_solution(row) and not include_solutions:
                excluded.append(row['id']); continue
            data = self.data(row)
            title = norm(str(row.get('title') or row.get('filename') or ''))
            for page in data['pages']:
                text = page.get('text','')
                notes = page.get('speaker_notes','')
                ocr = self.ocr_candidates(row,page)
                ocr_text = '\n'.join(c.get('text','') for c in ocr)
                heading = page.get('heading','')
                topics = page_topics(row,page['page']) if row['unit_kind'] in ('pdf_page','slide') else []
                candidate_text='\n'.join([text,notes,ocr_text])
                body = norm(candidate_text); labels = norm(heading+' '+' '.join(topics))
                matches = [t for t in terms if occurs(body,t) or occurs(labels,t) or occurs(title,t)]
                if not matches: continue
                score = sum((5 if occurs(labels,t) else 0)+(1 if occurs(title,t) else 0)+
                            min(3,count_term(body,t)) for t in matches)
                # A title-only hit can locate the file, but is not evidence for a specific page.
                body_matches = [t for t in matches if occurs(body,t) or occurs(labels,t)]
                if not body_matches: score *= .2
                original_hits=sum(occurs(body,t) or occurs(labels,t) for t in original_terms)
                score += 8*original_hits
                if len(original_terms)>1 and original_hits==len(original_terms): score += 20
                if 'assistant-authored' in norm(row.get('kind') or ''): score *= .65
                if body_matches:
                    first = next((body.find(t) for t in body_matches if body.find(t)>=0),0)
                else: first=0
                found.append({'material_id':row['id'],'course_id':row['course_id'],'title':row.get('title'),
                              'unit':page['page'],'unit_kind':row['unit_kind'],'heading':heading,'topics':topics,
                              'source_kind':row.get('kind'),'category':row.get('category'),
                              'score':round(score,2),'matched_terms':matches,'snippet':candidate_text[max(0,first-100):first+500],
                              'speaker_notes_matched':any(occurs(norm(notes),t) for t in matches),
                              'ocr_candidate_matched':any(occurs(norm(ocr_text),t) for t in matches),
                              'source_path':str(safe_source(self.study,row['relative_path'])),
                              'source_sha256':row['sha256'],'flags':page.get('flags',[])+(['ocr_unverified'] if ocr else []),
                              'verified':False,'next_action':'read with surrounding units; visually inspect formulas, figures and exclusions'})
        found.sort(key=lambda x:(-x['score'],x['material_id'],x['unit']))
        result = found[:limit]
        return {'query':query,'course':course,'state_revision':state.get('revision'),'expanded_terms':terms,
                'candidate_count':len(found),'returned':len(result),'has_more':len(found)>limit,'results':result,
                'unavailable':issues,'excluded_solution_materials':excluded,
                'evidence_sufficient':False,'next_action':'read candidate original units; if inadequate broaden to full section/outline',
                'warning':'Search ranks candidates only. It does not establish complete course coverage or source verification.'}

    def read(self, mid, start=1, end=None, around=1, include_solutions=False):
        state, rows, issues = self.fresh_rows(mid=mid)
        row = next((m for m in rows if m['id']==mid),None)
        if not row:
            raise ValueError('Source unavailable or stale: '+json.dumps([i for i in issues if i['id']==mid],ensure_ascii=False))
        if is_solution(row) and not include_solutions:
            raise ValueError('Solution material is hidden by default; use --include-solutions only for an authorized explanation or grading')
        pages = self.data(row)['pages']; end = end or start
        if start<1 or end<start or end>len(pages): raise ValueError('Unit range outside source')
        lo,hi = max(1,start-around),min(len(pages),end+around)
        selected = [dict(p) for p in pages if lo<=p['page']<=hi]
        for page in selected:
            candidates=self.ocr_candidates(row,page)
            if candidates:
                page['ocr_candidates']=candidates
                page['flags']=page.get('flags',[])+['ocr_unverified']
        return {'material_id':mid,'state_revision':state.get('revision'),'source_path':str(safe_source(self.study,row['relative_path'])),
                'source_sha256':row['sha256'],'unit_kind':row['unit_kind'],'requested':[start,end],
                'included':[lo,hi],'total_units':len(pages),'pages':selected,'verified':False,
                'source_kind':row.get('kind'),'category':row.get('category'),
                'previous_source_inspection':{'scope':row.get('inspection_scope'),
                    'source_hash_matches_registry':row.get('registry_sha256')==row['sha256'],
                    'units_recorded_as_visually_reviewed':[p for p in row.get('visually_reviewed_pages') or [] if lo<=p<=hi]
                        if row.get('registry_sha256')==row['sha256'] else [],
                    'note':'Historical source inspection only; not a new verification or mastery claim.'},
                'reading_note':'Complete cached units are returned, without text truncation. Expand if a proof or definition continues across this range.',
                'visual_note':'Text extraction cannot establish mathematical notation, diagrams, annotations, or official exclusions; inspect originals.'}

    def outline(self, course, include_solutions=False):
        state,rows,issues = self.fresh_rows(course)
        result=[]
        for row in rows:
            data = self.data(row)
            hidden=is_solution(row) and not include_solutions
            result.append({'id':row['id'],'title':row.get('title'),'path':row['relative_path'],
                           'unit_kind':row['unit_kind'],'units':row.get('units',0),'text_units':row.get('text_units',0),
                           'page_map':[] if hidden else row.get('page_map') or [],'map_is_complete':False,'solutions_hidden':hidden,
                           'headings':[] if hidden else [{'unit':p['page'],'heading':p.get('heading',''),
                               'flags':p.get('flags',[])} for p in data['pages']],
                           'warnings':row.get('warnings',[]),'verified':False})
        c=next((c for c in state.get('courses',[]) if c['id']==course),None)
        if c is None: raise ValueError('Unknown course: '+course)
        return {'course':course,'state_revision':state.get('revision'),'current_topic':c.get('current_topic'),
                'assignment_focus':c.get('assignment_focus'),'materials':result,'unavailable':issues,
                'coverage_complete':False,'instruction':'Full review must use the confirmed exam/assignment scope and account for all relevant materials and units; top search results are not a coverage checklist.'}

    def render(self, mid, unit, dpi=130, include_solutions=False):
        source=self.read(mid,unit,unit,0,include_solutions)
        row=next(m for m in self.catalog()['materials'] if m['id']==mid)
        original=Path(source['source_path'])
        if row['unit_kind']=='image':
            return {'path':str(original),'unit':unit,'source_sha256':source['source_sha256'],'verified':False}
        if row['unit_kind']!='pdf_page':
            return {'path':str(original),'unit':unit,'unit_kind':row['unit_kind'],'rendered':False,
                    'instruction':'Open the original at this slide/section; no fake PDF page mapping is provided.'}
        tool=shutil.which('pdftoppm')
        folder=self.folder/'renders'/source['source_sha256']
        if folder.resolve() != folder:
            raise ValueError('Render directory must not be a link to another location')
        folder.mkdir(parents=True,exist_ok=True)
        output=folder/f'page-{unit}-{dpi}.png'
        manifest=output.with_suffix('.json')
        valid=False
        if output.exists() and manifest.exists():
            try:
                record=read_json(manifest)
                valid=(record.get('source_sha256')==source['source_sha256'] and record.get('image_sha256')==digest(output)
                       and record.get('unit')==unit and record.get('dpi')==dpi)
            except (ValueError,OSError):
                pass
        if not valid:
            prefix=folder/f'page-{unit}-{dpi}-{os.getpid()}'
            try:
                if tool:
                    proc=subprocess.run([tool,'-f',str(unit),'-l',str(unit),'-r',str(dpi),'-singlefile','-png',str(original),str(prefix)],
                                        capture_output=True,timeout=90)
                    if proc.returncode: raise ValueError('PDF render failed: '+proc.stderr.decode('utf-8',errors='replace')[:180])
                else:
                    from learning_library.render import render_pdfium
                    render_pdfium(original,unit,dpi,Path(str(prefix)+'.png'))
                if digest(original)!=source['source_sha256']: raise ValueError('Source changed during rendering')
                os.replace(str(prefix)+'.png',output)
                atomic_json(manifest,{'source_sha256':source['source_sha256'],'image_sha256':digest(output),
                                      'unit':unit,'dpi':dpi,'rendered_at':now(),'verified':False})
            finally:
                candidate=Path(str(prefix)+'.png')
                if candidate.exists(): candidate.unlink()
        return {'path':str(output),'source_path':str(original),'unit':unit,'source_sha256':source['source_sha256'],'verified':False}


def norm(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',text).casefold()).strip()


def occurs(text,term):
    if re.fullmatch(r'[a-z0-9]+',term):
        return re.search(r'(?<![a-z0-9])'+re.escape(term)+r'(?![a-z0-9])',text) is not None
    return term in text


def count_term(text,term):
    if re.fullmatch(r'[a-z0-9]+',term):
        return len(re.findall(r'(?<![a-z0-9])'+re.escape(term)+r'(?![a-z0-9])',text))
    return text.count(term)


def query_terms(query):
    q=norm(query)
    tokens=[t for t in re.findall(r'[a-z0-9]+(?:[-\'][a-z0-9]+)*|[\u3400-\u9fff]+',q) if len(t)>1 and t not in STOP]
    # Keep exact phrases and bilingual aliases in addition to word search.
    triggers=[norm(t) for group in ALIASES for t in group if occurs(q,norm(t))]
    for group in ALIASES:
        matches=[norm(t) for t in group if occurs(q,norm(t))]
        # A more specific phrase such as 复数方根 must not expand to every
        # general complex-number page through its shorter 复数 substring.
        if any(not any(t!=longer and t in longer for longer in triggers) for t in matches):
            tokens.extend(norm(t) for t in group)
    return list(dict.fromkeys(tokens))


def is_solution(row):
    name=norm(' '.join(str(row.get(k) or '') for k in ['filename','relative_path','title','kind','category'])).replace('_',' ')
    return row.get('contains_solutions') is True or bool(re.search(r'\bsolutions?\b|\banswers?\b|答案|解答',name))


def page_topics(row,page):
    if row.get('registry_sha256') and row['registry_sha256'] != row.get('sha256'):
        return [] # Old page labels cannot be trusted for changed originals.
    result=[]
    for entry in row.get('page_map') or []:
        if isinstance(entry,dict):
            try:
                if int(entry.get('start',0))<=page<=int(entry.get('end',0)):
                    result.append(str(entry.get('topic','')))
            except (TypeError,ValueError): pass
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,default=DEFAULT_STATE)
    parser.add_argument('--cache-dir',type=Path)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('sync'); p.add_argument('--course'); p.add_argument('--force',action='store_true')
    p=sub.add_parser('ocr'); p.add_argument('--course'); p.add_argument('--force',action='store_true'); p.add_argument('--language',default='en-US')
    p=sub.add_parser('status'); p.add_argument('--check-sources',action='store_true')
    p=sub.add_parser('search'); p.add_argument('query'); p.add_argument('--course'); p.add_argument('--limit',type=int,default=8); p.add_argument('--include-solutions',action='store_true')
    p=sub.add_parser('read'); p.add_argument('material_id'); p.add_argument('--start',type=int,default=1); p.add_argument('--end',type=int); p.add_argument('--around',type=int,default=1); p.add_argument('--include-solutions',action='store_true')
    p=sub.add_parser('outline'); p.add_argument('--course',required=True); p.add_argument('--include-solutions',action='store_true')
    p=sub.add_parser('render'); p.add_argument('material_id'); p.add_argument('--unit',type=int,required=True); p.add_argument('--dpi',type=int,default=130); p.add_argument('--include-solutions',action='store_true')
    p=sub.add_parser('context'); p.add_argument('--course'); p.add_argument('--detail',action='store_true')
    p=sub.add_parser('detail'); p.add_argument('collection'); p.add_argument('record_id')
    args=parser.parse_args(); lib=Library(args.state,args.cache_dir)
    if args.command=='sync': result=lib.sync(args.course,args.force)
    elif args.command=='ocr':
        from learning_library.ocr import build_ocr
        result=build_ocr(lib,args.course,args.force,args.language)
    elif args.command=='status':
        result=lib.summary()
        if args.check_sources:
            state,rows,issues=lib.fresh_rows()
            result.update(current_state_revision=state.get('revision'),fresh_materials=len(rows),unavailable=issues)
    elif args.command=='search': result=lib.search(args.query,args.course,max(1,min(100,args.limit)),args.include_solutions)
    elif args.command=='read': result=lib.read(args.material_id,args.start,args.end,max(0,args.around),args.include_solutions)
    elif args.command=='outline': result=lib.outline(args.course,args.include_solutions)
    elif args.command=='render': result=lib.render(args.material_id,args.unit,max(72,min(300,args.dpi)),args.include_solutions)
    elif args.command=='context':
        from learning_library.context import build_context
        result=build_context(lib.state(),args.course,args.detail)
    else:
        from learning_library.context import read_detail
        result=read_detail(lib.state(),args.collection,args.record_id)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'error':str(exc),'type':type(exc).__name__,'state_written':False},ensure_ascii=False),file=sys.stderr)
        sys.exit(1)
