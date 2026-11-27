import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SKILL, 'scripts', 'ticket.py')
FIXTURES = os.path.join(SKILL, 'tests', 'fixtures', 'profiles')
sys.path.insert(0, os.path.dirname(SCRIPT))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(SKILL)), 'scripts'))

import house_style  # noqa: E402
import ticket  # noqa: E402

URGENT = {'p': ticket.URGENCY + 'Nice to have'}
BROWSE = 'https://example.atlassian.net/browse/'
JIRA = ticket.jira(['PROJ'], BROWSE)
GITHUB = ticket.forge('github')
ACME = house_style.load('Acme', os.path.join(FIXTURES, 'Acme'))
DEFAULT = house_style.load('Default', house_style.DEFAULT_DIR, 'github.com/acme/widget')
TASK = ticket.template_for(ACME, 'task')
RESEARCH = ticket.template_for(ACME, 'research')


def build(blocks, template=TASK, tracker=JIRA, update=False):
    ticket.check(blocks, template, tracker, update)
    return ticket.adf(blocks, tracker) if tracker.kind == 'jira' else ticket.markdown(blocks, tracker)


def skeleton(profile, ticket_type):
    with open(profile['tickets']['types'][ticket_type]['template'], encoding='utf-8') as f:
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


def files(*content):
    return task(*VALID_TASK[:3], ('Potentially impacted files', list(content)))


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
    def test_every_skeleton_builds_on_every_tracker(self):
        for name, profile in (('Acme', ACME), ('Default', DEFAULT)):
            for ticket_type in profile['tickets']['types']:
                for tracker in (JIRA, GITHUB, ticket.forge('gitlab')):
                    with self.subTest(profile=name, type=ticket_type, tracker=tracker.kind):
                        build(
                            skeleton(profile, ticket_type)['blocks'], ticket.template_for(profile, ticket_type), tracker
                        )

    def test_table_is_parsed_in_order_with_kinds_and_required(self):
        self.assertEqual(
            TASK.sections,
            [
                ticket.Section('What needs to be done', ('p',), True),
                ticket.Section('Acceptance criteria', ('ul',), True),
                ticket.Section('Out of scope', ('ul',), False),
                ticket.Section('Potentially impacted files', ('files',), True),
            ],
        )
        self.assertEqual(TASK.files, 'Potentially impacted files')

    def test_levels_come_from_the_rubric_and_types_can_opt_out(self):
        self.assertEqual(TASK.levels, ('Critical', 'Blocking', 'Nice to have'))
        self.assertEqual(RESEARCH.levels, ())
        self.assertIsNone(RESEARCH.files)

    def test_default_types_carry_forge_labels(self):
        labels = {k: v['labels'] for k, v in DEFAULT['tickets']['types'].items()}
        self.assertEqual(labels, {'story': ['enhancement'], 'bug': ['bug'], 'task': []})

    def test_bad_tables_rejected(self):
        for text in (
            'no table here',
            '| 1 | A | p | yes | x |\n| 2 | A | p | yes | x |',
            '| 1 | A | files | yes | x |\n| 2 | B | files | yes | x |',
            '| 1 | A | table | yes | x |',
        ):
            with self.subTest(text), self.assertRaises(SystemExit):
                ticket.parse_template(text)

    def test_optional_section_may_be_blank(self):
        build(task(*VALID_TASK))

    def test_required_section_may_not_be_blank(self):
        sections = [(h, [] if h == 'What needs to be done' else c) for h, c in VALID_TASK]
        with self.assertRaises(SystemExit):
            build(task(*sections))

    def test_headers_out_of_order_rejected(self):
        with self.assertRaises(SystemExit):
            build(task(VALID_TASK[1], VALID_TASK[0], *VALID_TASK[2:]))

    def test_block_kind_outside_the_sections_kinds_rejected(self):
        with self.assertRaises(SystemExit):
            build(task(('What needs to be done', [{'ul': ['Do it.']}]), *VALID_TASK[1:]))

    def test_code_span_outside_code_section_rejected(self):
        with self.assertRaises(SystemExit):
            build(task(('What needs to be done', [{'p': 'Call `foo`.'}]), *VALID_TASK[1:]))

    def test_type_without_files_section_rejects_code_spans(self):
        blocks = skeleton(ACME, 'research')['blocks']
        blocks[2] = {'p': 'Because `cache` is slow.'}
        with self.assertRaises(SystemExit):
            build(blocks, RESEARCH)


class FilesSection(unittest.TestCase):
    def test_paths_and_overlaps_accepted_in_each_trackers_form(self):
        build(files({'ul': ['`src/a.py`', '`src/b.py`']}, {'p': 'Overlaps with PROJ-1, PROJ-2.'}))
        build(files({'ul': ['`src/a.py`']}, {'p': 'Overlaps with #1, acme/other#2.'}), tracker=GITHUB)

    def test_other_trackers_reference_form_rejected(self):
        with self.assertRaises(SystemExit):
            build(files({'ul': ['`src/a.py`']}, {'p': 'Overlaps with #1.'}))
        with self.assertRaises(SystemExit):
            build(files({'ul': ['`src/a.py`']}, {'p': 'Overlaps with PROJ-1.'}), tracker=GITHUB)

    def test_not_investigated_line_accepted(self):
        build(files({'p': ticket.NOT_INVESTIGATED}))

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
                build(files(*content))


