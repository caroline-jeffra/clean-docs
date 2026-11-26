"""Build a ticket from a spec, enforcing the profile's template for its type, for the profile's tracker.

Usage: python3 ticket.py SPEC.json OUT_DIR --type TYPE [--project KEY] [--projects ABC,XYZ]
                         [--update REF] [--profile NAME] [--repo PATH]

The house style comes from the clean-docs profile for the repo (scripts/house_style.py
at the plugin root; --profile picks one by name). From it this script takes the
tracker, the summary limit, the ticket types, each type's template and the urgency
levels. Trackers:

  github, gitlab  An issue. Writes OUT_DIR/title.txt, OUT_DIR/body.md (Markdown with
                  ### headings) and OUT_DIR/issue.json ({"title", "labels", "update"}).
                  Labels come from the type's "labels". References are #12 or
                  group/repo#12, left for the forge to link. --update takes the issue
                  number. issue.json's "repo" is the profile's tracker.repo, the repo the
                  issue goes to, or null for the current repo's origin. --project,
                  --projects and "fields" are Jira-only.
  jira            A Jira work item. --project defaults to the profile's tracker.project
                  and is required without one; --update takes a key in it.
                  Writes OUT_DIR/description.md (Markdown for the Atlassian MCP's
                  createJiraIssue and editJiraIssue), and for the acli fallback
                  OUT_DIR/workitem.json (for `acli jira workitem create --from-json`,
                  so no ticket text passes through the shell) and OUT_DIR/description.json
                  (the ADF alone, for `acli jira workitem edit --description-file`).
                  Keys of the profile's link_projects, --project and any --projects
                  become links to the browse page.

--update builds the body for an existing ticket (reference/update.md): the first line
says it was restructured; on Jira, workitem.json is in the shape of
`acli jira workitem edit --from-json`, which has no custom fields.

SPEC.json is {"summary": "...", "blocks": [...], "fields": {...}}, each block a one-key object:
  {"h": "Header"}            section header
  {"p": "Text"}              paragraph
  {"ul": ["item", ...]}      bullet list
  {"ol": ["item", ...]}      ordered list

The optional "fields" (Jira only) sets components and single-select custom fields:
  {"customfield_10000": {"value": "Option"}, "components": [{"name": "Name"}]}

In any text, `backticks` become inline code.

A template is the type's Markdown file, whose table rows read
  | <n> | <Header> | <Block> | <yes|no> | <content> |
where Block is the allowed block kinds joined by "/" (p, ul, ol), or "files" for
the impacted-files section, and the fourth column says whether the section needs
content. The script refuses a spec unless:
  - the summary fits the profile's max_summary;
  - the first block is the generated-ticket line, with an urgency level from the
    profile's rubric when the type has urgency. The level is written plain in the
    spec and rendered bold;
  - the headers are exactly the template's, in order, each section holding only
    its allowed block kinds;
  - every required section has content;
  - code spans appear only in the files section, which is a bullet list of `path`
    items or the not-investigated line, optionally followed by an Overlaps line
    naming related tickets in the tracker's reference form.

It prints the profile and tracker, then the references to link (Jira) or the
references and labels (forges), and the fields as JSON (Jira).
"""

import argparse
import json
import os
import re
import sys
from collections import namedtuple

PLUGIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(PLUGIN, 'scripts'))

import house_style  # noqa: E402

