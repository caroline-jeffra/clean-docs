"""Per-repo overrides inside a profile, and the tracker.project / tracker.repo fields."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from test_house_style import SCRIPT, hs, write


class ValidateOverrides(unittest.TestCase):
    def errors(self, data, files=()):
        with tempfile.TemporaryDirectory() as folder:
            for name in files:
                write(folder, name, '# t\n')
            return hs.validate({'schema': 1, **data}, 'X/profile.json', folder)

    def test_valid_overrides_accepted(self):
        data = {
            'tracker': {'kind': 'jira', 'site': 'https://example.atlassian.net', 'project': 'ORG'},
            'overrides': {
                'Gadget': {'match': {'repos': ['gadget']}, 'tracker': {'project': 'GDG'}},
                'Web': {
                    'match': {'remotes': ['gitlab.com/acme/web']},
                    'tickets': {'types': {'task': None, 'bug': {'template': 'BugWeb.md'}}},
                    'on_ship': {'transition': 'In Review'},
                },
            },
        }
        self.assertEqual(self.errors(data, files=['BugWeb.md']), [])

    def test_override_rejections(self):
        ok_match = {'repos': ['widget']}
        cases = {
            'not an object': {'overrides': []},
            'null overrides': {'overrides': None},
            'lowercase name': {'overrides': {'gadget': {'match': ok_match}}},
            'null override': {'overrides': {'Gadget': None}},
            'override not an object': {'overrides': {'Gadget': 'x'}},
            'missing match': {'overrides': {'Gadget': {'tracker': {'project': 'GDG'}}}},
            'empty match': {'overrides': {'Gadget': {'match': {}}}},
            'empty match lists': {'overrides': {'Gadget': {'match': {'repos': [], 'remotes': []}}}},
            'schema inside': {'overrides': {'Gadget': {'match': ok_match, 'schema': 1}}},
            'nested overrides': {'overrides': {'Gadget': {'match': ok_match, 'overrides': {}}}},
            'unknown key': {'overrides': {'Gadget': {'match': ok_match, 'color': 'red'}}},
            'bad nested value': {'overrides': {'Gadget': {'match': ok_match, 'tracker': {'kind': 'trello'}}}},
            'null where not nullable': {'overrides': {'Gadget': {'match': ok_match, 'tickets': {'max_summary': None}}}},
            'missing template': {'overrides': {'Gadget': {'match': ok_match, 'request': {'template': 'Nope.md'}}}},
            'null match': {'overrides': {'Gadget': {'match': None}}},
            'match repos not a list': {'overrides': {'Gadget': {'match': {'repos': 'gadget'}}}},
            'match repos not strings': {'overrides': {'Gadget': {'match': {'repos': [1]}}}},
            'match not an object': {'overrides': {'Gadget': {'match': ['gadget']}}},
            'tracker not an object': {'overrides': {'Gadget': {'match': ok_match, 'tracker': 'jira'}}},
            'catch-all glob': {'overrides': {'Gadget': {'match': {'remotes': ['*']}}}},
            'catch-all double glob': {'overrides': {'Gadget': {'match': {'remotes': ['**', 'gitlab.com/a/b']}}}},
            'catch-all with slashes': {'overrides': {'Gadget': {'match': {'remotes': ['*/*']}}}},
            'catch-all with a dot': {'overrides': {'Gadget': {'match': {'remotes': ['*.*']}}}},
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertTrue(self.errors(data), label)

    def test_non_objects_are_messages_not_tracebacks(self):
        for data in ([], 'x', None):
            with self.subTest(data):
                self.assertTrue(hs.validate(data, 'X', hs.DEFAULT_DIR))
        self.assertTrue(self.errors({'schema': True}))

    def test_override_errors_name_the_override(self):
        errors = self.errors({'overrides': {'Gadget': {'match': {'repos': ['b']}, 'tracker': {'kind': 'trello'}}}})
        self.assertTrue(errors)
        self.assertIn('overrides.Gadget', errors[0])

    def test_tracker_project_and_repo_shapes(self):
        self.assertEqual(self.errors({'tracker': {'project': 'GDG'}}), [])
        self.assertEqual(self.errors({'tracker': {'repo': 'acme/widget'}}), [])
        self.assertEqual(self.errors({'tracker': {'repo': 'group/sub.group/my_repo-2'}}), [])
        self.assertEqual(self.errors({'tracker': {'repo': 'my.group/proj'}}), [])
        self.assertEqual(self.errors({'overrides': {'Web': {'match': {'remotes': ['github.com/acme/*']}}}}), [])
        self.assertEqual(self.errors({'tracker': {'project': None, 'repo': None}}), [])
        for tracker in (
            {'project': 'gadget'},
            {'project': 'GDG-1'},
            {'project': ['GDG']},
            {'repo': 'widget'},
            {'repo': 'acme/../widget'},
            {'repo': '/acme/widget'},
            {'repo': 'acme/widget/'},
            {'repo': 'acme/wid get'},
            {'repo': '-R/x'},
            {'repo': 'evil.example.com/team/issues'},
            {'repo': 'acme/widget.git'},
            {'repo': 'acme/widget.GIT'},
            {'repo': 'acme/foo.git/bar'},
            {'repo': 'acme.io/platform/issues'},
        ):
            with self.subTest(tracker):
                self.assertTrue(self.errors({'tracker': tracker}))


JIRA = {'kind': 'jira', 'site': 'https://example.atlassian.net'}


class ResolveOverrides(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pdir = os.path.join(self.tmp.name, 'profiles')
        self.repo = os.path.join(self.tmp.name, 'gadget')
        os.makedirs(self.repo)
        subprocess.run(['git', 'init', '-q', self.repo], check=True)
        subprocess.run(
            ['git', '-C', self.repo, 'remote', 'add', 'origin', 'git@gitlab.com:acme/gadget.git'], check=True
        )
        self.env = {'CLEAN_DOCS_PROFILES': self.pdir}

    def profile(self, name, **data):
        write(os.path.join(self.pdir, name), 'profile.json', {'schema': 1, **data})

    def resolve(self, force=None, repo=None):
        return hs.resolve(repo or self.repo, force, self.env)

    def org(self, **extra):
        self.profile(
            'Org',
            match={'remotes': ['gitlab.com/acme/*']},
            tracker=dict(JIRA, link_projects=['OPS']),
            overrides={
                'Gadget': {'match': {'repos': ['gadget']}, 'tracker': {'project': 'GDG'}},
                'Web': {'match': {'repos': ['web']}, 'tracker': {'project': 'WEB'}},
            },
            **extra,
        )

    def test_override_by_repo_name_applies_under_a_remote_glob_profile(self):
        self.org()
        got = self.resolve()
        r = got['resolved']
        self.assertEqual((r['name'], r['from'], r['override'], r['override_from']), ('Org', 'repos', 'Gadget', 'repos'))
        self.assertEqual(got['tracker']['project'], 'GDG')
        self.assertEqual(got['tracker']['link_projects'], ['OPS', 'GDG'])

    def test_no_matching_override_gives_the_profile_alone(self):
        self.profile(
            'Org',
            match={'remotes': ['gitlab.com/acme/*']},
            tracker=JIRA,
            overrides={
                'Web': {'match': {'repos': ['web']}, 'tracker': {'project': 'WEB'}},
            },
        )
        got = self.resolve()
        self.assertEqual((got['resolved']['override'], got['resolved']['override_from']), (None, None))
        self.assertNotIn('project', got['tracker'])

    def test_profile_matching_only_through_an_override_is_chosen(self):
        self.profile('Org', tracker=JIRA, overrides={'Gadget': {'match': {'repos': ['gadget']}}})
        got = self.resolve()
        self.assertEqual(
            (got['resolved']['name'], got['resolved']['from'], got['resolved']['override']), ('Org', 'repos', 'Gadget')
        )

    def test_override_repo_name_beats_another_profiles_glob(self):
        self.profile('Narrow', match={'remotes': ['gitlab.com/acme/gadget']})
        self.profile('Org', tracker=JIRA, overrides={'Gadget': {'match': {'repos': ['gadget']}}})
        self.assertEqual(self.resolve()['resolved']['name'], 'Org')

    def test_override_applies_even_when_the_profile_match_is_stronger(self):
        self.profile(
            'Org',
            match={'repos': ['gadget']},
            tracker=JIRA,
            overrides={
                'Acme': {'match': {'remotes': ['gitlab.com/acme/*']}, 'tracker': {'project': 'ACME'}},
            },
        )
        got = self.resolve()
        self.assertEqual((got['resolved']['override'], got['resolved']['override_from']), ('Acme', 'remote'))

    def test_best_override_wins_and_a_tie_is_ambiguous(self):
        self.profile(
            'Org',
            tracker=JIRA,
            overrides={
                'Broad': {'match': {'remotes': ['gitlab.com/*']}},
                'Narrow': {'match': {'remotes': ['gitlab.com/acme/*']}},
            },
        )
        self.assertEqual(self.resolve()['resolved']['override'], 'Narrow')
        self.profile(
            'Org',
            tracker=JIRA,
            overrides={
                'One': {'match': {'repos': ['gadget']}},
                'Two': {'match': {'repos': ['GADGET']}},
            },
        )
        got = self.resolve()
        self.assertEqual(got['resolved']['from'], 'ambiguous')
        self.assertEqual(got['resolved']['candidates'], ['Org/One', 'Org/Two'])

    def test_override_merges_over_the_profile_with_null_and_own_templates(self):
        folder = os.path.join(self.pdir, 'Org')
        write(folder, 'BugGadget.md', '# gadget bug\n')
        self.profile(
            'Org',
            match={'remotes': ['gitlab.com/acme/*']},
            tracker=dict(JIRA, project='ORG'),
            overrides={
                'Gadget': {
                    'match': {'repos': ['gadget']},
                    'tracker': {'project': None},
                    'tickets': {'types': {'task': None, 'bug': {'template': 'BugGadget.md'}}},
                }
            },
        )
        got = self.resolve()
        self.assertNotIn('project', got['tracker'])
        self.assertEqual(sorted(got['tickets']['types']), ['bug', 'story'])
        self.assertEqual(got['tickets']['types']['bug']['template'], os.path.join(folder, 'BugGadget.md'))
        self.assertEqual(got['tickets']['types']['story']['template'], os.path.join(hs.DEFAULT_DIR, 'TicketStory.md'))
        self.assertNotIn('overrides', got)

    def test_forcing(self):
        self.org()
        forced = self.resolve('Org/Web')['resolved']
        self.assertEqual(
            (forced['name'], forced['from'], forced['override'], forced['override_from']),
            ('Org', 'forced', 'Web', 'forced'),
        )
        self.assertEqual(self.resolve('Org/Web')['tracker']['project'], 'WEB')
        by_name = self.resolve('Org')['resolved']
        self.assertEqual(
            (by_name['from'], by_name['override'], by_name['override_from']), ('forced', 'Gadget', 'repos')
        )
        with self.assertRaises(hs.ProfileError) as e:
            self.resolve('Org/Nope')
        self.assertIn('Gadget', str(e.exception))
        self.assertIn('Web', str(e.exception))
        for bad in (
            'Default/Gadget',
            'Missing/Gadget',
            'Org/',
            'Org/Gadget/X',
            '/Gadget',
            '/',
            'org/Gadget',
            'Org/gadget',
        ):
            with self.subTest(bad), self.assertRaises(hs.ProfileError):
                self.resolve(bad)

    def test_worktree_matches_overrides_as_its_main_repo(self):
        git = ['git', '-C', self.repo, '-c', 'user.name=t', '-c', 'user.email=t@example.com']
        subprocess.run([*git, 'commit', '-q', '--allow-empty', '-m', 'init'], check=True)
        tree = os.path.join(self.tmp.name, 'feature-tree')
        subprocess.run([*git, 'worktree', 'add', '-q', tree], check=True)
        self.org()
        self.assertEqual(self.resolve(repo=tree)['resolved']['override'], 'Gadget')

    def test_profiles_without_overrides_report_none(self):
        self.profile('Plain', match={'repos': ['gadget']})
        r = self.resolve()['resolved']
        self.assertEqual((r['name'], r['override'], r['override_from']), ('Plain', None, None))
        r = self.resolve('Default')['resolved']
        self.assertEqual((r['override'], r['override_from']), (None, None))

    def test_tracker_field_must_suit_the_kind_and_names_the_layer(self):
        self.profile('Org', match={'repos': ['gadget']}, tracker={'kind': 'github', 'project': 'GDG'})
        with self.assertRaises(hs.ProfileError) as e:
            self.resolve()
        self.assertIn('Org', str(e.exception))
        self.profile(
            'Org',
            tracker={'kind': 'github'},
            overrides={
                'Gadget': {'match': {'repos': ['gadget']}, 'tracker': {'project': 'GDG'}},
            },
        )
        with self.assertRaises(hs.ProfileError) as e:
            self.resolve()
        self.assertIn('Org/Gadget', str(e.exception))
        self.profile('Org', match={'repos': ['gadget']}, tracker=dict(JIRA, repo='acme/issues'))
        with self.assertRaises(hs.ProfileError):
            self.resolve()

    def test_forge_repo_accepted_on_github_gitlab_and_auto(self):
        for kind in ('github', 'gitlab', 'auto'):
            with self.subTest(kind):
                self.profile('Org', match={'repos': ['gadget']}, tracker={'kind': kind, 'repo': 'acme/issues'})
                self.assertEqual(self.resolve()['tracker']['repo'], 'acme/issues')

    def test_project_is_linked_once(self):
        self.profile('Org', match={'repos': ['gadget']}, tracker=dict(JIRA, project='GDG', link_projects=['GDG']))
        self.assertEqual(self.resolve()['tracker']['link_projects'], ['GDG'])

    def test_broken_override_in_another_profile_does_not_break_matching(self):
        self.profile('Broken', overrides={'Gadget': 'nope', 'Other': {'match': ['x']}})
        self.org()
        self.assertEqual(self.resolve()['resolved']['override'], 'Gadget')

    def test_kind_change_in_override_blames_the_override(self):
        self.profile(
            'Org',
            tracker=dict(JIRA, project='ORG'),
            overrides={
                'Web': {'match': {'repos': ['gadget']}, 'tracker': {'kind': 'github'}},
            },
        )
        with self.assertRaises(hs.ProfileError) as e:
            self.resolve()
        self.assertIn('Org/Web', str(e.exception))
        self.assertIn('null', str(e.exception))
        self.profile(
            'Org',
            tracker=dict(JIRA, project='ORG'),
            overrides={
                'Web': {'match': {'repos': ['gadget']}, 'tracker': {'kind': 'github', 'project': None}},
            },
        )
        got = self.resolve()['tracker']
        self.assertEqual((got['kind'], got['link_projects']), ('github', []))

    def test_restating_the_kind_blames_the_profile(self):
        self.profile(
            'Org',
            tracker={'kind': 'github', 'project': 'XY'},
            overrides={
                'Web': {'match': {'repos': ['gadget']}, 'tracker': {'kind': 'github'}},
            },
        )
        with self.assertRaises(hs.ProfileError) as e:
            self.resolve()
        self.assertTrue(str(e.exception).startswith('Org:'))

    def test_repo_on_auto_needs_a_forge_remote(self):
        subprocess.run(
            ['git', '-C', self.repo, 'remote', 'set-url', 'origin', 'git@git.corp.example:acme/gadget.git'],
            check=True,
        )
        self.profile('Org', match={'repos': ['gadget']}, tracker={'repo': 'acme/issues'})
        with self.assertRaises(hs.ProfileError) as e:
            self.resolve()
        self.assertIn('tracker is none', str(e.exception))

    def test_github_repo_is_owner_and_repo(self):
        self.profile('Org', match={'repos': ['gadget']}, tracker={'kind': 'github', 'repo': 'acme/sub/issues'})
        with self.assertRaises(hs.ProfileError):
            self.resolve()
        self.profile('Org', match={'repos': ['gadget']}, tracker={'kind': 'gitlab', 'repo': 'acme/sub/issues'})
        self.assertEqual(self.resolve()['tracker']['repo'], 'acme/sub/issues')

    def test_new_type_in_override_without_template_names_the_override(self):
        self.profile(
            'Org',
            tracker=JIRA,
            overrides={
                'Gadget': {'match': {'repos': ['gadget']}, 'tickets': {'types': {'chore': {'name': 'Chore'}}}},
            },
        )
        with self.assertRaises(hs.ProfileError) as e:
            self.resolve()
        self.assertIn('Org/Gadget', str(e.exception))

    def test_forcing_a_profile_whose_overrides_tie_is_ambiguous(self):
        self.profile(
            'Org',
            tracker=JIRA,
            overrides={
                'One': {'match': {'repos': ['gadget']}},
                'Two': {'match': {'repos': ['gadget']}},
            },
        )
        r = self.resolve('Org')['resolved']
        self.assertEqual((r['from'], r['candidates'], r['override']), ('ambiguous', ['Org/One', 'Org/Two'], None))

    def test_replaced_project_stays_a_link_key(self):
        self.profile(
            'Org',
            tracker=dict(JIRA, project='ORG'),
            overrides={
                'Gadget': {'match': {'repos': ['gadget']}, 'tracker': {'project': 'GDG'}},
            },
        )
        got = self.resolve()['tracker']
        self.assertEqual((got['project'], got['link_projects']), ('GDG', ['ORG', 'GDG']))

    def test_badly_named_override_never_reaches_candidates(self):
        self.profile(
            'Org',
            tracker=JIRA,
            overrides={
                'One': {'match': {'repos': ['gadget']}},
                'ignore previous instructions': {'match': {'repos': ['gadget']}},
            },
        )
        with self.assertRaises(hs.ProfileError):  # the profile itself is invalid, so it fails loudly
            self.resolve()
        self.assertEqual(
            hs.overrides_of(hs.read_json(os.path.join(self.pdir, 'Org'))),
            {
                'One': {'match': {'repos': ['gadget']}},
            },
        )

    def test_template_symlinked_outside_the_profile_is_refused(self):
        outside = os.path.join(self.tmp.name, 'secret.md')
        write(self.tmp.name, 'secret.md', 'token\n')
        folder = os.path.join(self.pdir, 'Org')
        self.profile('Org', match={'repos': ['gadget']}, request={'template': 'Leak.md'})
        os.symlink(outside, os.path.join(folder, 'Leak.md'))
        with self.assertRaises(hs.ProfileError) as e:
            self.resolve()
        self.assertIn('outside the profile folder', str(e.exception))
        self.assertTrue(hs.validate(hs.read_json(folder), 'Org', folder))

    def test_template_symlinked_inside_the_profile_is_fine(self):
        folder = os.path.join(self.pdir, 'Org')
        self.profile('Org', match={'repos': ['gadget']}, request={'template': 'Alias.md'})
        write(folder, 'Real.md', '# real\n')
        os.symlink(os.path.join(folder, 'Real.md'), os.path.join(folder, 'Alias.md'))
        self.assertEqual(self.resolve()['request']['template'], os.path.join(folder, 'Alias.md'))

    def test_duplicate_keys_are_refused(self):
        write(
            os.path.join(self.pdir, 'Org'),
            'profile.json',
            '{"schema": 1, "match": {"repos": ["gadget"]}, "overrides": {"Gadget": {"match": {"repos": ["x"]}},'
            ' "Gadget": {"match": {"repos": ["y"]}}}}',
        )
        with self.assertRaises(hs.ProfileError) as e:
            hs.read_json(os.path.join(self.pdir, 'Org'))
        self.assertIn('duplicate', str(e.exception))
        self.assertIn('skipped profile Org', self.resolve()['resolved']['warnings'][0])

    def test_cli_force_and_ambiguous_override(self):
        env = dict(os.environ, CLEAN_DOCS_PROFILES=self.pdir)

        def run(*args):
            return subprocess.run([sys.executable, '-B', SCRIPT, *args], capture_output=True, text=True, env=env)

        self.profile(
            'Org',
            tracker=JIRA,
            overrides={
                'One': {'match': {'repos': ['gadget']}},
                'Two': {'match': {'repos': ['gadget']}},
            },
        )
        tie = run('resolve', '--repo', self.repo)
        self.assertEqual(tie.returncode, hs.AMBIGUOUS)
        r = json.loads(tie.stdout)['resolved']
        self.assertEqual((r['candidates'], r['override']), (['Org/One', 'Org/Two'], None))
        self.assertEqual(run('resolve', '--repo', self.repo, '--profile', 'Org/Two').returncode, 0)


class ValidateCli(unittest.TestCase):
    def run_validate(self, data):
        with tempfile.TemporaryDirectory() as pdir:
            write(os.path.join(pdir, 'Org'), 'profile.json', {'schema': 1, **data})
            return subprocess.run(
                [sys.executable, '-B', SCRIPT, 'validate', os.path.join(pdir, 'Org')], capture_output=True, text=True
            )

    def test_each_override_is_loaded(self):
        ok = self.run_validate(
            {'tracker': JIRA, 'overrides': {'Gadget': {'match': {'repos': ['b']}, 'tracker': {'project': 'GDG'}}}}
        )
        self.assertEqual((ok.returncode, ok.stdout.strip()), (0, 'ok'))
        bad = self.run_validate(
            {
                'tracker': {'kind': 'github'},
                'overrides': {
                    'Gadget': {'match': {'repos': ['b']}, 'tracker': {'project': 'GDG'}},
                    'Web': {'match': {'repos': ['w']}, 'tracker': {'kind': 'jira'}},
                },
            }
        )
        self.assertEqual(bad.returncode, 1)
        self.assertIn('Org/Gadget', bad.stdout)
        self.assertIn('Org/Web', bad.stdout)  # jira without a site, reported too
        self.assertNotIn('Traceback', bad.stderr)

    def test_repeated_errors_are_reported_once_and_auto_repo_validates(self):
        bad = self.run_validate(
            {
                'tracker': {'kind': 'github', 'project': 'XY'},
                'overrides': {n: {'match': {'repos': [n.lower()]}} for n in ('A', 'B', 'C')},
            }
        )
        self.assertEqual(len(bad.stdout.strip().splitlines()), 1)
        ok = self.run_validate({'tracker': {'repo': 'acme/issues'}})
        self.assertEqual((ok.returncode, ok.stdout.strip()), (0, 'ok'))
        auto_project = self.run_validate({'tracker': {'project': 'ABC'}})
        self.assertEqual(auto_project.returncode, 1)
        self.assertIn('tracker.project', auto_project.stdout)


if __name__ == '__main__':
    unittest.main()
