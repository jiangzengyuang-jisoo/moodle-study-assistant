"""Validate reading routes, without claiming an assistant has read any page."""
from pathlib import Path


def reading_route(library, mid):
    _, rows, _ = library.fresh_rows(mid=mid)
    row = next((r for r in rows if r['id'] == mid), None)
    if not row or row.get('status') != 'indexed' or not row.get('units'):
        raise ValueError('No usable chapter index')
    data = library.read(mid, end=row['units'], around=0, include_solutions=True)
    pages = data.get('pages', [])
    if len(pages) != row['units'] or [p.get('page') for p in pages] != list(range(1, row['units'] + 1)):
        raise ValueError('Incomplete chapter index')
    visual = []
    has_text = any(p.get('text', '').strip() for p in pages)
    for page in pages:
        if page.get('text', '').strip():
            continue
        if data.get('unit_kind') not in {'pdf_page', 'image'}:
            continue
        rendered = library.render(mid, page['page'], include_solutions=True)
        if rendered.get('rendered') is False or not Path(rendered['path']).is_file():
            raise ValueError('Original page cannot be rendered')
        from PIL import Image, ImageStat
        with Image.open(rendered['path']) as image:
            image.load()
            # A blank sheet is not proof of readable scanned course content.
            if ImageStat.Stat(image.convert('L')).stddev[0] > 1:
                visual.append(page['page'])
    if not has_text and not visual:
        raise ValueError('No text or visible page content')
    return {'reading_mode': 'mixed' if has_text and visual else 'text' if has_text else 'visual',
            'visual_units': visual, 'visual_review_required': bool(visual),
            'reviewed': False, 'mastery_updated': False}