GENERATED = 'This is a generated ticket description.'
UPDATED = 'This ticket description was restructured by a generator from the original.'
URGENCY_SUFFIX = ' Assessed ticket urgency: '
URGENCY = GENERATED + URGENCY_SUFFIX
NOT_INVESTIGATED = 'Codebase not yet investigated.'
BREAKS = '\r\n\x0b\x0c\x85\u2028\u2029'
CODE_SPAN = rf'`[^`{BREAKS}]+`'
PATH_ITEM = rf'`[^`<>{BREAKS}]+`'
PROJECT_KEY = r'[A-Z][A-Z0-9]{1,9}'
JIRA_REF = r'[A-Z][A-Z0-9]{1,9}-\d+'
FORGE_REF = r'(?:[\w.-]+(?:/[\w.-]+)+)?#\d+'
FORGES = ('github', 'gitlab')
BLOCK_KINDS = ('p', 'ul', 'ol')
MD_SPECIAL = re.compile(r'([\\`*_\[\]<>~|#!])')
MD_BLOCK_START = re.compile(r'^(\s*)([-+>/=]|\d+[.)])')  # / also stops GitLab quick actions
LINE_BREAKS = re.compile(rf'\s*[{BREAKS}]+\s*')
MENTION = re.compile(r'(?<![\w.@+-])@[A-Za-z0-9_][\w./-]*')
FENCE = re.compile(r'```|~~~')
ROW = re.compile(r'^\| *\d+ *\| *(.+?) *\| *([A-Za-z/ ]+?) *\| *(yes|no) *\|', re.M | re.I)
ROW_LIKE = re.compile(r'^\| *\d+ *\|.*$', re.M)
LEVEL = re.compile(r'^## \d+\. (.+?) (?:—|–|--?) ', re.M)
LEVEL_LIKE = re.compile(r'^## \d+\..*$', re.M)

Section = namedtuple('Section', 'header kinds required')
# sections: [Section]; files: the files section's header or None; levels: urgency levels, () for none
Template = namedtuple('Template', 'sections files levels')
# kind: jira | github | gitlab; refs: regex for references in text; overlap: regex for one Overlaps reference;
# browse: Jira browse URL prefix, or None on forges (which link references themselves)
Tracker = namedtuple('Tracker', 'kind refs overlap browse')


def fail(msg):
    sys.exit('spec error: ' + msg)


def jira(projects, browse):
    return Tracker('jira', r'\b(?:' + '|'.join(map(re.escape, projects)) + r')-\d+\b', JIRA_REF, browse)


def forge(kind):
    return Tracker(kind, r'(?<![\w#&/])' + FORGE_REF + r'\b', FORGE_REF, None)


# ---------------------------------------------------------------- templates


def parse_template(text, where='template'):
    """The sections of a template file's table, in order."""
    bad = [line for line in ROW_LIKE.findall(text) if not ROW.match(line)]
    if bad:
        sys.exit(f'{where}: table row not in the "| n | Header | Block | yes/no | ... |" format: {bad[0]!r}')
    sections = []
    for header, block, required in ROW.findall(text):
        kinds = tuple(k.strip().lower() for k in block.split('/'))
        required = required.lower()
        if kinds != ('files',) and not all(k in BLOCK_KINDS for k in kinds):
            sys.exit(f'{where}: section "{header}" has block "{block}"; use p, ul, ol joined by "/", or files')
        sections.append(Section(header, kinds, required == 'yes'))
    if not sections:
        sys.exit(f'{where}: no section table found (rows like "| 1 | Header | p | yes | ... |")')
    if len({s.header for s in sections}) != len(sections):
        sys.exit(f'{where}: section headers must be unique')
    if sum(s.kinds == ('files',) for s in sections) > 1:
        sys.exit(f'{where}: at most one files section')
    return sections


def parse_levels(text, where='urgency rubric'):
    bad = [line for line in LEVEL_LIKE.findall(text) if not LEVEL.match(line)]
    if bad:
        sys.exit(f'{where}: level heading not in the "## n. Level — meaning" format: {bad[0]!r}')
    levels = tuple(LEVEL.findall(text))
    if not levels:
        sys.exit(f'{where}: no levels found (headings like "## 1. Critical — meaning")')
    return levels


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def template_for(profile, ticket_type):
    spec = profile['tickets']['types'][ticket_type]
    sections = parse_template(read(spec['template']), spec['template'])
    files = next((s.header for s in sections if s.kinds == ('files',)), None)
    levels = ()
    if spec.get('urgency', True):
        rubric = profile['tickets'].get('urgency')
        if not rubric:
            sys.exit(f'type "{ticket_type}" has urgency but the profile names no urgency rubric')
        levels = parse_levels(read(rubric), rubric)
    return Template(sections, files, levels)


