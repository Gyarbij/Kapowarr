import unittest
from unittest.mock import patch

from flask import Flask

from backend.internals.server import Server
from frontend.ui import render, ui


class ThemeRenderTest(unittest.TestCase):
    def _rendered_theme(self, cookie=None):
        app = Flask(__name__)
        headers = {'Cookie': f'theme={cookie}'} if cookie is not None else {}
        with app.test_request_context(headers=headers), patch(
            'frontend.ui.render_template',
            return_value=''
        ) as render_template:
            render('volumes.html')

        return render_template.call_args.kwargs['theme']

    def test_dark_theme_cookie_is_rendered(self):
        self.assertEqual(self._rendered_theme('dark-mode'), 'dark-mode')

    def test_missing_cookie_renders_default_theme(self):
        self.assertEqual(self._rendered_theme(), '')

    def test_unknown_theme_cookie_is_ignored(self):
        self.assertEqual(self._rendered_theme('injected-class'), '')


class ReleasesRouteTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(ui)

    def test_releases_renders_weekly_packs_by_default(self):
        with patch('frontend.ui.render', return_value='weekly') as render:
            response = self.app.test_client().get('/releases')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), 'weekly')
        render.assert_called_once_with('weekly_packs.html')

    def test_calendar_remains_available(self):
        with patch('frontend.ui.render', return_value='calendar') as render:
            response = self.app.test_client().get('/releases/calendar')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), 'calendar')
        render.assert_called_once_with('releases.html')

    def test_task_settings_route_renders_task_settings(self):
        with patch('frontend.ui.render', return_value='task-settings') as render:
            response = self.app.test_client().get('/settings/tasks')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), 'task-settings')
        render.assert_called_once_with('settings_tasks.html')

    def test_legacy_weekly_route_redirects_with_safe_filters(self):
        with patch.object(Server, 'url_base', '/kapowarr'):
            response = self.app.test_client().get(
                '/weekly-packs?weeks=12&publisher=dc&unknown=discarded'
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers['Location'],
            '/kapowarr/releases?weeks=12&publisher=dc'
        )


if __name__ == '__main__':
    unittest.main()