class Fields(unittest.TestCase):
    def test_custom_select_and_components_accepted(self):
        ticket.check_fields({'customfield_10000': {'value': 'Option'}, 'components': [{'name': 'Billing'}]})

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
                ticket.check_fields(bad)


class FirstLine(unittest.TestCase):
    def test_wrong_urgency_level_rejected(self):
        blocks = task(*VALID_TASK)
        blocks[0] = {'p': ticket.URGENCY + 'High'}
        with self.assertRaises(SystemExit):
            build(blocks)

    def test_type_without_urgency_rejects_urgency_line(self):
        blocks = skeleton(ACME, 'research')['blocks']
        blocks[0] = URGENT
        with self.assertRaises(SystemExit):
            build(blocks, RESEARCH)

    def test_update_line_without_urgency(self):
        blocks = skeleton(ACME, 'research')['blocks']
        blocks[0] = {'p': ticket.UPDATED}
        build(blocks, RESEARCH, update=True)
        with self.assertRaises(SystemExit):
            build(blocks, RESEARCH)

    def test_urgency_type_rejects_plain_line(self):
        blocks = task(*VALID_TASK)
        blocks[0] = {'p': ticket.GENERATED}
        with self.assertRaises(SystemExit):
            build(blocks)


class Blocks(unittest.TestCase):
    def test_malformed_blocks_rejected(self):
        for bad in ({}, {'h': 'x', 'p': 'y'}, {'ul': []}, {'ul': 'abc'}, {'ul': ['']}, {'h': ''}, {'p': 5}, {'q': 'x'}):
            with self.subTest(bad), self.assertRaises(SystemExit):
                ticket.check_block(0, bad)


class JiraInline(unittest.TestCase):
    inline = staticmethod(ticket.make_inline(JIRA))

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
        self.assertIn(('What needs to be done', ['strong']), texts(build(task(*VALID_TASK))))


class Markdown(unittest.TestCase):
    SECTIONS = (
        ('What needs to be done', [{'p': 'Do it.'}]),
        ('Acceptance criteria', [{'ul': ['One.', 'Two.']}, {'ol': ['First.', 'Second.']}]),
        *VALID_TASK[2:],
    )

    def test_jira_headers_bold_lists_real_and_blocks_separated(self):
        md = ticket.markdown(task(*self.SECTIONS), JIRA)
        self.assertIn('\n\n**What needs to be done**\n\nDo it.\n\n', md)
        self.assertIn('- One.\n- Two.\n\n1. First.\n2. Second.', md)
        self.assertTrue(md.endswith('**Potentially impacted files**\n\n- `src/foo.py`\n'))

    def test_forge_headers_are_headings_and_urgency_bold(self):
        md = ticket.markdown(task(*self.SECTIONS), GITHUB)
        self.assertTrue(md.startswith(ticket.URGENCY + '**Nice to have**\n\n### What needs to be done\n\nDo it.\n\n'))
        self.assertTrue(md.endswith('### Potentially impacted files\n\n- `src/foo.py`\n'))

    def test_jira_keys_link_and_code_spans_stay_code(self):
        md = ticket.markdown([{'p': 'See PROJ-12 and `a_b*c`.'}], JIRA)
        self.assertEqual(md, f'See [PROJ-12]({BROWSE}PROJ-12) and `a_b*c`.\n')

    def test_forge_references_stay_bare_for_the_forge_to_link(self):
        for text in ('See #12.', 'See acme/widget#12.', 'See group/sub/app#3.'):
            with self.subTest(text):
                self.assertEqual(ticket.markdown([{'p': text}], GITHUB), text + '\n')

    def test_forge_hash_that_is_not_a_reference_is_escaped(self):
        for text, want in (('# not a heading', r'\# not a heading'), ('C#7 and a#b', r'C\#7 and a\#b')):
            with self.subTest(text):
                self.assertEqual(ticket.markdown([{'p': text}], GITHUB), want + '\n')

    def test_plain_text_markup_is_escaped(self):
        for text, want in (
            ('a *b* _c_ [d](e) <f> ~g~ | !i \\ j', r'a \*b\* \_c\_ \[d\](e) \<f\> \~g\~ \| \!i \\ j'),
            ('- not a bullet', r'\- not a bullet'),
            ('12. not a list', r'12\. not a list'),
            ('> not a quote', r'\> not a quote'),
            ('`stray backtick', r'\`stray backtick'),
        ):
            for tracker in (JIRA, GITHUB):
                with self.subTest(text=text, tracker=tracker.kind):
                    self.assertEqual(ticket.markdown([{'p': text}], tracker), want + '\n')

    def test_newlines_in_text_do_not_start_new_blocks(self):
        self.assertEqual(ticket.markdown([{'ul': ['a\n- b']}], GITHUB), '- a - b\n')