# ---------------------------------------------------------------- inline


def flat(text):
    """One line: every run of line breaks (and the space around it) becomes a single space."""
    return LINE_BREAKS.sub(' ', text)


def split_inline(s, refs):
    """Yield (kind, text) parts of s, kind one of 'ref', 'code' or 'text'."""
    for part in re.split(rf'({refs}|{CODE_SPAN})', flat(s)):
        if not part:
            continue
        if re.fullmatch(refs, part):
            yield 'ref', part
        elif re.fullmatch(CODE_SPAN, part):
            yield 'code', part[1:-1]
        else:
            yield 'text', part


def make_inline(tracker):
    """ADF text nodes for s (Jira): references link to the browse page."""

    def inline(s):
        out = []
        for kind, text in split_inline(s, tracker.refs):
            if kind == 'ref' and tracker.browse:
                out.append(
                    {
                        'type': 'text',
                        'text': text,
                        'marks': [{'type': 'link', 'attrs': {'href': tracker.browse + text}}],
                    }
                )
            elif kind == 'code':
                out.append({'type': 'text', 'text': text, 'marks': [{'type': 'code'}]})
            else:
                out.append({'type': 'text', 'text': text})
        return out

    return inline  # split_inline already flattened line breaks, as markdown() does


# ---------------------------------------------------------------- checks


def check_block(i, block):
    if not isinstance(block, dict) or len(block) != 1:
        fail(f'block {i} must be a one-key object, got {block!r}')
    ((kind, value),) = block.items()
    if kind in ('h', 'p'):
        if not isinstance(value, str) or not value.strip():
            fail(f'block {i}: "{kind}" needs non-empty text')
        return kind, [value]
    if kind in ('ul', 'ol'):
        if not isinstance(value, list) or not value:
            fail(f'block {i}: "{kind}" needs a non-empty list of strings; omit the block for an empty optional section')
        if not all(isinstance(v, str) and v.strip() for v in value):
            fail(f'block {i}: every "{kind}" item must be non-empty text')
        return kind, value
    fail(f'block {i}: unknown kind {kind!r}')


def check(blocks, template, tracker, update=False):
    """Refuse blocks that don't follow the template; see the module docstring."""
    for i, block in enumerate(blocks):
        check_block(i, block)
    first = blocks[0].get('p', '') if blocks else ''
    check_text(0, [first], tracker)
    line = UPDATED if update else GENERATED
    urgency = line + URGENCY_SUFFIX
    if not template.levels:
        if first != line:
            fail(f'first block must be {{"p": "{line}"}}')
    elif not (first.startswith(urgency) and first[len(urgency) :] in template.levels):
        fail(f'first block must be {{"p": "{urgency}<{" | ".join(template.levels)}>"}}')

    expected = [s.header for s in template.sections]
    headers = [b['h'] for b in blocks if 'h' in b]
    if headers != expected:
        fail(f'headers must be exactly, in order: {expected}')
    if 'h' not in blocks[1]:
        fail('the first header must follow the first line directly')

    by_header = {s.header: s for s in template.sections}
    section, content = None, {}
    for i, block in enumerate(blocks[1:], 1):
        ((kind, value),) = block.items()
        if kind == 'h':
            section = value
            content[section] = []
            continue
        content[section].append(block)
        if section != template.files and kind not in by_header[section].kinds:
            fail(f'block {i}: "{section}" takes {"/".join(by_header[section].kinds)}, not {kind}')
        texts = [value] if isinstance(value, str) else value
        check_text(i, texts, tracker)
        if section != template.files and any(re.search(CODE_SPAN, t) for t in texts):
            where = f'only in "{template.files}"' if template.files else 'nowhere in this type'
            fail(f'block {i}: code spans belong {where}, not in "{section}"')
    empty = [s.header for s in template.sections if s.required and not content[s.header]]
    if empty:
        fail(f'sections need content: {empty}')
    if template.files and content[template.files]:
        check_files(template.files, content[template.files], tracker)


