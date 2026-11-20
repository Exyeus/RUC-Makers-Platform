"""Appearance bootstrap runs before CSS and its native control stays accessible."""

import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from tests.hackathon.test_workflows import endpoint


@pytest.mark.django_db
@pytest.mark.parametrize(
    'route,authenticated', [('home', False), ('student-signup', False), ('home', True), ('teams', True)]
)
def test_theme_bootstraps_before_styles_and_swiss_layer_loads_last(client, hackathon, user, route, authenticated):
    if authenticated:
        client.force_login(user)
    response = client.get(endpoint(hackathon, route))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    bootstrap = soup.select_one('head script[src*="hackathon/theme-init.js"]')
    assert bootstrap is not None
    assert not bootstrap.has_attr('defer') and not bootstrap.has_attr('async')
    assert bootstrap.get('type') != 'module'
    head = str(soup.head)
    assert head.index(str(bootstrap)) < head.index(str(soup.select_one('head link[rel="stylesheet"]')))
    stylesheets = [link.get('href', '') for link in soup.select('head link[rel="stylesheet"]')]
    assert 'hackathon/swiss.css' in stylesheets[-1]
    # The Swiss system is light only; there is no appearance switch to show.
    assert soup.select_one('[data-ui-theme]') is None


def test_appearance_runtime_regressions():
    node = shutil.which('node')
    if not node:
        pytest.skip('Appearance runtime checks require Node.js.')
    script = Path(__file__).with_name('theme_init_regression.cjs')
    result = subprocess.run([node, '--test', str(script)], capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