class Cli(unittest.TestCase):
    def run_cli(self, spec, *args, profile='Acme', remote=None, pdir=FIXTURES):
        with tempfile.TemporaryDirectory() as out:
            if remote:
                subprocess.run(['git', 'init', '-q', out], check=True)
                subprocess.run(['git', '-C', out, 'remote', 'add', 'origin', remote], check=True)
            path = os.path.join(out, 'spec.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(spec, f)
            env = dict(os.environ, CLEAN_DOCS_PROFILES=pdir, XDG_CONFIG_HOME=out)
            extra = ('--profile', profile) if profile else ()
            proc = subprocess.run(
                [sys.executable, '-B', SCRIPT, path, out, *args, *extra, '--repo', out],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            outputs = {}
            for name in ('workitem.json', 'description.json', 'description.md', 'title.txt', 'body.md', 'issue.json'):
                if os.path.exists(os.path.join(out, name)):
                    with open(os.path.join(out, name), encoding='utf-8') as f:
                        outputs[name] = json.load(f) if name.endswith('.json') else f.read()
            return proc, outputs

    # Jira

    def test_jira_writes_workitem_payload(self):
        proc, out = self.run_cli(
            {'summary': 'A summary', 'blocks': task(*VALID_TASK)}, '--type', 'Task', '--project', 'proj'
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        item = out['workitem.json']
        self.assertEqual((item['projectKey'], item['type'], item['summary']), ('PROJ', 'Task', 'A summary'))
        self.assertEqual(item['description'], out['description.json'])
        self.assertTrue(out['description.md'].startswith(ticket.URGENCY + '**Nice to have**\n\n**'))
        first = out['description.json']['content'][0]['content']
        self.assertEqual(first[-1], {'type': 'text', 'text': 'Nice to have', 'marks': [{'type': 'strong'}]})
        self.assertIn('profile: Acme (forced), tracker: jira', proc.stdout)
        self.assertNotIn('body.md', out)

    def test_jira_issue_type_name_comes_from_the_profile(self):
        proc, out = self.run_cli(skeleton(ACME, 'research'), '--type', 'research', '--project', 'PROJ')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['workitem.json']['type'], 'Spike')
        self.assertTrue(out['description.md'].startswith(ticket.GENERATED + '\n\n**'))

    def test_jira_needs_a_project(self):
        proc, out = self.run_cli({'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(out, {})

    def test_unknown_type_rejected(self):
        proc, out = self.run_cli({'summary': 's', 'blocks': []}, '--type', 'epic', '--project', 'PROJ')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("['bug', 'research', 'story', 'task']", proc.stderr)
        self.assertEqual(out, {})

    def test_jira_fields_pass_through_to_workitem_and_stdout(self):
        fields = {'customfield_10001': {'value': 'Option A'}, 'components': [{'name': 'Billing'}]}
        proc, out = self.run_cli(
            {'summary': 's', 'blocks': task(*VALID_TASK), 'fields': fields}, '--type', 'task', '--project', 'PROJ'
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['workitem.json']['additionalAttributes'], fields)
        self.assertIn('fields: ' + json.dumps(fields), proc.stdout)

    def test_jira_update_writes_edit_payload_and_skips_self_link(self):
        blocks = files({'ul': ['`a.py`']}, {'p': 'Overlaps with PROJ-1, PROJ-9.'})
        blocks[0] = {'p': ticket.UPDATED + ticket.URGENCY_SUFFIX + 'Nice to have'}
        proc, out = self.run_cli(
            {'summary': 's', 'blocks': blocks}, '--type', 'task', '--project', 'PROJ', '--update', 'proj-9'
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(set(out['workitem.json']), {'issues', 'summary', 'description'})
        self.assertEqual(out['workitem.json']['issues'], ['PROJ-9'])
        self.assertIn("linkable keys: ['PROJ-1']", proc.stdout)

    def test_jira_update_needs_restructured_first_line_and_own_project_key(self):
        spec = {'summary': 's', 'blocks': task(*VALID_TASK)}
        proc, _ = self.run_cli(spec, '--type', 'task', '--project', 'PROJ', '--update', 'PROJ-9')
        self.assertNotEqual(proc.returncode, 0)
        spec['blocks'][0] = {'p': ticket.UPDATED + ticket.URGENCY_SUFFIX + 'Nice to have'}
        for bad in ('OPS-9', 'PROJ', 'PROJ-9;x', '12'):
            with self.subTest(bad):
                proc, _ = self.run_cli(spec, '--type', 'task', '--project', 'PROJ', '--update', bad)
                self.assertNotEqual(proc.returncode, 0)

    def test_jira_projects_add_to_profile_links_and_target(self):
        blocks = files({'ul': ['`src/foo.py`']}, {'p': 'Overlaps with PROJ-1, OPS-2, ABC-3, NOPE-4.'})
        proc, out = self.run_cli(
            {'summary': 's', 'blocks': blocks}, '--type', 'task', '--project', 'ABC', '--projects', ' ops , '
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        linked = {t for t, marks in texts(out['description.json']) if 'link' in marks}
        self.assertEqual(linked, {'PROJ-1', 'OPS-2', 'ABC-3'})
        self.assertIn("['ABC-3', 'OPS-2', 'PROJ-1']", proc.stdout)

    def test_jira_project_key_validated(self):
        for bad in ('PROJ;id', 'PROJ-1'):
            with self.subTest(bad):
                proc, _ = self.run_cli(
                    {'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task', '--project', bad
                )
                self.assertNotEqual(proc.returncode, 0)
        proc, _ = self.run_cli(
            {'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task', '--project', 'PROJ', '--projects', 'ABC-7'
        )
        self.assertNotEqual(proc.returncode, 0)

    # GitHub / GitLab

    def test_github_writes_title_body_and_labels(self):
        blocks = files({'ul': ['`src/a.py`']}, {'p': 'Overlaps with #4, acme/other#5.'})
        proc, out = self.run_cli({'summary': 'A summary', 'blocks': blocks}, '--type', 'task', profile='Hub')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['title.txt'], 'A summary')
        self.assertEqual(out['issue.json'], {'title': 'A summary', 'labels': [], 'update': None, 'repo': None})
        self.assertIn("destination: this repo's origin", proc.stdout)
        self.assertIn('### What needs to be done', out['body.md'])
        self.assertIn('Overlaps with #4, acme/other#5.', out['body.md'])
        self.assertIn('tracker: github', proc.stdout)
        self.assertIn("references: ['#4', 'acme/other#5']", proc.stdout)
        self.assertNotIn('workitem.json', out)

    def test_github_story_gets_the_types_labels(self):
        proc, out = self.run_cli(skeleton(DEFAULT, 'story'), '--type', 'story', profile='Hub')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['issue.json']['labels'], ['enhancement'])
        self.assertIn('labels: ["enhancement"]', proc.stdout)

    def test_github_update_takes_an_issue_number(self):
        blocks = task(*VALID_TASK)
        blocks[0] = {'p': ticket.UPDATED + ticket.URGENCY_SUFFIX + 'Nice to have'}
        for ref in ('12', '#12'):
            with self.subTest(ref):
                proc, out = self.run_cli(
                    {'summary': 's', 'blocks': blocks}, '--type', 'task', '--update', ref, profile='Hub'
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(out['issue.json']['update'], 12)
        proc, _ = self.run_cli({'summary': 's', 'blocks': blocks}, '--type', 'task', '--update', 'ABC-1', profile='Hub')
        self.assertNotEqual(proc.returncode, 0)

    def confirms(self, stdout):
        return [line for line in stdout.splitlines() if line.startswith('confirm:')]

    def test_jira_project_defaults_to_the_profiles_without_asking(self):
        spec = {'summary': 's', 'blocks': task(*VALID_TASK)}
        proc, out = self.run_cli(spec, '--type', 'task', profile='Acme/Ops')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['workitem.json']['projectKey'], 'OPS')
        self.assertIn('profile: Acme/Ops (forced)', proc.stdout)
        self.assertIn('destination: project OPS (from profile Acme/Ops)', proc.stdout)
        self.assertEqual(self.confirms(proc.stdout), [])
        self.assertNotIn('repo-flag:', proc.stdout)

    def test_explicit_project_replacing_the_profiles_is_asked(self):
        spec = {'summary': 's', 'blocks': task(*VALID_TASK)}
        proc, out = self.run_cli(spec, '--type', 'task', '--project', 'PROJ', profile='Acme/Ops')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['workitem.json']['projectKey'], 'PROJ')
        self.assertIn('destination: project PROJ (from --project)', proc.stdout)
        self.assertEqual(
            self.confirms(proc.stdout), ["confirm: project PROJ (from --project), replacing the profile's project OPS"]
        )
        for args, profile in ((('--project', 'PROJ'), 'Acme'), (('--project', 'OPS'), 'Acme/Ops')):
            with self.subTest(args):
                proc, _ = self.run_cli(spec, '--type', 'task', *args, profile=profile)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(f'destination: project {args[1]} (from --project)', proc.stdout)
                self.assertEqual(self.confirms(proc.stdout), [])

    def test_inferred_destinations_are_always_asked(self):
        spec = {'summary': 's', 'blocks': task(*VALID_TASK)}
        proc, _ = self.run_cli(spec, '--type', 'task', '--project', 'PROJ', '--inferred')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('destination: project PROJ (from inferred from the repo)', proc.stdout)
        self.assertEqual(
            self.confirms(proc.stdout), ['confirm: project PROJ was inferred from the repo, not named by you']
        )
        proc, _ = self.run_cli(spec, '--type', 'task', '--project', 'PROJ', '--inferred', profile='Acme/Ops')
        self.assertEqual(
            self.confirms(proc.stdout),
            ["confirm: project PROJ was inferred from the repo, not named by you, replacing the profile's project OPS"],
        )
        proc, out = self.run_cli(
            spec,
            '--type',
            'task',
            '--forge-repo',
            'acme/other',
            '--inferred',
            profile='Hub',
            remote='git@github.com:acme/widget.git',
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['issue.json']['repo'], 'acme/other')
        self.assertEqual(
            self.confirms(proc.stdout),
            ['confirm: the issue goes to acme/other (inferred from the repo), not this repo ("acme/widget")'],
        )
        proc, _ = self.run_cli(spec, '--type', 'task', '--inferred', profile='Hub')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('--inferred describes', proc.stderr)

    def test_empty_destination_options_rejected(self):
        spec = {'summary': 's', 'blocks': task(*VALID_TASK)}
        cases = (
            (('--project', ''), 'Acme/Ops'),
            (('--forge-repo', ' '), 'Hub'),
            (('--update', ''), 'Acme/Ops'),
            (('--update', ' '), 'Hub'),
        )
        for args, profile in cases:
            with self.subTest(args):
                proc, out = self.run_cli(spec, '--type', 'task', *args, profile=profile)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(f'{args[0]} is empty', proc.stderr)
                self.assertEqual(out, {})

    def test_jira_without_any_project_says_how_to_set_one(self):
        proc, out = self.run_cli({'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('tracker.project', proc.stderr)

    def test_jira_update_takes_its_project_from_the_key_without_asking(self):
        blocks = task(*VALID_TASK)
        blocks[0] = {'p': ticket.UPDATED + ticket.URGENCY_SUFFIX + 'Nice to have'}
        spec = {'summary': 's', 'blocks': blocks}
        for key, project in (('OPS-7', 'OPS'), ('proj-7', 'PROJ')):
            with self.subTest(key):
                proc, out = self.run_cli(spec, '--type', 'task', '--update', key, profile='Acme/Ops')
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(out['workitem.json']['issues'], [key.upper()])
                self.assertIn(f'destination: project {project} (from --update {key.upper()})', proc.stdout)
                self.assertEqual(self.confirms(proc.stdout), [])
        proc, _ = self.run_cli(spec, '--type', 'task', '--update', 'PROJ-7', '--project', 'OPS', profile='Acme/Ops')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('--update PROJ-7 is not in project OPS (from --project)', proc.stderr)

    def test_forge_repo_from_the_profile_is_the_destination(self):
        blocks = files({'ul': ['`src/a.py`']}, {'p': 'Overlaps with #4.'})
        spec = {'summary': 'A summary', 'blocks': blocks}
        proc, out = self.run_cli(spec, '--type', 'task', profile='Hub/Issues')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['issue.json']['repo'], 'acme/issues')
        self.assertIn('destination: acme/issues (from profile Hub/Issues)', proc.stdout)
        self.assertIn('repo-flag: -R "acme/issues"', proc.stdout)
        self.assertEqual(
            self.confirms(proc.stdout),
            ['confirm: the issue goes to acme/issues (profile Hub/Issues); this directory has no origin to compare'],
        )
        cases = (
            (
                'git@github.com:acme/widget.git',
                ['confirm: the issue goes to acme/issues (profile Hub/Issues), not this repo ("acme/widget")'],
            ),
            ('git@github.com:Acme/Issues.git', []),
            (
                'https://gitlab.example.com/acme/issues.git',
                [
                    'confirm: this profile files github issues, '
                    'but this repo\'s origin is on "gitlab.example.com" (gitlab)'
                ],
            ),
            (
                'git@ghe.corp.example:acme/issues.git',
                [
                    'confirm: this profile files github issues, '
                    'but this repo\'s origin is on "ghe.corp.example" (not a known github host)'
                ],
            ),
        )
        for remote, expected in cases:
            with self.subTest(remote):
                proc, _ = self.run_cli(spec, '--type', 'task', profile='Hub/Issues', remote=remote)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self.confirms(proc.stdout), expected)

    def test_origin_destination_has_no_repo_flag(self):
        spec = {'summary': 'A summary', 'blocks': task(*VALID_TASK)}
        proc, out = self.run_cli(spec, '--type', 'task', profile='Hub', remote='git@github.com:acme/widget.git')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(out['issue.json']['repo'])
        self.assertIn("destination: this repo's origin", proc.stdout)
        self.assertIn('repo-flag: none', proc.stdout)
        self.assertEqual(self.confirms(proc.stdout), [])

    def test_forge_repo_named_by_the_user(self):
        spec = {'summary': 'A summary', 'blocks': task(*VALID_TASK)}
        proc, out = self.run_cli(
            spec,
            '--type',
            'task',
            '--forge-repo',
            'acme/other',
            profile='Hub/Issues',
            remote='git@github.com:acme/widget.git',
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['issue.json']['repo'], 'acme/other')
        self.assertIn('destination: acme/other (from --forge-repo)', proc.stdout)
        self.assertIn('repo-flag: -R "acme/other"', proc.stdout)
        self.assertEqual(self.confirms(proc.stdout), [])
        for bad in ('evil.example.com/acme/x', 'acme/x.git', '$(id)/x', '-R/x', 'acme/sub/x', 'acme', 'a"b/c'):
            with self.subTest(bad):
                proc, out = self.run_cli(spec, '--type', 'task', f'--forge-repo={bad}', profile='Hub')
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('--forge-repo must be', proc.stderr)
                self.assertEqual(out, {})
        proc, _ = self.run_cli(spec, '--type', 'task', '--project', 'PROJ', '--forge-repo', 'acme/x')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('--forge-repo is for GitHub and GitLab', proc.stderr)

    def test_gitlab_subgroups_ports_and_case(self):
        spec = {'summary': 'A summary', 'blocks': task(*VALID_TASK)}
        cases = (
            ('https://gitlab.example.com/group/sub/proj.git', False),
            ('ssh://git@gitlab.example.com:2222/group/sub/proj.git', False),
            ('git@gitlab.example.com:Group/Sub/Proj.git', False),
            ('git@gitlab.example.com:group/other.git', True),
        )
        for remote, asks in cases:
            with self.subTest(remote):
                proc, out = self.run_cli(spec, '--type', 'task', profile='Lab/Sub', remote=remote)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(out['issue.json']['repo'], 'group/sub/proj')
                self.assertEqual(bool(self.confirms(proc.stdout)), asks)

    def test_check_destination_prints_the_lines_without_a_spec(self):
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(['git', 'init', '-q', repo], check=True)
            subprocess.run(['git', '-C', repo, 'remote', 'add', 'origin', 'git@github.com:acme/widget.git'], check=True)
            env = dict(os.environ, CLEAN_DOCS_PROFILES=FIXTURES, XDG_CONFIG_HOME=repo)
            proc = subprocess.run(
                [sys.executable, '-B', SCRIPT, '--check-destination', '--profile', 'Hub/Issues', '--repo', repo],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                proc.stdout.splitlines(),
                [
                    'profile: Hub/Issues (forced), tracker: github',
                    'destination: acme/issues (from profile Hub/Issues)',
                    'confirm: the issue goes to acme/issues (profile Hub/Issues), not this repo ("acme/widget")',
                    'repo-flag: -R "acme/issues"',
                ],
            )
            self.assertEqual(os.listdir(repo), ['.git'])
            proc = subprocess.run(
                [sys.executable, '-B', SCRIPT, '--profile', 'Hub', '--repo', repo],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('required unless --check-destination', proc.stderr)

    def test_origin_is_printed_as_quoted_data(self):
        spec = {'summary': 'A summary', 'blocks': task(*VALID_TASK)}
        remote = 'git@github.com:acme/we"ird.git'
        proc, _ = self.run_cli(spec, '--type', 'task', profile='Hub/Issues', remote=remote)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('not this repo ("acme/we\\"ird")', proc.stdout)
        self.assertEqual(len(self.confirms(proc.stdout)), 1)

    def test_warnings_are_printed_as_quoted_data(self):
        with tempfile.TemporaryDirectory() as pdir:
            os.makedirs(os.path.join(pdir, 'Broken'))
            with open(os.path.join(pdir, 'Broken', 'profile.json'), 'w', encoding='utf-8') as f:
                f.write('{"schema": 1,\n"confirm: ok"')
            proc, _ = self.run_cli(
                {'summary': 's', 'blocks': task(*VALID_TASK)},
                '--type',
                'task',
                profile=None,
                pdir=pdir,
                remote='git@github.com:acme/widget.git',
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        warnings = [line for line in proc.stdout.splitlines() if line.startswith('warning:')]
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith('warning: "skipped profile Broken'))
        self.assertEqual(self.confirms(proc.stdout), [])

    def test_invalid_profile_project_is_a_profile_error(self):
        with tempfile.TemporaryDirectory() as pdir:
            os.makedirs(os.path.join(pdir, 'Bad'))
            with open(os.path.join(pdir, 'Bad', 'profile.json'), 'w', encoding='utf-8') as f:
                json.dump(
                    {
                        'schema': 1,
                        'tracker': {'kind': 'jira', 'site': 'https://example.atlassian.net', 'project': 'ops'},
                    },
                    f,
                )
            proc, out = self.run_cli(
                {'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task', profile='Bad', pdir=pdir
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('profile error', proc.stderr)
        self.assertIn('tracker.project', proc.stderr)
        self.assertEqual(out, {})

    def test_a_failed_write_prints_no_destination(self):
        import contextlib
        import io
        from unittest import mock

        with tempfile.TemporaryDirectory() as out:
            spec = os.path.join(out, 'spec.json')
            with open(spec, 'w', encoding='utf-8') as f:
                json.dump({'summary': 'A summary', 'blocks': task(*VALID_TASK)}, f)
            argv = [
                'ticket.py',
                spec,
                os.path.join(out, 'o'),
                '--type',
                'task',
                '--profile',
                'Hub/Issues',
                '--repo',
                out,
            ]
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {'CLEAN_DOCS_PROFILES': FIXTURES, 'XDG_CONFIG_HOME': out}),
                mock.patch.object(sys, 'argv', argv),
                mock.patch.object(ticket, 'write', side_effect=OSError('disk full')),
                contextlib.redirect_stdout(stdout),
                self.assertRaises(OSError),
            ):
                ticket.main()
        self.assertEqual(stdout.getvalue(), '')

    def test_github_refuses_jira_only_options(self):
        spec = {'summary': 's', 'blocks': task(*VALID_TASK)}
        for args in (('--project', 'PROJ'), ('--projects', 'OPS')):
            with self.subTest(args):
                proc, out = self.run_cli(spec, '--type', 'task', *args, profile='Hub')
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('Jira-only', proc.stderr)
        proc, out = self.run_cli(
            dict(spec, fields={'components': [{'name': 'Billing'}]}), '--type', 'task', profile='Hub'
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(out, {})

    # Shared

    def test_summary_limit_comes_from_the_profile(self):
        for profile, extra in (('Acme', ('--project', 'PROJ')), ('Hub', ())):
            with self.subTest(profile):
                ok, _ = self.run_cli(
                    {'summary': 'x' * 74, 'blocks': task(*VALID_TASK)}, '--type', 'task', *extra, profile=profile
                )
                long, out = self.run_cli(
                    {'summary': 'x' * 75, 'blocks': task(*VALID_TASK)}, '--type', 'task', *extra, profile=profile
                )
                self.assertEqual(ok.returncode, 0, ok.stderr)
                self.assertNotEqual(long.returncode, 0)
                self.assertEqual(out, {})

    def test_multiline_summary_rejected(self):
        proc, _ = self.run_cli({'summary': 'a\nb', 'blocks': task(*VALID_TASK)}, '--type', 'task', profile='Hub')
        self.assertNotEqual(proc.returncode, 0)

    def test_profile_without_a_tracker_refused(self):
        proc, out = self.run_cli({'summary': 's', 'blocks': task(*VALID_TASK)}, '--type', 'task', profile=None)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('needs jira, github or gitlab', proc.stderr)
        self.assertEqual(out, {})

    def test_bad_top_level_rejected(self):
        proc, _ = self.run_cli(['x'], '--type', 'task', profile='Hub')
        self.assertIn('spec error', proc.stderr)


class Hardening(unittest.TestCase):
    """Regression tests for the review-cycle findings."""

    def test_leading_slash_cannot_become_a_gitlab_quick_action(self):
        gitlab = ticket.forge('gitlab')
        for blocks, want in (
            ([{'p': '/close'}], '\\/close\n'),
            ([{'p': '  /assign me'}], '  \\/assign me\n'),
            ([{'p': 'text\r/close'}], 'text /close\n'),
        ):
            with self.subTest(blocks):
                md = ticket.markdown(blocks, gitlab)
                self.assertEqual(md, want)
                self.assertNotRegex(md, r'(?m)^\s*/')

    def test_code_span_cannot_span_lines_or_hold_html(self):
        for item in ('`a.py\n/close`', '`a.py\n\n<img src=//x>`', '`a.py\r/close`', '`<img src=x>`'):
            with self.subTest(item), self.assertRaises(SystemExit):
                build(files({'ul': [item]}), tracker=ticket.forge('gitlab'))

    def test_code_fences_rejected(self):
        for text in ('```\nlog\n```', '~~~ trace'):
            with self.subTest(text), self.assertRaises(SystemExit):
                build(task(('What needs to be done', [{'p': text}]), *VALID_TASK[1:]))

    def test_mentions_rejected_on_forges_only(self):
        for text in ('Ask @alice.', 'cc @org/team', '@all please'):
            for tracker in (GITHUB, ticket.forge('gitlab')):
                with self.subTest(text=text, tracker=tracker.kind), self.assertRaises(SystemExit):
                    build(task(('What needs to be done', [{'p': text}]), *VALID_TASK[1:]), tracker=tracker)
            build(task(('What needs to be done', [{'p': text}]), *VALID_TASK[1:]), tracker=JIRA)
        for text in ('Mail ops@example.com.', 'Use `@decorator` style.'):
            with self.subTest(text):
                sections = [('What needs to be done', [{'p': text.replace('`@decorator` ', '')}]), *VALID_TASK[1:]]
                build(task(*sections), tracker=GITHUB)

    def test_mention_in_first_line_rejected(self):
        blocks = task(*VALID_TASK)
        blocks[0] = {'p': ticket.URGENCY + 'Nice to have @x'}
        with self.assertRaises(SystemExit):
            build(blocks, tracker=GITHUB)

    def test_malformed_template_rows_fail_loudly(self):
        for text in (
            '| 1 | A | p | yes | x |\n| 2 | B | p | maybe | x |',
            '| 1 | A | p | yes | x |\n| 2 | B |',
            '| 1 | A | p, ul | yes | x |',
        ):
            with self.subTest(text), self.assertRaises(SystemExit):
                ticket.parse_template(text)

    def test_case_and_spacing_in_rows_are_tolerated(self):
        got = ticket.parse_template('| 1 | A | P / UL | Yes | x |\n| 2 | B | files | NO | x |')
        self.assertEqual(got, [ticket.Section('A', ('p', 'ul'), True), ticket.Section('B', ('files',), False)])

    def test_level_headings_accept_dashes_and_fail_loudly_otherwise(self):
        self.assertEqual(
            ticket.parse_levels('## 1. A — x\n## 2. B – y\n## 3. C - z\n## 4. D -- w'), ('A', 'B', 'C', 'D')
        )
        with self.assertRaises(SystemExit):
            ticket.parse_levels('## 1. A — x\n## 2. B: no dash')

    def test_references_match_what_renders(self):
        blocks = files({'ul': ['`src/PROJ-9.py`']}, {'p': 'Overlaps with PROJ-1.'})
        blocks[2] = {'p': 'See\u2014PROJ-2 and PROJ-3.'}
        self.assertEqual(ticket.references(blocks, JIRA), ['PROJ-1', 'PROJ-2', 'PROJ-3'])
        md = ticket.markdown(blocks, JIRA)
        for key in ('PROJ-1', 'PROJ-2', 'PROJ-3'):
            self.assertIn(f'[{key}]({BROWSE}{key})', md)
        self.assertIn('`src/PROJ-9.py`', md)

    def test_line_breaks_collapse_the_same_in_both_outputs(self):
        text = 'one\r\ntwo\rthree\u2028four'
        self.assertEqual(ticket.markdown([{'p': text}], JIRA), 'one two three four\n')
        nodes = texts(ticket.adf([{'p': text}], JIRA))
        self.assertEqual(''.join(t for t, _ in nodes), 'one two three four')

    def test_urgency_level_is_escaped_in_bold(self):
        template = ticket.Template(TASK.sections, TASK.files, ('Do *now*',))
        blocks = task(*VALID_TASK)
        blocks[0] = {'p': ticket.URGENCY + 'Do *now*'}
        ticket.check(blocks, template, JIRA)
        self.assertTrue(ticket.markdown(blocks, JIRA).startswith(ticket.URGENCY + '**Do \\*now\\***'))


class OutputDir(unittest.TestCase):
    def run_cli(self, spec, out, *args, profile='Hub'):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'spec.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(spec, f)
            env = dict(os.environ, CLEAN_DOCS_PROFILES=FIXTURES, XDG_CONFIG_HOME=tmp)
            return subprocess.run(
                [sys.executable, '-B', SCRIPT, path, out, *args, '--profile', profile, '--repo', tmp],
                capture_output=True,
                text=True,
                env=env,
            )

    def test_creates_the_dir_and_clears_the_other_trackers_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'new', 'dir')
            spec = {'summary': 's', 'blocks': task(*VALID_TASK)}
            jira_run = self.run_cli(spec, out, '--type', 'task', '--project', 'PROJ', profile='Acme')
            self.assertEqual(jira_run.returncode, 0, jira_run.stderr)
            self.assertTrue(os.path.exists(os.path.join(out, 'workitem.json')))
            forge_run = self.run_cli(spec, out, '--type', 'task')
            self.assertEqual(forge_run.returncode, 0, forge_run.stderr)
            self.assertEqual(sorted(os.listdir(out)), ['body.md', 'issue.json', 'title.txt'])

    def test_summary_with_any_line_break_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for summary in ('a\rb', 'a\u2028b', 'a\x00b'):
                with self.subTest(summary=repr(summary)):
                    proc = self.run_cli({'summary': summary, 'blocks': task(*VALID_TASK)}, tmp, '--type', 'task')
                    self.assertNotEqual(proc.returncode, 0)

    def test_jira_update_with_fields_says_they_need_edit(self):
        blocks = task(*VALID_TASK)
        blocks[0] = {'p': ticket.UPDATED + ticket.URGENCY_SUFFIX + 'Nice to have'}
        spec = {'summary': 's', 'blocks': blocks, 'fields': {'components': [{'name': 'Billing'}]}}
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_cli(spec, tmp, '--type', 'task', '--project', 'PROJ', '--update', 'PROJ-9', profile='Acme')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('set them with editJiraIssue', proc.stdout)


if __name__ == '__main__':
    unittest.main()