def check_text(i, texts, tracker):
    """Refuse what the builder can't render faithfully, or what a forge would act on."""
    for t in texts:
        if FENCE.search(t):
            fail(f"block {i}: code blocks (``` or ~~~) aren't supported; describe the content, or link to it")
        if tracker.kind != 'jira':
            plain = re.sub(CODE_SPAN, '', flat(t))
            m = MENTION.search(plain)
            if m:
                fail(f'block {i}: "{m.group()}" would notify someone on {tracker.kind}; name the role instead')


def check_files(header, blocks, tracker):
    """The files section is a list of `path` items (or the not-investigated line), then an optional Overlaps line."""
    first, rest = blocks[0], blocks[1:]
    paths = first.get('ul')
    if not (first == {'p': NOT_INVESTIGATED} or (paths and all(re.fullmatch(PATH_ITEM, i) for i in paths))):
        fail(f'"{header}" must start with a bullet list of `path` items, or {{"p": "{NOT_INVESTIGATED}"}}')
    overlaps = rf'Overlaps with {tracker.overlap}(?:, {tracker.overlap})*\.'
    if rest and (len(rest) > 1 or not re.fullmatch(overlaps, rest[0].get('p', ''))):
        example = 'ABC-1, ABC-2' if tracker.kind == 'jira' else '#1, #2'
        fail(f'"{header}" may end only with one {{"p": "Overlaps with {example}."}}')


def check_fields(fields):
    """Fields must be single-select custom fields ({"value": ...}) or components ([{"name": ...}])."""
    if not isinstance(fields, dict):
        fail('"fields" must be an object')

    def named(v, key):
        return isinstance(v, dict) and list(v) == [key] and isinstance(v[key], str) and v[key].strip()

    for field, value in fields.items():
        if field == 'components':
            if not isinstance(value, list) or not value or not all(named(c, 'name') for c in value):
                fail('"components" must be a non-empty list of {"name": "..."}')
        elif re.fullmatch(r'customfield_\d+', field):
            if not named(value, 'value'):
                fail(f'"{field}" must be {{"value": "..."}}')
        else:
            fail(f'unknown field {field!r}: only components and customfield_* selects are set')


# ---------------------------------------------------------------- output


def split_urgency(first):
    """Split an already-checked first line into (text up to the level, level); level is '' without urgency."""
    head, sep, level = first.rpartition(URGENCY_SUFFIX)
    return (head + sep, level) if sep else (first, '')


def adf(blocks, tracker):
    """The ADF document for already-checked blocks (Jira)."""
    inline = make_inline(tracker)

    def h(t):
        return {
            'type': 'paragraph',
            'content': [dict(n, marks=n.get('marks', []) + [{'type': 'strong'}]) for n in inline(t)],
        }

    def p(t):
        return {'type': 'paragraph', 'content': inline(t)}

    def ul(items):
        return {'type': 'bulletList', 'content': [{'type': 'listItem', 'content': [p(i)]} for i in items]}

    def ol(items):
        return {'type': 'orderedList', 'content': [{'type': 'listItem', 'content': [p(i)]} for i in items]}

    kinds = {'h': h, 'p': p, 'ul': ul, 'ol': ol}
    content = [kinds[k](v) for b in blocks for k, v in b.items()]
    line, level = split_urgency(blocks[0].get('p', '') if blocks else '')
    if level:
        content[0] = {
            'type': 'paragraph',
            'content': [*inline(line), {'type': 'text', 'text': level, 'marks': [{'type': 'strong'}]}],
        }
    return {'version': 1, 'type': 'doc', 'content': content}


