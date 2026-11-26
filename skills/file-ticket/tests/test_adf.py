import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SKILL, 'scripts', 'adf.py')
FIXTURES = os.path.join(SKILL, 'tests', 'fixtures', 'profiles')
sys.path.insert(0, os.path.dirname(SCRIPT))

import adf  # noqa: E402
import house_style  # noqa: E402  (adf puts the plugin's scripts/ on the path)

URGENT = {'p': adf.URGENCY + 'Nice to have'}
BROWSE = 'https://example.atlassian.net/browse/'
PROJECTS = ['PROJ']
PROFILE = house_style.load('Acme', os.path.join(FIXTURES, 'Acme'))
TASK = adf.template_for(PROFILE, 'task')
RESEARCH = adf.template_for(PROFILE, 'research')


def skeleton(ticket_type):
    path = PROFILE['tickets']['types'][ticket_type]['template']
    with open(path, encoding='utf-8') as f:
        return json.loads(re.search(r'```json\n(.*?)```', f.read(), re.S).group(1))


def task(*sections):
    """A task spec whose sections are given as (header, [blocks]) pairs."""
    blocks = [URGENT]
    for header, content in sections:
        blocks += [{'h': header}, *content]
    return blocks


VALID_TASK = [
    ('What needs to be done', [{'p': 'Do it.'}]),
    ('Acceptance criteria', [{'ul': ['It is done.']}]),
    ('Out of scope', []),
    ('Potentially impacted files', [{'ul': ['`src/foo.py`']}]),
]


def texts(doc):
    out = []

    def walk(node):
        if node.get('type') == 'text':
            out.append((node['text'], [m['type'] for m in node.get('marks', [])]))
        for child in node.get('content', []):
            walk(child)

    walk(doc)
    return out


class Templates(unittest.TestCase):
    def test_every_skeleton_builds_against_its_own_table(self):
        for ticket_type in PROFILE['tickets']['types']:
            with self.subTest(ticket_type):
                adf.build(skeleton(ticket_type)['blocks'], PROJECTS, adf.template_for(PROFILE, ticket_type), BROWSE)

    def test_table_is_parsed_in_order_with_kinds_and_required(self):
        self.assertEqual(
            TASK.sections,
            [
                adf.Section('What needs to be done', ('p',), True),
                adf.Section('Acceptance criteria', ('ul',), True),
                adf.Section('Out of scope', ('ul',), False),
                adf.Section('Potentially impacted files', ('files',), True),
            ],
        )
        self.assertEqual(TASK.files, 'Potentially impacted files')

    def test_levels_come_from_the_rubric_and_types_can_opt_out(self):
        self.assertEqual(TASK.levels, ('Critical', 'Blocking', 'Nice to have'))
        self.assertEqual(RESEARCH.levels, ())
        self.assertIsNone(RESEARCH.files)

    def test_bad_tables_rejected(self):
        for text in (
            'no table here',
            '| 1 | A | p | yes | x |\n| 2 | A | p | yes | x |',
            '| 1 | A | files | yes | x |\n| 2 | B | files | yes | x |',
            '| 1 | A | table | yes | x |',
        ):
            with self.subTest(text), self.assertRaises(SystemExit):
                adf.parse_template(text)

    def test_optional_section_may_be_blank(self):
        adf.build(task(*VALID_TASK), PROJECTS, TASK, BROWSE)

    def test_required_section_may_not_be_blank(self):
        sections = [(h, [] if h == 'What needs to be done' else c) for h, c in VALID_TASK]
        with self.assertRaises(SystemExit):
            adf.build(task(*sections), PROJECTS, TASK, BROWSE)

    def test_headers_out_of_order_rejected(self):
        with self.assertRaises(SystemExit):
            adf.build(task(VALID_TASK[1], VALID_TASK[0], *VALID_TASK[2:]), PROJECTS, TASK, BROWSE)

    def test_block_kind_outside_the_sections_kinds_rejected(self):
        sections = [('What needs to be done', [{'ul': ['Do it.']}]), *VALID_TASK[1:]]
        with self.assertRaises(SystemExit):
            adf.build(task(*sections), PROJECTS, TASK, BROWSE)

    def test_code_span_outside_code_section_rejected(self):
        sections = [('What needs to be done', [{'p': 'Call `foo`.'}]), *VALID_TASK[1:]]
        with self.assertRaises(SystemExit):
            adf.build(task(*sections), PROJECTS, TASK, BROWSE)

    def test_type_without_files_section_rejects_code_spans(self):
        blocks = skeleton('research')['blocks']
        blocks[2] = {'p': 'Because `cache` is slow.'}
        with self.assertRaises(SystemExit):
            adf.build(blocks, PROJECTS, RESEARCH, BROWSE)


