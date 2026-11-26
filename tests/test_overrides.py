"""Per-repo overrides inside a profile, and the tracker.project / tracker.repo fields."""

import tempfile
import unittest

from test_house_style import hs, write


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
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertTrue(self.errors(data), label)

    def test_override_errors_name_the_override(self):
        errors = self.errors({'overrides': {'Gadget': {'match': {'repos': ['b']}, 'tracker': {'kind': 'trello'}}}})
        self.assertTrue(errors)
        self.assertIn('overrides.Gadget', errors[0])

    def test_tracker_project_and_repo_shapes(self):
        self.assertEqual(self.errors({'tracker': {'project': 'GDG'}}), [])
        self.assertEqual(self.errors({'tracker': {'repo': 'acme/widget'}}), [])
        self.assertEqual(self.errors({'tracker': {'repo': 'group/sub.group/my_repo-2'}}), [])
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
        ):
            with self.subTest(tracker):
                self.assertTrue(self.errors({'tracker': tracker}))


if __name__ == '__main__':
    unittest.main()