def markdown(blocks, tracker):
    """Render already-checked blocks as Markdown; plain text is escaped so it stays literal.

    Jira: headers are bold paragraphs and references link to the browse page.
    Forges: headers are ### headings and references stay bare for the forge to link.
    """

    def inline(s):
        out = []
        for kind, text in split_inline(s, tracker.refs):
            if kind == 'ref':
                out.append(f'[{text}]({tracker.browse}{text})' if tracker.browse else text)
            elif kind == 'code':
                out.append(f'`{text}`')
            else:
                out.append(MD_SPECIAL.sub(r'\\\1', text))
        return MD_BLOCK_START.sub(lambda m: m.group(1) + m.group(2)[:-1] + '\\' + m.group(2)[-1], ''.join(out), count=1)

    header = (lambda t: f'**{inline(t)}**') if tracker.kind == 'jira' else (lambda t: f'### {inline(t)}')
    kinds = {
        'h': header,
        'p': inline,
        'ul': lambda items: '\n'.join(f'- {inline(i)}' for i in items),
        'ol': lambda items: '\n'.join(f'{n}. {inline(i)}' for n, i in enumerate(items, 1)),
    }
    rendered = [kinds[k](v) for b in blocks for k, v in b.items()]
    line, level = split_urgency(blocks[0].get('p', '') if blocks else '')
    if level:
        level = MD_SPECIAL.sub(r'\\\1', level)
        rendered[0] = f'{inline(line)}**{level}**'
    return '\n\n'.join(rendered) + '\n'


def references(blocks, tracker):
    """The references that render as references — not those inside code spans."""
    texts = [t for b in blocks for v in b.values() for t in ([v] if isinstance(v, str) else v)]
    return sorted({part for t in texts for kind, part in split_inline(t, tracker.refs) if kind == 'ref'})


OUTPUTS = ('workitem.json', 'description.json', 'description.md', 'title.txt', 'body.md', 'issue.json')


def prepare(out_dir):
    """Create out_dir, and remove this script's outputs from an earlier run so no stale file is left beside new ones."""
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        sys.exit(f'{out_dir} is a file, not a directory')
    os.makedirs(out_dir, exist_ok=True)
    for name in OUTPUTS:
        path = os.path.join(out_dir, name)
        if os.path.lexists(path):
            os.remove(path)


def write(out_dir, name, data):
    with open(os.path.join(out_dir, name), 'w', encoding='utf-8') as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f)


# ---------------------------------------------------------------- main


def load_profile(repo, name):
    try:
        profile = house_style.resolve(repo, name)
    except house_style.ProfileError as e:
        sys.exit(f'profile error: {e}')
    resolved = profile['resolved']
    if resolved['from'] == 'ambiguous':
        sys.exit(
            'profile error: several profiles or overrides match this repo equally: '
            f'{resolved["candidates"]}; pass --profile Name or Name/Override'
        )
    kind = profile['tracker']['kind']
    if kind not in ('jira', *FORGES):
        sys.exit(f'profile error: {resolved["name"]} has tracker "{kind}"; file-ticket needs jira, github or gitlab')
    return profile