class FilesSection(unittest.TestCase):
    def build(self, *content):
        adf.build(task(*VALID_TASK[:3], ('Potentially impacted files', list(content))), PROJECTS, TASK, BROWSE)

    def test_paths_and_overlaps_accepted(self):
        self.build({'ul': ['`src/a.py`', '`src/b.py`']}, {'p': 'Overlaps with PROJ-1, PROJ-2.'})

    def test_not_investigated_line_accepted(self):
        self.build({'p': adf.NOT_INVESTIGATED})

    def test_suggestions_rejected(self):
        for content in (
            [{'p': 'Change `Foo.bar`.'}],
            [{'ol': ['`src/a.py`']}],
            [{'ul': ['`src/a.py` — add a null check']}],
            [{'ul': ['Update `src/a.py`']}],
            [{'ul': ['`src/a.py`']}, {'p': 'Overlaps with PROJ-1, which does it.'}],
            [{'ul': ['`src/a.py`']}, {'p': 'Overlaps with PROJ-1.'}, {'p': 'Overlaps with PROJ-2.'}],
        ):
            with self.subTest(content), self.assertRaises(SystemExit):
                self.build(*content)


class Fields(unittest.TestCase):
    def test_custom_select_and_components_accepted(self):
        adf.check_fields({'customfield_10000': {'value': 'Option'}, 'components': [{'name': 'Billing'}]})

    def test_malformed_fields_rejected(self):
        for bad in (
            [],
            {'customfield_10000': 'Option'},
            {'customfield_10000': {'value': ''}},
            {'customfield_10000': {'value': 'X', 'id': '1'}},
            {'components': []},
            {'components': ['Billing']},
            {'components': [{'name': ' '}]},
            {'priority': {'name': 'High'}},
            {'customfield_x': {'value': 'X'}},
        ):
            with self.subTest(bad), self.assertRaises(SystemExit):
                adf.check_fields(bad)


class FirstLine(unittest.TestCase):
    def test_wrong_urgency_level_rejected(self):
        blocks = task(*VALID_TASK)
        blocks[0] = {'p': adf.URGENCY + 'High'}
        with self.assertRaises(SystemExit):
            adf.build(blocks, PROJECTS, TASK, BROWSE)

    def test_type_without_urgency_rejects_urgency_line(self):
        blocks = skeleton('research')['blocks']
        blocks[0] = URGENT
        with self.assertRaises(SystemExit):
            adf.build(blocks, PROJECTS, RESEARCH, BROWSE)

    def test_update_line_without_urgency(self):
        blocks = skeleton('research')['blocks']
        blocks[0] = {'p': adf.UPDATED}
        adf.build(blocks, PROJECTS, RESEARCH, BROWSE, update=True)
        with self.assertRaises(SystemExit):
            adf.build(blocks, PROJECTS, RESEARCH, BROWSE)

    def test_urgency_type_rejects_plain_line(self):
        blocks = task(*VALID_TASK)
        blocks[0] = {'p': adf.GENERATED}
        with self.assertRaises(SystemExit):
            adf.build(blocks, PROJECTS, TASK, BROWSE)


class Blocks(unittest.TestCase):
    def test_malformed_blocks_rejected(self):
        for bad in ({}, {'h': 'x', 'p': 'y'}, {'ul': []}, {'ul': 'abc'}, {'ul': ['']}, {'h': ''}, {'p': 5}, {'q': 'x'}):
            with self.subTest(bad), self.assertRaises(SystemExit):
                adf.check_block(0, bad)


class Inline(unittest.TestCase):
    inline = staticmethod(adf.make_inline(PROJECTS, BROWSE))

    def test_keys_link_and_backticks_become_code(self):
        self.assertEqual(
            [(n['text'], [m['type'] for m in n.get('marks', [])]) for n in self.inline('See PROJ-12 and `x`.')],
            [('See ', []), ('PROJ-12', ['link']), (' and ', []), ('x', ['code']), ('.', [])],
        )

    def test_link_points_at_browse_page(self):
        (link,) = [n for n in self.inline('PROJ-7') if n.get('marks')]
        self.assertEqual(link['marks'][0]['attrs']['href'], BROWSE + 'PROJ-7')

    def test_lookalike_keys_not_linked(self):
        for s in ('SHA-256', 'XPROJ-7', 'PROJ-4x'):
            with self.subTest(s):
                self.assertFalse(any(n.get('marks') for n in self.inline(s)))

    def test_stray_backticks_keep_every_character_and_no_empty_nodes(self):
        for s in ('`foo bar', '`a``b', 'PROJ-1`x', '`'):
            with self.subTest(s):
                nodes = self.inline(s)
                self.assertTrue(all(n['text'] for n in nodes))
                rendered = ''.join(
                    f'`{n["text"]}`' if n.get('marks') == [{'type': 'code'}] else n['text'] for n in nodes
                )
                self.assertEqual(rendered, s)

    def test_headers_are_bold_and_inline_processed(self):
        doc = adf.build(task(*VALID_TASK), PROJECTS, TASK, BROWSE)
        self.assertIn(('What needs to be done', ['strong']), texts(doc))


