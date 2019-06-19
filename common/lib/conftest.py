"""
Code run by pylint before running any tests.
"""
from __future__ import absolute_import

from setproctitle import getproctitle, setproctitle

# Patch the xml libs before anything else.
from safe_lxml import defuse_xml_libs
defuse_xml_libs()

import pytest


@pytest.fixture(autouse=True)
def no_webpack_loader(monkeypatch):
    monkeypatch.setattr(
        "webpack_loader.templatetags.webpack_loader.render_bundle",
        lambda entry, extension=None, config='DEFAULT', attrs='': ''
    )
    monkeypatch.setattr(
        "webpack_loader.utils.get_as_tags",
        lambda entry, extension=None, config='DEFAULT', attrs='': []
    )
    monkeypatch.setattr(
        "webpack_loader.utils.get_files",
        lambda entry, extension=None, config='DEFAULT', attrs='': []
    )


def pytest_configure(config):
    """
    Rename the process for pytest-xdist workers for easier identification.
    """
    if hasattr(config, 'workerinput'):
        # Set the process name for pytest-xdist workers to something
        # recognizable, like "py_xdist_gw0", for the benefit of tools like
        # New Relic Infrastructure and top
        old_name = getproctitle()
        new_name = 'py_xdist_{}'.format(config.workerinput['workerid'])
        setproctitle(old_name.replace('python', new_name, 1))