def read_spec(path, max_summary):
    with open(path, encoding='utf-8-sig') as f:
        spec = json.load(f)
    if (
        not isinstance(spec, dict)
        or not isinstance(spec.get('summary'), str)
        or not isinstance(spec.get('blocks'), list)
    ):
        fail('top level must be {"summary": "...", "blocks": [...]}')
    summary = spec['summary'].strip()
    if not summary:
        fail('summary is empty')
    if re.search(r'[\x00-\x1f\x7f\x85\u2028\u2029]', summary):
        fail('summary must be one line of plain text')
    if len(summary) > max_summary:
        fail(f'summary is {len(summary)} characters; the limit is {max_summary}')
    return summary, spec['blocks'], spec.get('fields', {})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('spec')
    ap.add_argument('out_dir')
    ap.add_argument('--type', required=True, type=str.lower)
    ap.add_argument('--project', type=str.upper, help='Jira project key (Jira only)')
    ap.add_argument('--projects', default='', help='extra Jira project keys to link, comma-separated')
    ap.add_argument('--update', metavar='REF', help='existing ticket this replaces: a Jira key, or an issue number')
    ap.add_argument('--profile', help='profile name (default: the one matching --repo)')
    ap.add_argument('--repo', default='.', help='repo to match a profile for (default: %(default)s)')
    args = ap.parse_args()

    profile = load_profile(args.repo, args.profile)
    kind = profile['tracker']['kind']
    types = profile['tickets']['types']
    if args.type not in types:
        sys.exit(f"--type must be one of the profile's types: {sorted(types)}")
    template = template_for(profile, args.type)

    resolved = profile['resolved']
    shown = f'{resolved["name"]}/{resolved["override"]}' if resolved.get('override') else resolved['name']
    note = None
    if kind == 'jira':
        default = profile['tracker'].get('project')
        if args.project:
            destination = f'project {args.project} (from --project)'
            if default and default != args.project:
                note = f"note: --project {args.project} replaces the profile's project {default}"
        elif default:
            args.project = default
            destination = f'project {default} (from profile {shown})'
        else:
            sys.exit('--project is needed: this profile sets no "tracker.project"')
        if not re.fullmatch(PROJECT_KEY, args.project):
            sys.exit('--project must be a Jira project key such as ABC')
        extra = [k.strip().upper() for k in args.projects.split(',') if k.strip()]
        bad = [k for k in extra if not re.fullmatch(PROJECT_KEY, k)]
        if bad:
            sys.exit(f'--projects takes project keys such as ABC, not {bad}')
        update = args.update.upper() if args.update else None
        if update and not re.fullmatch(rf'{re.escape(args.project)}-\d+', update):
            sys.exit('--update takes a ticket key in --project, such as ABC-123')
        browse = profile['tracker']['site'].rstrip('/') + '/browse/'
        tracker = jira(list(dict.fromkeys(profile['tracker']['link_projects'] + [args.project] + extra)), browse)
    else:
        if args.project or args.projects:
            sys.exit(f'--project and --projects are Jira-only; this profile files {kind} issues')
        update = None
        if args.update:
            m = re.fullmatch(r'#?(\d+)', args.update.strip())
            if not m:
                sys.exit('--update takes an issue number such as 12 or #12')
            update = int(m.group(1))
        tracker = forge(kind)
        repo = profile['tracker'].get('repo')
        destination = f'{repo} (from profile {shown})' if repo else "this repo's origin"
        origin = (resolved.get('remote') or '').partition('/')[2]
        if repo and repo.lower() != origin.lower():
            note = f'note: the issue goes to {repo}, not ' + (f'this repo ({origin})' if origin else 'this repo')

    summary, blocks, fields = read_spec(args.spec, profile['tickets']['max_summary'])
    check_fields(fields)
    if fields and kind != 'jira':
        fail(f'"fields" are Jira-only; this profile files {kind} issues')
    check(blocks, template, tracker, update is not None)

    prepare(args.out_dir)
    print(f'profile: {shown} ({resolved["from"]}), tracker: {kind}')
    print(f'destination: {destination}')
    if note:
        print(note)
    for warning in resolved.get('warnings', []):
        print(f'warning: {warning}')
    if kind == 'jira':
        description = adf(blocks, tracker)
        if update:
            workitem = {'issues': [update], 'summary': summary, 'description': description}
        else:
            workitem = {
                'projectKey': args.project,
                'type': types[args.type]['name'],
                'summary': summary,
                'description': description,
            }
            if fields:
                workitem['additionalAttributes'] = fields
        write(args.out_dir, 'workitem.json', workitem)
        write(args.out_dir, 'description.json', description)
        write(args.out_dir, 'description.md', markdown(blocks, tracker))
        print(f'linkable keys: {[k for k in references(blocks, tracker) if k != update]}')
        print(f'fields: {json.dumps(fields)}')
        if update and fields:
            print('note: workitem.json carries no fields in update mode; set them with editJiraIssue additional_fields')
    else:
        labels = types[args.type]['labels']
        write(args.out_dir, 'title.txt', summary)
        write(args.out_dir, 'body.md', markdown(blocks, tracker))
        write(args.out_dir, 'issue.json', {'title': summary, 'labels': labels, 'update': update, 'repo': repo})
        print(f'references: {references(blocks, tracker)}')
        print(f'labels: {json.dumps(labels)}')


if __name__ == '__main__':
    main()