class Markdown(unittest.TestCase):
    def test_headers_bold_lists_real_and_blocks_separated(self):
        md = adf.markdown(
            task(
                ('What needs to be done', [{'p': 'Do it.'}]),
                ('Acceptance criteria', [{'ul': ['One.', 'Two.']}, {'ol': ['First.', 'Second.']}]),
                *VALID_TASK[2:],
            ),
            PROJECTS,
            BROWSE,
        )
        self.assertIn('\n\n**What needs to be done**\n\nDo it.\n\n', md)
        self.assertIn('- One.\n- Two.\n\n1. First.\n2. Second.', md)
        self.assertTrue(md.endswith('**Potentially impacted files**\n\n- `src/foo.py`\n'))

    def test_keys_link_and_code_spans_stay_code(self):
        inline = adf.markdown([{'p': 'See PROJ-12 and `a_b*c`.'}], PROJECTS, BROWSE)
        self.assertEqual(inline, f'See [PROJ-12]({BROWSE}PROJ-12) and `a_b*c`.\n')

    def test_plain_text_markup_is_escaped(self):
        for text, want in (
            ('a *b* _c_ [d](e) <f> ~g~ #h | !i \\ j', r'a \*b\* \_c\_ \[d\](e) \<f\> \~g\~ \#h \| \!i \\ j'),
            ('- not a bullet', r'\- not a bullet'),
            ('12. not a list', r'12\. not a list'),
            ('> not a quote', r'\> not a quote'),
            ('`stray backtick', r'\`stray backtick'),
        ):
            with self.subTest(text):
                self.assertEqual(adf.markdown([{'p': text}], PROJECTS, BROWSE), want + '\n')

    def test_newlines_in_text_do_not_start_new_blocks(self):
        self.assertEqual(adf.markdown([{'ul': ['a\n- b']}], PROJECTS, BROWSE), '- a - b\n')


