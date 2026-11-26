"""Profile resolution, merging and validation (scripts/house_style.py)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'scripts', 'house_style.py')
sys.path.insert(0, os.path.dirname(SCRIPT))

import house_style as hs  # noqa: E402


def write(folder, name, data):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), 'w', encoding='utf-8') as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


class Remotes(unittest.TestCase):
    def test_forms_normalize_to_host_and_path(self):
        for url in (
            'git@github.com:acme/widget.git',
            'https://github.com/acme/widget.git',
            'https://github.com/acme/widget',
            'ssh://git@GitHub.com:22/acme/widget.git',
            'https://user:token@github.com/acme/widget.git',  # pragma: allowlist secret
        ):
            with self.subTest(url):
                self.assertEqual(hs.normalize_remote(url), 'github.com/acme/widget')

    def test_nested_groups_kept(self):
        got = hs.normalize_remote('git@gitlab.example.com:team/sub/app.git')
        self.assertEqual(got, 'gitlab.example.com/team/sub/app')

    def test_credentials_and_scheme_case_never_leak(self):
        creds = (
            'HTTPS://user:tok@GitHub.com/acme/widget.git',  # pragma: allowlist secret
            'Https://tok@github.com/acme/widget',
        )
        for url in creds:
            with self.subTest(url):
                got = hs.normalize_remote(url)
                self.assertEqual(got, 'github.com/acme/widget')
                self.assertNotIn('tok', got)

    def test_unrecognized_remote_is_none(self):
        for url in ('', 'not a url', '/local/path/repo.git', 'file:///tmp/repo'):
            with self.subTest(url):
                self.assertIsNone(hs.normalize_remote(url))

    def test_tracker_inferred_from_host(self):
        self.assertEqual(hs.infer_tracker('github.com/a/b'), 'github')
        self.assertEqual(hs.infer_tracker('gitlab.example.com/a/b'), 'gitlab')
        self.assertEqual(hs.infer_tracker('example.com/a/b'), 'none')
        self.assertEqual(hs.infer_tracker(None), 'none')


class Resolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pdir = os.path.join(self.tmp.name, 'profiles')
        self.repo = os.path.join(self.tmp.name, 'widget')
        os.makedirs(self.repo)
        subprocess.run(['git', 'init', '-q', self.repo], check=True)
        subprocess.run(
            ['git', '-C', self.repo, 'remote', 'add', 'origin', 'git@github.com:acme/widget.git'], check=True
        )
        self.env = {'CLEAN_DOCS_PROFILES': self.pdir}

    def profile(self, name, **data):
        write(os.path.join(self.pdir, name), 'profile.json', {'schema': 1, **data})

    def resolve(self, force=None):
        return hs.resolve(self.repo, force, self.env)

    def test_no_profiles_falls_back_to_default(self):
        got = self.resolve()
        self.assertEqual((got['resolved']['name'], got['resolved']['from']), ('Default', 'default'))
        self.assertEqual(got['tracker']['kind'], 'github')
        self.assertTrue(os.path.isabs(got['request']['template']))

    def test_remote_glob_matches(self):
        self.profile('Acme', match={'remotes': ['github.com/acme/*']})
        got = self.resolve()
        self.assertEqual((got['resolved']['name'], got['resolved']['from']), ('Acme', 'remote'))

    def test_longer_glob_wins(self):
        self.profile('Broad', match={'remotes': ['github.com/*']})
        self.profile('Narrow', match={'remotes': ['github.com/acme/widget']})
        self.assertEqual(self.resolve()['resolved']['name'], 'Narrow')

    def test_repo_name_beats_any_glob(self):
        self.profile('Narrow', match={'remotes': ['github.com/acme/widget']})
        self.profile('Named', match={'repos': ['widget']})
        self.assertEqual(self.resolve()['resolved']['name'], 'Named')

    def test_tie_is_ambiguous(self):
        self.profile('One', match={'remotes': ['github.com/acme/*']})
        self.profile('Two', match={'remotes': ['github.com/acme/*']})
        got = self.resolve()
        self.assertEqual(got['resolved']['from'], 'ambiguous')
        self.assertEqual(got['resolved']['candidates'], ['One', 'Two'])

    def test_malformed_match_in_another_profile_does_not_break_matching(self):
        self.profile('Broken', match=['not', 'an', 'object'])
        self.profile('Acme', match={'remotes': ['github.com/acme/*']})
        self.assertEqual(self.resolve()['resolved']['name'], 'Acme')

    def test_force_picks_by_name_and_rejects_unknown(self):
        self.profile('Acme')
        self.assertEqual(self.resolve('Acme')['resolved']['from'], 'forced')
        self.assertEqual(self.resolve('Default')['resolved']['name'], 'Default')
        with self.assertRaises(hs.ProfileError):
            self.resolve('Missing')

    def test_non_pascal_folders_and_a_user_default_are_ignored(self):
        self.profile('lower', match={'repos': ['widget']})
        self.profile('Default', match={'repos': ['widget']})
        self.assertEqual(self.resolve()['resolved']['name'], 'Default')
        self.assertEqual(self.resolve()['resolved']['dir'], hs.DEFAULT_DIR)

    def test_overlay_merges_removes_and_prefers_own_templates(self):
        folder = os.path.join(self.pdir, 'Acme')
        write(folder, 'PullRequest.md', '# own\n')
        self.profile(
            'Acme',
            match={'repos': ['widget']},
            tracker={'kind': 'jira', 'site': 'https://example.atlassian.net'},
            tickets={'types': {'task': None}},
        )
        got = self.resolve()
        self.assertEqual(sorted(got['tickets']['types']), ['bug', 'story'])
        self.assertEqual(got['request']['template'], os.path.join(folder, 'PullRequest.md'))
        self.assertEqual(got['tickets']['urgency'], os.path.join(hs.DEFAULT_DIR, 'Urgency.md'))
        self.assertEqual(got['tracker']['link_projects'], [])

    def test_skill_reference_passes_through(self):
        self.profile('Acme', match={'repos': ['widget']}, request={'template': 'skill:some-plugin:pr'})
        self.assertEqual(self.resolve()['request']['template'], 'skill:some-plugin:pr')

    def test_jira_needs_a_site(self):
        self.profile('Acme', match={'repos': ['widget']}, tracker={'kind': 'jira'})
        with self.assertRaises(hs.ProfileError):
            self.resolve()

    def test_new_type_needs_name_and_template(self):
        self.profile('Acme', match={'repos': ['widget']}, tickets={'types': {'spike': {'urgency': False}}})
        with self.assertRaises(hs.ProfileError):
            self.resolve()

    def test_config_file_names_the_profiles_dir(self):
        self.profile('Acme', match={'repos': ['widget']})
        write(os.path.join(self.tmp.name, 'clean-docs'), 'config.json', {'profiles_dir': self.pdir})
        got = hs.resolve(self.repo, None, {'XDG_CONFIG_HOME': self.tmp.name})
        self.assertEqual(got['resolved']['name'], 'Acme')

    def test_bad_config_file_rejected(self):
        write(os.path.join(self.tmp.name, 'clean-docs'), 'config.json', {'profiles_dir': ''})
        with self.assertRaises(hs.ProfileError):
            hs.profiles_dir({'XDG_CONFIG_HOME': self.tmp.name})

    def test_cli_exit_codes(self):
        env = dict(os.environ, CLEAN_DOCS_PROFILES=self.pdir)

        def run(*args):
            return subprocess.run([sys.executable, '-B', SCRIPT, *args], capture_output=True, text=True, env=env)

        self.assertEqual(run('resolve', '--repo', self.repo).returncode, 0)
        self.profile('One', match={'remotes': ['github.com/acme/*']})
        self.profile('Two', match={'remotes': ['github.com/acme/*']})
        self.assertEqual(run('resolve', '--repo', self.repo).returncode, hs.AMBIGUOUS)
        self.assertEqual(run('resolve', '--repo', self.repo, '--profile', 'One').returncode, 0)
        self.assertEqual(run('validate', os.path.join(self.pdir, 'One')).stdout.strip(), 'ok')
        self.profile('Bad', unknown=1)
        bad = run('validate', os.path.join(self.pdir, 'Bad'))
        self.assertEqual(bad.returncode, 1)
        self.assertIn('unknown key "unknown"', bad.stdout)


class Validate(unittest.TestCase):
    def errors(self, data, files=()):
        with tempfile.TemporaryDirectory() as folder:
            for name in files:
                write(folder, name, '# t\n')
            return hs.validate({'schema': 1, **data}, 'X/profile.json', folder)

    def test_default_profile_is_valid(self):
        self.assertEqual(hs.validate(hs.read_json(hs.DEFAULT_DIR), 'Default', hs.DEFAULT_DIR), [])

    def test_rejections(self):
        cases = {
            'schema': {'schema': 2},
            'unknown key': {'color': 'red'},
            'unknown nested key': {'tracker': {'kind': 'jira', 'token': 'x'}},
            'bad kind': {'tracker': {'kind': 'trello'}},
            'insecure site': {'tracker': {'site': 'http://example.com'}},
            'ticket key as project': {'tracker': {'link_projects': ['ABC-1']}},
            'path traversal': {'request': {'template': '../../secret.md'}},
            'missing template': {'request': {'template': 'Nope.md'}},
            'bad skill ref': {'request': {'template': 'skill:Bad Ref'}},
            'draft not bool': {'request': {'draft': 'yes'}},
            'type key': {'tickets': {'types': {'Story': {'name': 'Story'}}}},
            'urgency not bool': {'tickets': {'types': {'story': {'urgency': 'yes'}}}},
            'summary limit': {'tickets': {'max_summary': 5}},
            'match not list': {'match': {'remotes': 'github.com/*'}},
            'empty transition': {'on_ship': {'transition': ' '}},
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertTrue(self.errors(data), label)

    def test_own_template_and_skill_ref_accepted(self):
        self.assertEqual(self.errors({'request': {'template': 'Mine.md'}}, files=['Mine.md']), [])
        self.assertEqual(self.errors({'request': {'template': 'skill:mattpocock-skills:pr'}}), [])
        self.assertEqual(self.errors({'on_ship': {'comment': None, 'transition': 'In Review'}}), [])


class Hardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pdir = os.path.join(self.tmp.name, 'profiles')
        self.repo = os.path.join(self.tmp.name, 'Widget')
        os.makedirs(self.pdir)
        os.makedirs(self.repo)
        git = ['git', '-C', self.repo]
        subprocess.run([*git, 'init', '-q'], check=True)
        subprocess.run([*git, 'remote', 'add', 'origin', 'git@GitHub.com:Acme/Widget.git'], check=True)
        self.env = {'CLEAN_DOCS_PROFILES': self.pdir}

    def profile(self, name, **data):
        write(os.path.join(self.pdir, name), 'profile.json', {'schema': 1, **data})

    def test_matching_ignores_case(self):
        self.profile('Acme', match={'remotes': ['github.com/acme/*']})
        self.assertEqual(hs.resolve(self.repo, None, self.env)['resolved']['name'], 'Acme')
        os.remove(os.path.join(self.pdir, 'Acme', 'profile.json'))
        self.profile('Named', match={'repos': ['widget']})
        self.assertEqual(hs.resolve(self.repo, None, self.env)['resolved']['from'], 'repos')

    def test_broken_profile_is_skipped_with_a_warning(self):
        write(os.path.join(self.pdir, 'Broken'), 'profile.json', '{ not json')
        self.profile('Acme', match={'remotes': ['github.com/acme/*']})
        got = hs.resolve(self.repo, None, self.env)
        self.assertEqual(got['resolved']['name'], 'Acme')
        self.assertTrue(any('Broken' in w for w in got['resolved']['warnings']))

    def test_user_default_folder_is_ignored_with_a_warning(self):
        self.profile('Default', match={'repos': ['widget']})
        got = hs.resolve(self.repo, None, self.env)
        self.assertEqual(got['resolved']['dir'], hs.DEFAULT_DIR)
        self.assertTrue(any('Default is the built-in profile' in w for w in got['resolved']['warnings']))

    def test_missing_profiles_dir_warns(self):
        got = hs.resolve(self.repo, None, {'CLEAN_DOCS_PROFILES': os.path.join(self.tmp.name, 'nope')})
        self.assertEqual(got['resolved']['name'], 'Default')
        self.assertTrue(any('does not exist' in w for w in got['resolved']['warnings']))

    def test_corrupt_config_is_a_profile_error(self):
        write(os.path.join(self.tmp.name, 'clean-docs'), 'config.json', '{')
        with self.assertRaises(hs.ProfileError):
            hs.profiles_dir({'XDG_CONFIG_HOME': self.tmp.name})

    def test_git_problems_are_warnings_not_silence(self):
        plain = os.path.join(self.tmp.name, 'plain')
        os.makedirs(plain)
        got = hs.resolve(plain, None, self.env)
        self.assertIsNone(got['resolved']['remote'])
        self.assertTrue(any('not matched as a git repo' in w for w in got['resolved']['warnings']))
        subprocess.run(['git', '-C', self.repo, 'remote', 'remove', 'origin'], check=True)
        got = hs.resolve(self.repo, None, self.env)
        self.assertTrue(any('no origin remote' in w for w in got['resolved']['warnings']))

    def test_worktree_matches_as_its_main_repo(self):
        git = ['git', '-C', self.repo, '-c', 'user.name=t', '-c', 'user.email=t@example.com']
        subprocess.run([*git, 'commit', '-q', '--allow-empty', '-m', 'init'], check=True)
        tree = os.path.join(self.tmp.name, 'feature-tree')
        subprocess.run([*git, 'worktree', 'add', '-q', tree], check=True)
        self.profile('Named', match={'repos': ['Widget']})
        self.assertEqual(hs.resolve(tree, None, self.env)['resolved']['name'], 'Named')

    def test_cli_turns_profile_problems_into_messages(self):
        env = dict(os.environ, XDG_CONFIG_HOME=self.tmp.name)
        env.pop('CLEAN_DOCS_PROFILES', None)
        write(os.path.join(self.tmp.name, 'clean-docs'), 'config.json', '{')
        proc = subprocess.run(
            [sys.executable, '-B', SCRIPT, 'resolve', '--repo', self.repo], capture_output=True, text=True, env=env
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn('profile error', proc.stderr)
        self.assertNotIn('Traceback', proc.stderr)


class Validation(unittest.TestCase):
    def errors(self, data):
        with tempfile.TemporaryDirectory() as folder:
            return hs.validate({'schema': 1, **data}, 'X/profile.json', folder)

    def test_null_only_where_it_removes_something(self):
        self.assertEqual(self.errors({'tickets': {'types': {'task': None}, 'fields': None, 'urgency': None}}), [])
        self.assertEqual(self.errors({'on_ship': {'comment': None, 'transition': None}}), [])
        for data in (
            {'tracker': {'site': None}},
            {'tracker': {'kind': None}},
            {'tickets': {'max_summary': None}},
            {'tickets': {'types': None}},
            {'match': None},
        ):
            with self.subTest(data):
                errors = self.errors(data)
                self.assertTrue(errors)
                self.assertTrue(any("can't be null" in e for e in errors))

    def test_types_must_be_an_object(self):
        for types in ([], 'story'):
            with self.subTest(types):
                self.assertTrue(self.errors({'tickets': {'types': types}}))

    def test_schema_must_be_the_integer_one(self):
        for schema in (True, 1.0, '1'):
            with self.subTest(schema):
                self.assertTrue(hs.validate({'schema': schema}, 'X', hs.DEFAULT_DIR))

    def test_site_may_have_a_port_and_path(self):
        self.assertEqual(self.errors({'tracker': {'kind': 'jira', 'site': 'https://jira.example.com:8443/jira'}}), [])
        self.assertTrue(self.errors({'tracker': {'site': 'https://jira.example.com/a b'}}))

    def test_labels_cannot_carry_shell_characters(self):
        self.assertEqual(
            self.errors({'tickets': {'types': {'story': {'labels': ['good first issue', 'type: bug']}}}}), []
        )
        for label in ('$(id)', '`x`', 'a"b', 'a\\b', 'a\nb', ''):
            with self.subTest(label):
                self.assertTrue(self.errors({'tickets': {'types': {'story': {'labels': [label]}}}}))


if __name__ == '__main__':
    unittest.main()