class Cli(unittest.TestCase):
    def run_cli(self, spec, *args, profiles=FIXTURES, profile='Acme'):
        with tempfile.TemporaryDirectory() as out:
            path = os.path.join(out, 'spec.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(spec, f)
            env = dict(os.environ, CLEAN_DOCS_PROFILES=profiles, XDG_CONFIG_HOME=out)
            extra = ('--profile', profile) if profile else ()
            proc = subprocess.run(
                [sys.executable, '-B', SCRIPT, path, out, *args, *extra, '--repo', out],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            outputs = {}
            for name in ('workitem.json', 'description.json', 'description.md'):
                if os.path.exists(os.path.join(out, name)):
                    with open(os.path.join(out, name), encoding='utf-8') as f:
                        outputs[name] = f.read() if name.endswith('.md') else json.load(f)
            return proc, outputs

    def test_writes_workitem_payload(self):
        proc, out = self.run_cli(
            {'summary': 'A summary', 'blocks': task(*VALID_TASK)}, '--type', 'Task', '--project', 'proj'
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        item = out['workitem.json']
        self.assertEqual((item['projectKey'], item['type'], item['summary']), ('PROJ', 'Task', 'A summary'))
        self.assertEqual(item['description'], out['description.json'])
        self.assertTrue(out['description.md'].startswith(adf.URGENCY + '**Nice to have**\n\n**'))
        first = out['description.json']['content'][0]['content']
        self.assertEqual(first[-1], {'type': 'text', 'text': 'Nice to have', 'marks': [{'type': 'strong'}]})
        self.assertEqual(''.join(n['text'] for n in first), adf.URGENCY + 'Nice to have')
        self.assertIn('profile: Acme (forced)', proc.stdout)

    def test_issue_type_name_comes_from_the_profile(self):
        proc, out = self.run_cli(skeleton('research'), '--type', 'research', '--project', 'PROJ')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['workitem.json']['type'], 'Spike')
        self.assertTrue(out['description.md'].startswith(adf.GENERATED + '\n\n**'))
        self.assertNotIn('marks', out['description.json']['content'][0]['content'][0])

    def test_unknown_type_rejected(self):
        proc, out = self.run_cli({'summary': 's', 'blocks': []}, '--type', 'epic', '--project', 'PROJ')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("['bug', 'research', 'story', 'task']", proc.stderr)
        self.assertEqual(out, {})

    def test_fields_pass_through_to_workitem_and_stdout(self):
        fields = {'customfield_10001': {'value': 'Option A'}, 'components': [{'name': 'Billing'}]}
        proc, out = self.run_cli(
            {'summary': 's', 'blocks': task(*VALID_TASK), 'fields': fields}, '--type', 'task', '--project', 'PROJ'
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['workitem.json']['additionalAttributes'], fields)
        self.assertIn('fields: ' + json.dumps(fields), proc.stdout)

    def test_no_fields_means_no_attributes(self):
        proc, out = self.run_cli({'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task', '--project', 'PROJ')
        self.assertNotIn('additionalAttributes', out['workitem.json'])
        self.assertIn('fields: {}', proc.stdout)

    def test_update_writes_edit_payload_and_skips_self_link(self):
        blocks = task(
            *VALID_TASK[:3],
            ('Potentially impacted files', [{'ul': ['`a.py`']}, {'p': 'Overlaps with PROJ-1, PROJ-9.'}]),
        )
        blocks[0] = {'p': adf.UPDATED + adf.URGENCY_SUFFIX + 'Nice to have'}
        proc, out = self.run_cli(
            {'summary': 's', 'blocks': blocks, 'fields': {'customfield_1': {'value': 'X'}}},
            '--type',
            'task',
            '--project',
            'PROJ',
            '--update',
            'proj-9',
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(set(out['workitem.json']), {'issues', 'summary', 'description'})
        self.assertEqual(out['workitem.json']['issues'], ['PROJ-9'])
        self.assertIn("linkable keys: ['PROJ-1']", proc.stdout)

    def test_update_needs_restructured_first_line_and_own_project_key(self):
        spec = {'summary': 's', 'blocks': task(*VALID_TASK)}
        proc, _ = self.run_cli(spec, '--type', 'task', '--project', 'PROJ', '--update', 'PROJ-9')
        self.assertNotEqual(proc.returncode, 0)
        spec['blocks'][0] = {'p': adf.UPDATED + adf.URGENCY_SUFFIX + 'Nice to have'}
        for bad in ('OPS-9', 'PROJ', 'PROJ-9;x'):
            with self.subTest(bad):
                proc, _ = self.run_cli(spec, '--type', 'task', '--project', 'PROJ', '--update', bad)
                self.assertNotEqual(proc.returncode, 0)

    def test_summary_limit_comes_from_the_profile(self):
        ok, _ = self.run_cli({'summary': 'x' * 74, 'blocks': task(*VALID_TASK)}, '--type', 'task', '--project', 'PROJ')
        long, out = self.run_cli(
            {'summary': 'x' * 75, 'blocks': task(*VALID_TASK)}, '--type', 'task', '--project', 'PROJ'
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertNotEqual(long.returncode, 0)
        self.assertEqual(out, {})

    def test_project_key_validated(self):
        for bad in ('PROJ;id', 'PROJ-1', ''):
            with self.subTest(bad):
                proc, _ = self.run_cli(
                    {'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task', '--project', bad
                )
                self.assertNotEqual(proc.returncode, 0)

    def test_projects_add_to_profile_links_and_target(self):
        sections = [
            *VALID_TASK[:3],
            (
                'Potentially impacted files',
                [{'ul': ['`src/foo.py`']}, {'p': 'Overlaps with PROJ-1, OPS-2, ABC-3, NOPE-4.'}],
            ),
        ]
        proc, out = self.run_cli(
            {'summary': 's', 'blocks': task(*sections)}, '--type', 'task', '--project', 'ABC', '--projects', ' ops , '
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        linked = {t for t, marks in texts(out['description.json']) if 'link' in marks}
        self.assertEqual(linked, {'PROJ-1', 'OPS-2', 'ABC-3'})
        self.assertIn("['ABC-3', 'OPS-2', 'PROJ-1']", proc.stdout)
        self.assertIn('https://example.atlassian.net/browse/PROJ-1', out['description.md'])

    def test_ticket_key_in_projects_rejected(self):
        proc, _ = self.run_cli(
            {'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task', '--project', 'PROJ', '--projects', 'ABC-7'
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_profile_without_jira_refused(self):
        proc, out = self.run_cli(
            {'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task', '--project', 'PROJ', profile=None
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('file-ticket needs jira', proc.stderr)
        self.assertEqual(out, {})

    def test_bad_top_level_rejected(self):
        proc, _ = self.run_cli(['x'], '--type', 'task', '--project', 'PROJ')
        self.assertIn('spec error', proc.stderr)


if __name__ == '__main__':
    unittest.main()
